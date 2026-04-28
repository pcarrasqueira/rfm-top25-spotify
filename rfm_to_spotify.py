#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.
"""

import os
import sys
import json
import time
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{id}/tracks"
RFM_URL = "https://rfm.pt/top25rfm"


def get_access_token() -> str:
    """Troca o refresh token por um access token."""
    client_id = os.environ["SPOTIFY_CLIENT_ID"]
    client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
    refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def scrape_rfm_top25() -> list[dict]:
    """Devolve lista de {position, artist, title} do RFM Top 25."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; rfm-top25-spotify-bot/1.0)"
    }
    resp = requests.get(RFM_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    tracks = []

    # RFM usa um bloco de JSON embebido com os dados da chart — tentamos isso primeiro
    # e fazemos fallback para parsing HTML.
    for script in soup.find_all("script", type="application/json"):
        try:
            data = json.loads(script.string or "")
            # Procura estrutura {songs: [{title, artist}]}
            songs = None
            if isinstance(data, dict):
                songs = data.get("songs") or data.get("tracks") or data.get("items")
            if songs:
                for i, s in enumerate(songs[:25], 1):
                    tracks.append({
                        "position": i,
                        "title": s.get("title") or s.get("name", ""),
                        "artist": s.get("artist") or s.get("artistName", ""),
                    })
                if tracks:
                    return tracks
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: parsing HTML — procura padrões comuns em sites de rádio PT
    selectors = [
        ("div.chart-item", "span.artist", "span.title"),
        ("li.chart-item", ".artist", ".title"),
        ("div.music-item", ".artist-name", ".song-name"),
        ("article.top-item", ".artist", ".song"),
    ]
    for container_sel, artist_sel, title_sel in selectors:
        items = soup.select(container_sel)
        if not items:
            continue
        for i, item in enumerate(items[:25], 1):
            artist_el = item.select_one(artist_sel)
            title_el = item.select_one(title_sel)
            if artist_el and title_el:
                tracks.append({
                    "position": i,
                    "title": title_el.get_text(strip=True),
                    "artist": artist_el.get_text(strip=True),
                })
        if tracks:
            return tracks

    # Última tentativa: qualquer lista ordenada ou div com atributo data-*
    items = soup.select("[data-rank], [data-position], [data-index]")
    for item in items[:25]:
        text = item.get_text(separator=" | ", strip=True)
        parts = text.split("|")
        if len(parts) >= 2:
            tracks.append({
                "position": len(tracks) + 1,
                "artist": parts[0].strip(),
                "title": parts[1].strip(),
            })

    return tracks


def search_spotify(token: str, artist: str, title: str) -> str | None:
    """Devolve o URI do track mais relevante no Spotify."""
    headers = {"Authorization": f"Bearer {token}"}
    query = f"track:{title} artist:{artist}"
    params = {"q": query, "type": "track", "limit": 5, "market": "PT"}

    resp = requests.get(SPOTIFY_SEARCH_URL, headers=headers, params=params, timeout=15)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"Rate limited — a aguardar {retry_after}s...")
        time.sleep(retry_after)
        return search_spotify(token, artist, title)
    resp.raise_for_status()

    items = resp.json().get("tracks", {}).get("items", [])
    if items:
        return items[0]["uri"]

    # Fallback: pesquisa mais permissiva
    params["q"] = f"{artist} {title}"
    resp = requests.get(SPOTIFY_SEARCH_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def replace_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    """Substitui TODOS os tracks da playlist pelos novos URIs."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = SPOTIFY_PLAYLIST_URL.format(id=playlist_id)

    # Spotify aceita no máximo 100 URIs por pedido
    # Primeiro PUT substitui os primeiros 100 (ou todos se < 100)
    resp = requests.put(url, headers=headers, json={"uris": uris[:100]}, timeout=15)
    resp.raise_for_status()

    # Se houver mais de 100 (improvável para top 25), faz POST
    for batch_start in range(100, len(uris), 100):
        batch = uris[batch_start : batch_start + 100]
        resp = requests.post(url, headers=headers, json={"uris": batch}, timeout=15)
        resp.raise_for_status()


def main() -> None:
    print("=== RFM Top 25 → Spotify ===")

    # 1. Scrape RFM
    print("\nA scraper o RFM Top 25...")
    tracks = scrape_rfm_top25()
    if not tracks:
        print("ERRO: Não foi possível obter os tracks do RFM.")
        sys.exit(1)
    print(f"{len(tracks)} tracks encontrados.")
    for t in tracks:
        print(f"  {t['position']:2}. {t['artist']} — {t['title']}")

    # 2. Auth Spotify
    print("\nA obter access token Spotify...")
    token = get_access_token()
    print("Token obtido com sucesso.")

    # 3. Pesquisar cada track
    print("\nA pesquisar tracks no Spotify...")
    uris = []
    not_found = []
    for t in tracks:
        uri = search_spotify(token, t["artist"], t["title"])
        if uri:
            uris.append(uri)
            print(f"  ✓ {t['artist']} — {t['title']}")
        else:
            not_found.append(t)
            print(f"  ✗ {t['artist']} — {t['title']} (não encontrado)")
        time.sleep(0.1)  # respeita rate limits

    if not uris:
        print("ERRO: Nenhum track encontrado no Spotify.")
        sys.exit(1)

    print(f"\n{len(uris)}/{len(tracks)} tracks encontrados no Spotify.")
    if not_found:
        print("Tracks não encontrados:")
        for t in not_found:
            print(f"  - {t['artist']} — {t['title']}")

    # 4. Atualizar playlist
    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    print(f"\nA atualizar playlist {playlist_id}...")
    replace_playlist(token, playlist_id, uris)
    print("Playlist atualizada com sucesso! ✓")


if __name__ == "__main__":
    main()
