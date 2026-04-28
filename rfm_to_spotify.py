#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.
"""

import os
import sys
import time
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_URL = "https://api.spotify.com/v1/playlists/{id}/tracks"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_PLAYLIST_INFO_URL = "https://api.spotify.com/v1/playlists/{id}"
RFM_URL = "https://rfm.pt/top25rfm"


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


def get_access_token() -> tuple[str, str]:
    """Troca o refresh token por um access token. Devolve (token, scopes)."""
    client_id = os.environ["SPOTIFY_CLIENT_ID"]
    client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
    refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(client_id, client_secret),
        timeout=15,
    )
    if not resp.ok:
        print(f"  HTTP {resp.status_code} ao obter token")
        print(f"  Resposta: {resp.text[:300]}")
        resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("scope", "(sem scope na resposta)")


def scrape_rfm_top25() -> list[dict]:
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
    params = {
        "q": f"track:{title} artist:{artist}",
        "type": "track",
        "limit": 5,
        "market": "PT",
    }
    resp = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    if items:
        return items[0]["uri"]
    params["q"] = f"{artist} {title}"
    resp = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def replace_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_URL.format(id=playlist_id)
    spotify_request("PUT", url, token, json={"uris": uris[:100]})
    for batch_start in range(100, len(uris), 100):
        spotify_request("POST", url, token, json={"uris": uris[batch_start:batch_start + 100]})


def main() -> None:
    print("=== RFM Top 25 \u2192 Spotify ===")

    # 1. Scrape RFM
    print("\nA scraper o RFM Top 25...")
    tracks = scrape_rfm_top25()
    if not tracks:
        print("ERRO: N\u00e3o foi poss\u00edvel obter os tracks do RFM.")
        sys.exit(1)
    print(f"{len(tracks)} tracks encontrados:")
    for t in tracks:
        print(f"  {t['position']:2}. {t['artist']} \u2014 {t['title']}")

    # 2. Auth Spotify
    print("\nA obter access token Spotify...")
    token, scopes = get_access_token()
    print(f"Token obtido. Scopes concedidos: {scopes}")

    # 3. Verificar identidade e ownership da playlist
    print("\nA verificar conta autenticada...")
    me = spotify_request("GET", SPOTIFY_ME_URL, token).json()
    print(f"  Conta: {me.get('display_name')} ({me.get('id')}) | email: {me.get('email')}")

    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    print(f"\nA verificar playlist {playlist_id}...")
    pl = spotify_request("GET", SPOTIFY_PLAYLIST_INFO_URL.format(id=playlist_id), token).json()
    owner = pl.get("owner", {})
    print(f"  Playlist: '{pl.get('name')}' | Owner: {owner.get('display_name')} ({owner.get('id')})")
    print(f"  Collaborative: {pl.get('collaborative')} | Public: {pl.get('public')}")
    if owner.get("id") != me.get("id"):
        print("  AVISO: O owner da playlist NAO corresponde à conta autenticada!")

    # 4. Pesquisar tracks
    print("\nA pesquisar tracks no Spotify...")
    uris = []
    not_found = []
    for t in tracks:
        uri = search_spotify(token, t["artist"], t["title"])
        if uri:
            uris.append(uri)
            print(f"  \u2713 {t['artist']} \u2014 {t['title']}")
        else:
            not_found.append(t)
            print(f"  \u2717 {t['artist']} \u2014 {t['title']} (n\u00e3o encontrado)")
        time.sleep(0.1)

    if not uris:
        print("ERRO: Nenhum track encontrado no Spotify.")
        sys.exit(1)

    print(f"\n{len(uris)}/{len(tracks)} tracks encontrados.")

    # 5. Atualizar playlist
    print(f"\nA atualizar playlist {playlist_id}...")
    replace_playlist(token, playlist_id, uris)
    print("Playlist atualizada com sucesso! \u2713")


if __name__ == "__main__":
    main()
