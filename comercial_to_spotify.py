#!/usr/bin/env python3
"""
Scrapa o TNT Top 20 da Rádio Comercial em https://radiocomercial.pt/programas/tnt-todos-no-top
e atualiza a playlist Spotify indicada.

Nota API Spotify (Fev 2026):
  - GET/POST/DELETE usam /playlists/{id}/items (o antigo /tracks foi removido)
  - PUT /playlists/{id}/items com {"uris": [...]} substitui todos os items de uma vez (max 100)
  - GET /search tem limit máximo de 10
"""

import os
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_ME_URL         = "https://api.spotify.com/v1/me"
COMERCIAL_URL          = "https://radiocomercial.pt/programas/tnt-todos-no-top"


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
        print(f"  Response body: {resp.text[:500]}")
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


def scrape_comercial_tnt() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; comercial-tnt-spotify-bot/1.0)"}
    resp = requests.get(COMERCIAL_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tracks = []

    # Estratégia A: seletores de chart explícitos
    for item in soup.select(".chart-list__item, .tnt-item, .top-item, .music-item"):
        pos_el    = item.select_one(".position, .pos, .rank, .number")
        artist_el = item.select_one(".artist, .singer, .band")
        title_el  = item.select_one(".title, .song, .track-name, .music-title")
        if pos_el and artist_el and title_el:
            pos_text = pos_el.get_text(strip=True).replace(".", "").replace("º", "")
            if pos_text.isdigit():
                tracks.append({
                    "position": int(pos_text),
                    "artist":   artist_el.get_text(strip=True),
                    "title":    title_el.get_text(strip=True),
                })

    # Estratégia B: tabela com linhas numeradas
    if not tracks:
        for row in soup.select("table tr, .ranking tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 3:
                pos_text = cells[0].get_text(strip=True).replace(".", "").replace("º", "")
                if pos_text.isdigit():
                    tracks.append({
                        "position": int(pos_text),
                        "artist":   cells[1].get_text(strip=True),
                        "title":    cells[2].get_text(strip=True),
                    })

    # Estratégia C: listas ordenadas com padrão "Artista - Título"
    if not tracks:
        for ol in soup.select("ol"):
            for idx, li in enumerate(ol.find_all("li"), start=1):
                text = li.get_text(separator=" | ", strip=True)
                if " - " in text or " — " in text:
                    sep = " — " if " — " in text else " - "
                    parts = text.split(sep, 1)
                    tracks.append({
                        "position": idx,
                        "artist":   parts[0].strip(),
                        "title":    parts[1].strip(),
                    })

    # Estratégia D: data-attributes
    if not tracks:
        for item in soup.select("[data-position], [data-rank]"):
            pos = item.get("data-position") or item.get("data-rank", "")
            art = item.get("data-artist", "") or ""
            tit = item.get("data-title", "") or item.get("data-song", "") or ""
            if pos.isdigit() and art and tit:
                tracks.append({"position": int(pos), "artist": art, "title": tit})

    return sorted(tracks, key=lambda x: x["position"])


def search_spotify(token: str, artist: str, title: str) -> str | None:
    params = {"q": f"track:{title} artist:{artist}", "type": "track", "limit": 10, "market": "PT"}
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    if items:
        return items[0]["uri"]
    params["q"] = f"{artist} {title}"
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def replace_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    spotify_request("PUT", url, token, json={"uris": uris[:100]})
    for i in range(100, len(uris), 100):
        spotify_request("POST", url, token, json={"uris": uris[i:i + 100], "position": i})
        time.sleep(0.2)


def main() -> None:
    print("=== Rádio Comercial TNT Top 20 → Spotify ===")

    print("\nA scraper o TNT Top 20...")
    tracks = scrape_comercial_tnt()
    if not tracks:
        print("AVISO: Nenhum track encontrado — a página pode ter mudado a estrutura HTML.")
        sys.exit(1)
    print(f"{len(tracks)} tracks encontrados:")
    for t in tracks:
        print(f"  {t['position']:2}. {t['artist']} — {t['title']}")

    print("\nA obter access token Spotify...")
    token = get_access_token()
    print("Token obtido com sucesso.")

    me = spotify_request("GET", SPOTIFY_ME_URL, token).json()
    print(f"  Conta: {me.get('display_name')} ({me.get('id')})")

    print("\nA pesquisar tracks no Spotify...")
    uris, not_found = [], []
    for t in tracks:
        uri = search_spotify(token, t["artist"], t["title"])
        if uri:
            uris.append(uri)
            print(f"  ✓ {t['artist']} — {t['title']}")
        else:
            not_found.append(t)
            print(f"  ✗ {t['artist']} — {t['title']} (não encontrado)")
        time.sleep(0.1)

    if not uris:
        print("ERRO: Nenhum track encontrado no Spotify.")
        sys.exit(1)

    playlist_id = os.environ["SPOTIFY_COMERCIAL_PLAYLIST_ID"]
    print(f"\nA substituir playlist com {len(uris)} tracks...")
    replace_playlist(token, playlist_id, uris)
    print("Playlist atualizada com sucesso! ✓")

    now = datetime.datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
    summary = [
        "## 🎵 Rádio Comercial TNT Top 20 → Spotify",
        f"> Atualizado em **{now}** &nbsp;—&nbsp; [{playlist_id}]({playlist_url})",
        "",
        f"**{len(uris)}/{len(tracks)} tracks** adicionados com sucesso.",
        "",
        "| # | Artista | Música | Estado |",
        "|---|---|---|---|",
    ]
    for t in tracks:
        found = not any(nf["position"] == t["position"] for nf in not_found)
        estado = "✅" if found else "❌ não encontrado"
        summary.append(f"| {t['position']} | {t['artist']} | {t['title']} | {estado} |")
    if not_found:
        summary += ["", f"⚠️ {len(not_found)} track(s) não encontrados no Spotify."]
    write_summary(summary)


if __name__ == "__main__":
    main()
