#!/usr/bin/env python3
"""
Scrapa o TNT Top 20 da Rádio Comercial em https://radiocomercial.pt/programas/tnt-todos-no-top
e actualiza a playlist Spotify indicada.

Estrutura HTML confirmada (Abril 2026):
  <div class="inside">
    <div class="songNumber">
      <div>1</div>          <- posição
      <div class="weeknumbers">...</div>
    </div>
    <div class="songTitle">Die On This Hill</div>   <- título
    <div class="songArtist">Sienna Spiro</div>      <- artista
    ...
  </div>

Nota API Spotify:
  - PUT /playlists/{id}/items com {"uris": [...]} substitui todos os items (max 100)
  - GET /search limit máximo 10
"""

import os
import re
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_PLAYLIST_URL   = "https://api.spotify.com/v1/playlists/{id}"
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
    """
    Raspa o TNT Top 20 da Rádio Comercial.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; comercial-tnt-spotify-bot/2.0)",
        "Accept-Language": "pt-PT,pt;q=0.9",
    }
    resp = requests.get(COMERCIAL_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tracks = []

    for inside in soup.find_all("div", class_="inside"):
        song_number_div = inside.find("div", class_="songNumber")
        if not song_number_div:
            continue

        pos_div = song_number_div.find("div", recursive=False)
        if not pos_div:
            continue
        pos_text = pos_div.get_text(strip=True)
        if not re.match(r'^\d+$', pos_text):
            continue
        pos = int(pos_text)

        title_div  = inside.find("div", class_="songTitle")
        artist_div = inside.find("div", class_="songArtist")

        if title_div and artist_div:
            title  = title_div.get_text(strip=True)
            artist = artist_div.get_text(strip=True)
        else:
            SKIP_KEYWORDS = {"semanas", "no", "tnt", "ultima", "semana", "em", "novo", "entrada", "=", "^", "v"}
            texts = []
            for child in inside.children:
                if hasattr(child, "get_text"):
                    t = child.get_text(separator=" ", strip=True)
                    if child == song_number_div:
                        continue
                    tokens = t.lower().split()
                    if all(tok.isdigit() or tok in SKIP_KEYWORDS for tok in tokens if tok):
                        continue
                    if t:
                        texts.append(t)
            if len(texts) >= 2:
                title  = texts[0]
                artist = texts[1]
            elif len(texts) == 1:
                sep = " \u2014 " if " \u2014 " in texts[0] else " - "
                parts = texts[0].split(sep, 1)
                title  = parts[0].strip()
                artist = parts[1].strip() if len(parts) > 1 else "Desconhecido"
            else:
                continue

        if title and artist:
            tracks.append({"position": pos, "title": title, "artist": artist})

    return sorted(tracks, key=lambda x: x["position"])


def search_spotify(token: str, artist: str, title: str) -> str | None:
    queries = [
        f'track:"{title}" artist:"{artist}"',
        f'{title} {artist}',
        f'{title}',
    ]
    for q in queries:
        params = {"q": q, "type": "track", "limit": 5, "market": "PT"}
        resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
    return None


def replace_playlist(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    spotify_request("PUT", url, token, json={"uris": uris[:100]})
    for i in range(100, len(uris), 100):
        spotify_request("POST", url, token, json={"uris": uris[i:i + 100], "position": i})
        time.sleep(0.2)


def update_playlist_description(token: str, playlist_id: str, description: str) -> None:
    url = SPOTIFY_PLAYLIST_URL.format(id=playlist_id)
    spotify_request("PUT", url, token, json={"description": description})


def main() -> None:
    print("=== Rádio Comercial TNT Top 20 -> Spotify ===")

    print("\n[1/3] A raspar radiocomercial.pt...")
    tracks = scrape_comercial_tnt()
    if not tracks:
        print("ERRO: Nenhum track encontrado. Verificar estrutura da página.")
        sys.exit(1)
    print(f"  {len(tracks)} tracks encontrados:")
    for t in tracks:
        print(f"  {t['position']:2d}. {t['artist']} - {t['title']}")

    print("\n[2/3] A pesquisar no Spotify...")
    token = get_access_token()
    me    = spotify_request("GET", SPOTIFY_ME_URL, token).json()
    print(f"  Conta: {me.get('display_name')} ({me.get('id')})")

    uris, not_found = [], []
    for t in tracks:
        uri = search_spotify(token, t["artist"], t["title"])
        status = "ok" if uri else "nao encontrado"
        print(f"  [{status}] {t['position']:2d}. {t['artist']} - {t['title']}")
        if uri:
            uris.append(uri)
        else:
            not_found.append(t)
        time.sleep(0.1)
    print(f"\n  Encontradas: {len(uris)}/{len(tracks)}")

    if not uris:
        print("ERRO: Nenhum track encontrado no Spotify.")
        sys.exit(1)

    playlist_id = os.environ["SPOTIFY_COMERCIAL_PLAYLIST_ID"]
    print(f"\n[3/3] A actualizar playlist {playlist_id}...")
    replace_playlist(token, playlist_id, uris)
    print("  Playlist actualizada com sucesso!")

    lisbon_str = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
    update_playlist_description(token, playlist_id, f"Actualizado a {lisbon_str}")

    now = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
    summary = [
        "## Rádio Comercial TNT Top 20 -> Spotify",
        f"> Actualizado em **{now}** &nbsp;—&nbsp; [{playlist_id}]({playlist_url})",
        "",
        f"**{len(uris)}/{len(tracks)} tracks** adicionados com sucesso.",
        "",
        "| # | Artista | Música | Estado |",
        "|---|---|---|---|",
    ]
    for t in tracks:
        found = not any(nf["position"] == t["position"] for nf in not_found)
        estado = "OK" if found else "nao encontrado"
        summary.append(f"| {t['position']} | {t['artist']} | {t['title']} | {estado} |")
    if not_found:
        summary += ["", f"Atencao: {len(not_found)} track(s) nao encontrados no Spotify."]
    write_summary(summary)


if __name__ == "__main__":
    main()
