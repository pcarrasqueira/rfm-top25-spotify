#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.

Estrutura do HTML da página:
  A lista ordenada do top 25 está num <ol> (ou lista de <li>) onde cada
  item tem:
    - um <li> com o número da posição
    - uma lista aninhada com artista e título como <li> separados
  Abaixo desta lista existe uma secção de candidatos à votação
  (com .g-pods-it) que NÃO devemos usar.
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

    A lista real está no HTML estático como uma <ul> onde cada item é:
      <li>
        <number>   <- texto com o número
        <ul>
          <li>Artista</li>
          <li>Título</li>
        </ul>
      </li>
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; rfm-top25-spotify-bot/1.0)"}
    resp = requests.get(RFM_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tracks = []

    # Procura todas as <ul> da página e encontra a que contém os itens do top
    # Cada item tem: li > (texto numérico + ul > [li artista, li titulo])
    for ul in soup.find_all("ul"):
        candidates = []
        for li in ul.find_all("li", recursive=False):
            # Texto direto do li (deve ser um número 1-25)
            direct_text = li.find(text=True, recursive=False)
            if not direct_text:
                continue
            position_str = direct_text.strip()
            if not position_str.isdigit():
                continue
            position = int(position_str)
            if position < 1 or position > 25:
                continue

            # Sub-lista com artista e título
            sub_ul = li.find("ul")
            if not sub_ul:
                continue
            sub_items = sub_ul.find_all("li", recursive=False)
            if len(sub_items) < 2:
                continue

            artist = sub_items[0].get_text(strip=True)
            title = sub_items[1].get_text(strip=True)
            if artist and title:
                candidates.append({"position": position, "artist": artist, "title": title})

        # A <ul> correta é a que tem pelo menos 20 entradas válidas
        if len(candidates) >= 20:
            tracks = sorted(candidates, key=lambda x: x["position"])
            break

    return tracks


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
