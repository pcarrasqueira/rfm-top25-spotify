#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.

Nota API Spotify (Fev 2026):
  - GET/POST/DELETE usam /playlists/{id}/items (o antigo /tracks foi removido)
  - DELETE espera body: {"items": [{"uri": "spotify:track:xxx"}, ...]}
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
RFM_URL                = "https://rfm.pt/top25rfm"


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


def scrape_rfm_top25() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; rfm-top25-spotify-bot/1.0)"}
    resp = requests.get(RFM_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tracks = []
    for ul in soup.select("ul.g-mx"):
        pos_el  = ul.select_one("li.t-pos")
        desc_el = ul.select_one("li.t-desc")
        if not pos_el or not desc_el:
            continue
        position = pos_el.get_text(strip=True)
        if not position.isdigit():
            continue
        lis = desc_el.select("ul.unstyled li")
        if len(lis) >= 2:
            tracks.append({
                "position": int(position),
                "artist":   lis[0].get_text(strip=True),
                "title":    lis[1].get_text(strip=True),
            })
    return sorted(tracks, key=lambda x: x["position"])


def search_spotify(token: str, artist: str, title: str) -> str | None:
    params = {"q": f"track:{title} artist:{artist}", "type": "track", "limit": 10, "market": "PT"}
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    if items:
        return items[0]["uri"]
    # fallback: pesquisa simples
    params["q"] = f"{artist} {title}"
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def replace_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    """Substitui o conteudo completo da playlist usando PUT (max 100 tracks).
    Para mais de 100 tracks: PUT com os primeiros 100, POST com o resto.
    O Top 25 cabe sempre num unico PUT.
    """
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    # PUT substitui tudo de uma vez (até 100 uris)
    spotify_request("PUT", url, token, json={"uris": uris[:100]})
    # Se houver mais de 100 (improvável no Top 25), adiciona o resto
    for i in range(100, len(uris), 100):
        spotify_request("POST", url, token, json={"uris": uris[i:i + 100], "position": i})
        time.sleep(0.2)


def main() -> None:
    print("=== RFM Top 25 → Spotify ===")

    # 1. Scrape RFM
    print("\nA scraper o RFM Top 25...")
    tracks = scrape_rfm_top25()
    if not tracks:
        print("ERRO: Não foi possível obter os tracks do RFM.")
        sys.exit(1)
    print(f"{len(tracks)} tracks encontrados:")
    for t in tracks:
        print(f"  {t['position']:2}. {t['artist']} — {t['title']}")

    # 2. Auth
    print("\nA obter access token Spotify...")
    token = get_access_token()
    print("Token obtido com sucesso.")

    # 3. Verificar conta
    me = spotify_request("GET", SPOTIFY_ME_URL, token).json()
    print(f"  Conta: {me.get('display_name')} ({me.get('id')})")

    # 4. Pesquisar tracks
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

    # 5. Substituir playlist (PUT em vez de DELETE + POST)
    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    print(f"\nA substituir playlist com {len(uris)} tracks...")
    replace_playlist(token, playlist_id, uris)
    print("Playlist atualizada com sucesso! ✓")

    # 6. Job Summary
    now = datetime.datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
    summary = [
        "## 🎵 RFM Top 25 → Spotify",
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
        summary += [
            "",
            f"⚠️ {len(not_found)} track(s) não encontrados no Spotify.",
        ]

    write_summary(summary)


if __name__ == "__main__":
    main()
