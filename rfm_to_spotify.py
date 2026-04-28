#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.

Estratégia de atualização:
  1. GET todos os track URIs actuais da playlist
  2. DELETE todos (em batches de 100)
  3. POST os novos (em batches de 100)
  Evita o PUT /tracks que o Spotify bloqueia em Development Mode.
"""

import os
import sys
import time
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_TRACKS = "https://api.spotify.com/v1/playlists/{id}/tracks"
SPOTIFY_ME_URL         = "https://api.spotify.com/v1/me"
RFM_URL                = "https://rfm.pt/top25rfm"


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
    client_id     = os.environ["SPOTIFY_CLIENT_ID"]
    client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
    refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(client_id, client_secret),
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
    params = {"q": f"track:{title} artist:{artist}", "type": "track", "limit": 5, "market": "PT"}
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    if items:
        return items[0]["uri"]
    params["q"] = f"{artist} {title}"
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def get_current_tracks(token: str, playlist_id: str) -> list[str]:
    """Devolve todos os URIs actuais da playlist (paginado)."""
    url    = SPOTIFY_PLAYLIST_TRACKS.format(id=playlist_id)
    uris   = []
    params = {"fields": "next,items(track(uri))", "limit": 100}
    while url:
        resp = spotify_request("GET", url, token, params=params)
        data = resp.json()
        for item in data.get("items", []):
            track = item.get("track")
            if track and track.get("uri"):
                uris.append(track["uri"])
        url    = data.get("next")
        params = {}  # next já tem os params no URL
    return uris


def clear_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    """Remove todos os tracks da playlist em batches de 100."""
    url = SPOTIFY_PLAYLIST_TRACKS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        batch = [{"uri": u} for u in uris[i:i + 100]]
        spotify_request("DELETE", url, token, json={"tracks": batch})


def add_tracks(token: str, playlist_id: str, uris: list[str]) -> None:
    """Adiciona tracks à playlist em batches de 100."""
    url = SPOTIFY_PLAYLIST_TRACKS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
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

    # 4. Pesquisar tracks no Spotify
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

    print(f"\n{len(uris)}/{len(tracks)} tracks encontrados.")
    if not_found:
        for t in not_found:
            print(f"  - {t['artist']} — {t['title']}")

    # 5. Limpar playlist actual
    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    print(f"\nA obter tracks actuais da playlist {playlist_id}...")
    current_uris = get_current_tracks(token, playlist_id)
    print(f"  {len(current_uris)} tracks actuais a remover...")
    if current_uris:
        clear_playlist(token, playlist_id, current_uris)
        print("  Playlist limpa ✓")

    # 6. Adicionar novos tracks
    print("A adicionar novos tracks...")
    add_tracks(token, playlist_id, uris)
    print("Playlist atualizada com sucesso! ✓")


if __name__ == "__main__":
    main()
