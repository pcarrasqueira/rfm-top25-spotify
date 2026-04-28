#!/usr/bin/env python3
"""
Scrapa o historial de musicas tocadas na RFM (rfm.pt/que-musica-era)
e adiciona as novas a uma playlist Spotify, mantendo um limite de 100 tracks
e sem duplicados.

Corre 4x por dia via GitHub Actions e recolhe a hora actual e a anterior
para nao perder musicas entre execucoes.
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
RFM_HISTORY_URL        = "https://rfm.pt/que-musica-era"
PLAYLIST_LIMIT         = 100


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"]},
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def spotify(method: str, url: str, token: str, **kwargs) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if not resp.ok:
        print(f"  HTTP {resp.status_code} {method} {url}: {resp.text[:300]}")
        resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Scraping RFM
# ---------------------------------------------------------------------------

def scrape_hour(period: str, hour: str) -> list[dict]:
    """
    period: 'hoje' ou 'ontem'
    hour:   ex. '18' (so o numero)
    Devolve lista de {artist, title}
    """
    params = {"quando": period, "hora": hour}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; rfm-live-bot/1.0)"}
    try:
        resp = requests.get(RFM_HISTORY_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Erro ao scrape {period} hora {hour}: {e}")
        return []

    soup   = BeautifulSoup(resp.text, "lxml")
    tracks = []
    for li in soup.select("ul li"):
        children = li.find_all("li")
        if len(children) >= 2:
            title  = children[0].get_text(strip=True)
            artist = children[1].get_text(strip=True)
            if title and artist and title != "Quando" and artist != "Periodo":
                tracks.append({"artist": artist, "title": title})
    return tracks


def get_recent_tracks() -> list[dict]:
    """
    Recolhe a hora actual e a hora anterior para nao perder
    musicas tocadas perto da hora de execucao.
    """
    now    = datetime.datetime.utcnow() + datetime.timedelta(hours=1)  # Lisboa ~UTC+1
    tracks = []
    seen   = set()

    for delta in [0, 1]:  # hora actual e hora anterior
        dt     = now - datetime.timedelta(hours=delta)
        period = "hoje" if dt.date() == now.date() else "ontem"
        hour   = str(dt.hour)
        batch  = scrape_hour(period, hour)
        print(f"  {period} {hour}h: {len(batch)} tracks")
        for t in batch:
            key = (t["artist"].upper(), t["title"].upper())
            if key not in seen:
                seen.add(key)
                tracks.append(t)

    return tracks


# ---------------------------------------------------------------------------
# Spotify helpers
# ---------------------------------------------------------------------------

def search_track(token: str, artist: str, title: str) -> str | None:
    for query in [f"track:{title} artist:{artist}", f"{artist} {title}"]:
        resp  = spotify("GET", SPOTIFY_SEARCH_URL, token,
                        params={"q": query, "type": "track", "limit": 10, "market": "PT"})
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
        time.sleep(0.1)
    return None


def get_playlist_items(token: str, playlist_id: str) -> list[dict]:
    """Devolve lista de {uri, added_at} ordenada por added_at desc (mais recente primeiro)."""
    url    = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    items  = []
    params = {"fields": "next,items(added_at,item(uri))", "limit": 100}
    while url:
        data = spotify("GET", url, token, params=params).json()
        for e in data.get("items", []):
            item = e.get("item")
            if item and item.get("uri"):
                items.append({"uri": item["uri"], "added_at": e.get("added_at", "")})
        url    = data.get("next")
        params = {}
    return sorted(items, key=lambda x: x["added_at"], reverse=True)


def remove_items(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify("DELETE", url, token, json={"tracks": [{"uri": u} for u in uris[i:i+100]]})


def add_items(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify("POST", url, token, json={"uris": uris[i:i+100], "position": 0})
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== RFM Live → Spotify ===")

    # 1. Scrape
    print("\nA recolher historial RFM...")
    raw_tracks = get_recent_tracks()
    print(f"  {len(raw_tracks)} tracks unicos encontrados")
    if not raw_tracks:
        print("Nenhum track encontrado, a sair.")
        sys.exit(0)

    # 2. Auth
    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_LIVE_PLAYLIST_ID"]

    # 3. Estado actual da playlist
    print(f"\nA ler playlist {playlist_id}...")
    current = get_playlist_items(token, playlist_id)
    current_uris = [i["uri"] for i in current]
    current_set  = set(current_uris)
    print(f"  {len(current)} tracks actuais")

    # 4. Pesquisar no Spotify apenas os tracks ainda nao na playlist
    print("\nA pesquisar tracks novos...")
    new_uris = []
    for t in raw_tracks:
        uri = search_track(token, t["artist"], t["title"])
        if not uri:
            print(f"  ✗ {t['artist']} — {t['title']}")
            continue
        if uri in current_set:
            print(f"  = {t['artist']} — {t['title']} (ja existe)")
            continue
        print(f"  ✓ {t['artist']} — {t['title']}")
        new_uris.append(uri)

    if not new_uris:
        print("\nNenhum track novo para adicionar.")
        return

    # 5. Gerir limite de 100 tracks
    # Adicionar no topo (position=0), remover os mais antigos se necessario
    total_after = len(current_uris) + len(new_uris)
    to_remove   = []
    if total_after > PLAYLIST_LIMIT:
        overflow  = total_after - PLAYLIST_LIMIT
        # os mais antigos estao no fim da lista (sorted desc)
        to_remove = current_uris[-overflow:]
        print(f"\n  Limite atingido — a remover {len(to_remove)} tracks antigos")
        remove_items(token, playlist_id, to_remove)

    # 6. Adicionar novos no topo
    print(f"\nA adicionar {len(new_uris)} tracks novos...")
    add_items(token, playlist_id, new_uris)
    print("Playlist atualizada com sucesso! ✓")


if __name__ == "__main__":
    main()
