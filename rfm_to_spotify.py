#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.

Estrutura do HTML:
  Os 25 tracks estão em <ul class='g-mx ...'> com:
    <li class='t-pos'>  -> número da posição
    <li class='t-cover'> -> capa
    <li class='t-desc'>  -> <ul class='unstyled'><li>Artista</li><li>Título</li></ul>
  Os dados estão integralmente no HTML estático, sem necessidade de JS.
"""

import os
import sys
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
    """
    Devolve lista de {position, artist, title} com o Top 25 do RFM.
    Os tracks estão no HTML estático em <ul class='g-mx'> dentro de
    section.list-25.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; rfm-top25-spotify-bot/1.0)"}
    resp = requests.get(RFM_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tracks = []
    for ul in soup.select("ul.g-mx"):
        pos_el = ul.select_one("li.t-pos")
        desc_el = ul.select_one("li.t-desc")
        if not pos_el or not desc_el:
            continue
        position = pos_el.get_text(strip=True)
        if not position.isdigit():
            continue
        lis = desc_el.select("ul.unstyled li")
        if len(lis) >= 2:
            artist = lis[0].get_text(strip=True)
            title = lis[1].get_text(strip=True)
            tracks.append({"position": int(position), "artist": artist, "title": title})

    return sorted(tracks, key=lambda x: x["position"])


def search_spotify(token: str, artist: str, title: str) -> str | None:
    """Devolve o URI do track mais relevante no Spotify."""
    headers = {"Authorization": f"Bearer {token}"}

    # Pesquisa precisa primeiro
    params = {
        "q": f"track:{title} artist:{artist}",
        "type": "track",
        "limit": 5,
        "market": "PT",
    }
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

    resp = requests.put(url, headers=headers, json={"uris": uris[:100]}, timeout=15)
    resp.raise_for_status()

    for batch_start in range(100, len(uris), 100):
        batch = uris[batch_start: batch_start + 100]
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
    print(f"{len(tracks)} tracks encontrados:")
    for t in tracks:
        print(f"  {t['position']:2}. {t['artist']} — {t['title']}")

    # 2. Auth Spotify
    print("\nA obter access token Spotify...")
    token = get_access_token()
    print("Token obtido com sucesso.")

    # 3. Pesquisar cada track no Spotify
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
        time.sleep(0.1)

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
