#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.

Nota: Em Fevereiro de 2026 a Spotify removeu os endpoints /playlists/{id}/tracks
e substituiu por /playlists/{id}/items. O campo track foi renomeado para item.
A pesquisa via GET /search tem agora limit máximo de 10.
"""

import os
import sys
import time
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL       = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL      = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS  = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_ME_URL          = "https://api.spotify.com/v1/me"
RFM_URL                 = "https://rfm.pt/top25rfm"


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
    # limit máximo é 10 desde Fev 2026
    params = {"q": f"track:{title} artist:{artist}", "type": "track", "limit": 10, "market": "PT"}
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    if items:
        return items[0]["uri"]
    # fallback mais permissivo
    params["q"] = f"{artist} {title}"
    resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
    items = resp.json().get("tracks", {}).get("items", [])
    return items[0]["uri"] if items else None


def get_current_items(token: str, playlist_id: str) -> list[str]:
    """Devolve todos os URIs actuais da playlist via novo endpoint /items."""
    url    = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    uris   = []
    params = {"fields": "next,items(item(uri))", "limit": 100}
    while url:
        resp = spotify_request("GET", url, token, params=params)
        data = resp.json()
        for entry in data.get("items", []):
            item = entry.get("item")  # renomeado de 'track' para 'item' em Fev 2026
            if item and item.get("uri"):
                uris.append(item["uri"])
        url    = data.get("next")
        params = {}
    return uris


def clear_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    """Remove todos os items da playlist em batches de 100 via DELETE /items."""
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        batch = [{"uri": u} for u in uris[i:i + 100]]
        spotify_request("DELETE", url, token, json={"tracks": batch})


def add_items(token: str, playlist_id: str, uris: list[str]) -> None:
    """Adiciona items à playlist em batches de 100 via POST /items."""
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify_request("POST", url, token, json={"uris": uris[i:i + 100], "position": i})
        time.sleep(0.2)


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
            print(f"  \u2713 {t['artist']} \u2014 {t['title']}")
        else:
            not_found.append(t)
            print(f"  \u2717 {t['artist']} \u2014 {t['title']} (n\u00e3o encontrado)")
        time.sleep(0.1)

    if not uris:
        print("ERRO: Nenhum track encontrado no Spotify.")
        sys.exit(1)

    print(f"\n{len(uris)}/{len(tracks)} tracks encontrados.")
    if not_found:
        for t in not_found:
            print(f"  - {t['artist']} \u2014 {t['title']}")

    # 5. Limpar playlist actual
    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    print(f"\nA obter items actuais da playlist {playlist_id}...")
    current_uris = get_current_items(token, playlist_id)
    print(f"  {len(current_uris)} items actuais a remover...")
    if current_uris:
        clear_playlist(token, playlist_id, current_uris)
        print("  Playlist limpa \u2713")

    # 6. Adicionar novos tracks
    print("A adicionar novos tracks...")
    add_items(token, playlist_id, uris)
    print("Playlist atualizada com sucesso! \u2713")


if __name__ == "__main__":
    main()
