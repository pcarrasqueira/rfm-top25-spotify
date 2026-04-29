#!/usr/bin/env python3
"""
Scrapa o Top 25 RFM em https://rfm.pt/top25rfm
e actualiza a playlist Spotify indicada.

Estrutura HTML da página (confirmada em Abril 2026):
  <ul>
    <li>1</li>          <- posição (número solto)
    <li>
      <ul>
        <li>Artista</li>
        <li>Título</li>
      </ul>
    </li>
    <li>2</li>
    ...
  </ul>

API Spotify:
  - PUT /playlists/{id}/items com {"uris": [...]} substitui todos os items (max 100)
  - GET /search limit máximo 10
"""

import os
import re
import sys
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
RFM_URL                = "https://rfm.pt/top25rfm"
PLAYLIST_ID            = os.environ.get("SPOTIFY_PLAYLIST_ID", "5Bgp9ddbbmwNkbzAFy5SSC")


def write_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def spotify_request(method: str, url: str, token: str, **kwargs) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if not resp.ok:
        print(f"  HTTP {resp.status_code} {method} {url}")
        print(f"  Body: {resp.text[:500]}")
        resp.raise_for_status()
    return resp


def get_access_token() -> str:
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"]},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=15,
    )
    if not resp.ok:
        print(f"  HTTP {resp.status_code} ao obter token: {resp.text[:300]}")
        resp.raise_for_status()
    data = resp.json()
    print(f"  Scopes: {data.get('scope', '(vazio)')}")
    return data["access_token"]


def scrape_rfm_top25() -> list[dict]:
    """
    Raspa o Top 25 do rfm.pt/top25rfm.

    A estrutura real da página é uma <ul> principal onde os filhos directos <li>
    alternam entre:
      - um <li> com texto numérico (a posição)
      - um <li> que contém um <ul> com dois <li>: artista e título

    Exemplo simplificado:
      <ul>
        <li>1</li>
        <li><ul><li>Miley Cyrus</li><li>Dream As One</li></ul></li>
        <li>2</li>
        <li><ul><li>Bandidos do Cante</li><li>Rosa</li></ul></li>
        ...
      </ul>
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; rfm-top25-spotify-bot/2.0)",
        "Accept-Language": "pt-PT,pt;q=0.9",
    }
    resp = requests.get(RFM_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tracks = []

    # Encontra todos os <li> que contêm um <ul> interno com exactamente 2 filhos <li>
    # (artista + título), e tenta fazer match com o número de posição que vem antes.
    all_top_level_li = []
    for ul in soup.find_all("ul"):
        children = [li for li in ul.find_all("li", recursive=False)]
        # Queremos a lista principal que tenha pelo menos 25 entradas e
        # cujos filhos sigam o padrão número / sub-lista
        numeric_children = sum(1 for li in children if re.match(r'^\s*\d+\s*$', li.get_text()))
        if numeric_children >= 10:
            all_top_level_li = children
            break

    pos = None
    for li in all_top_level_li:
        text = li.get_text(strip=True)
        # É um <li> de posição?
        if re.match(r'^\d+$', text):
            pos = int(text)
            continue
        # É um <li> com sub-lista artista/título?
        sub_ul = li.find("ul")
        if sub_ul and pos is not None:
            sub_items = sub_ul.find_all("li", recursive=False)
            if len(sub_items) >= 2:
                artist = sub_items[0].get_text(separator=" ", strip=True)
                title  = sub_items[1].get_text(separator=" ", strip=True)
                if artist and title:
                    tracks.append({"position": pos, "artist": artist, "title": title})
            pos = None

    # Fallback: se a estratégia acima falhar, tenta pares de <li> consecutivos
    # onde o primeiro é número e o segundo tem artista e título separados por texto
    if not tracks:
        print("  [WARN] Estratégia principal falhou, a tentar fallback por texto...")
        for ul in soup.find_all("ul"):
            items = ul.find_all("li", recursive=False)
            i = 0
            while i < len(items) - 1:
                pos_text = items[i].get_text(strip=True)
                if re.match(r'^\d+$', pos_text):
                    combined = items[i + 1].get_text(separator="|", strip=True)
                    parts = combined.split("|")
                    parts = [p.strip() for p in parts if p.strip()]
                    if len(parts) >= 2:
                        tracks.append({
                            "position": int(pos_text),
                            "artist":   parts[0],
                            "title":    parts[1],
                        })
                        i += 2
                        continue
                i += 1
            if len(tracks) >= 10:
                break

    return sorted(tracks, key=lambda x: x["position"])


def search_spotify(token: str, artist: str, title: str) -> str | None:
    """Pesquisa uma track no Spotify, tenta várias combinações."""
    queries = [
        f'track:"{title}" artist:"{artist}"',
        f'{title} {artist}',
        f'{title}',
    ]
    for q in queries:
        params = {"q": q, "type": "track", "limit": 5, "market": "PT"}
        resp = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
    return None


def update_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    # PUT substitui todos os items de uma vez (max 100)
    spotify_request("PUT", url, token, json={"uris": uris[:100]})


def main() -> None:
    print("=== RFM Top 25 → Spotify ===")

    print("\n[1/3] A raspar rfm.pt/top25rfm...")
    tracks = scrape_rfm_top25()

    if not tracks:
        print("ERRO: Nenhuma track encontrada. Verificar estrutura da página.")
        sys.exit(1)

    print(f"  {len(tracks)} tracks encontradas:")
    for t in tracks:
        print(f"  {t['position']:2d}. {t['artist']} — {t['title']}")

    print("\n[2/3] A pesquisar no Spotify...")
    token = get_access_token()
    uris = []
    found_log = []
    not_found = []

    for t in tracks:
        uri = search_spotify(token, t["artist"], t["title"])
        status = "✓" if uri else "✗"
        print(f"  {status} {t['position']:2d}. {t['artist']} — {t['title']}")
        if uri:
            uris.append(uri)
            found_log.append(f"| {t['position']} | {t['artist']} | {t['title']} | ✅ |")
        else:
            not_found.append(f"| {t['position']} | {t['artist']} | {t['title']} | ❌ |")

    print(f"\n  Encontradas: {len(uris)}/{len(tracks)}")

    if not uris:
        print("ERRO: Nenhuma track encontrada no Spotify.")
        sys.exit(1)

    print("\n[3/3] A actualizar playlist Spotify...")
    update_playlist(token, PLAYLIST_ID, uris)
    print(f"  Playlist {PLAYLIST_ID} actualizada com {len(uris)} tracks.")

    # GitHub Actions Step Summary
    summary = [
        "## RFM Top 25 → Spotify",
        f"",
        f"**{len(uris)}/{len(tracks)} tracks** adicionadas à playlist.",
        "",
        "| # | Artista | Título | Spotify |",
        "|---|---------|--------|---------|",
    ] + found_log + not_found
    write_summary(summary)


if __name__ == "__main__":
    main()
