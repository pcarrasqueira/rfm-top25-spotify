#!/usr/bin/env python3
"""
Scrapa o RFM Top 25 em https://rfm.pt/top25rfm
e atualiza a playlist Spotify indicada.

Nota API Spotify (Fev 2026):
  - GET/POST/DELETE usam /playlists/{id}/items (o antigo /tracks foi removido)
  - DELETE espera body: {"items": [{"uri": "spotify:track:xxx"}, ...]}
  - PUT /playlists/{id}/items com {"uris": [...]} substitui todos os items de uma vez (max 100)
  - GET /search tem limit maximo de 10
"""

import os
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
from spotify_client import (
    QuotaExceededError,
    RateLimitError,
    get_access_token,
    spotify_request,
)

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_PLAYLIST_URL   = "https://api.spotify.com/v1/playlists/{id}"
SPOTIFY_ME_URL         = "https://api.spotify.com/v1/me"
RFM_URL                = "https://rfm.pt/top25rfm"


def write_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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


def search_spotify(token: str, artist: str, title: str) -> dict | None:
    queries = [
        f"track:{title} artist:{artist}",
        f"{artist} {title}",
    ]
    for q in queries:
        params = {"q": q, "type": "track", "limit": 10, "market": "PT"}
        resp  = spotify_request("GET", SPOTIFY_SEARCH_URL, token, params=params)
        time.sleep(0.3)
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            item = items[0]
            return {
                "uri": item["uri"],
                "spotify_artist": item["artists"][0]["name"] if item.get("artists") else artist,
                "spotify_title": item["name"],
            }
        time.sleep(0.3)
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
    print("=== RFM Top 25 -> Spotify ===")

    print("\nA scraper o RFM Top 25...")
    tracks = scrape_rfm_top25()
    if not tracks:
        print("ERRO: Nao foi possivel obter os tracks do RFM.")
        sys.exit(1)
    print(f"{len(tracks)} tracks encontrados:")
    for t in tracks:
        print(f"  {t['position']:2}. {t['artist']} - {t['title']}")

    print("\nA obter access token Spotify...")
    token = get_access_token()
    print("Token obtido com sucesso.")

    me = spotify_request("GET", SPOTIFY_ME_URL, token).json()
    print(f"  Conta: {me.get('display_name')} ({me.get('id')})")

    print("\nA pesquisar tracks no Spotify...")
    results = []
    uris    = []
    for t in tracks:
        match = search_spotify(token, t["artist"], t["title"])
        if match:
            results.append({"track": t, "status": "added", "match": match})
            uris.append(match["uri"])
            print(f"  \u2713 {t['artist']} - {t['title']}")
        else:
            results.append({"track": t, "status": "not_found"})
            print(f"  \u2717 {t['artist']} - {t['title']} (nao encontrado)")

    if not uris:
        print("ERRO: Nenhum track encontrado no Spotify.")
        sys.exit(1)

    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    print(f"\nA substituir playlist com {len(uris)} tracks...")
    replace_playlist(token, playlist_id, uris)
    print("Playlist actualizada com sucesso!")

    lisbon_str = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
    update_playlist_description(token, playlist_id, f"Actualizado a {lisbon_str}")

    now          = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
    not_found    = [r for r in results if r["status"] == "not_found"]

    summary = [
        "## \U0001f3b5 RFM Top 25 -> Spotify",
        f"> Actualizado em **{now}** &nbsp;\u2014&nbsp; [Abrir playlist]({playlist_url})",
        "",
        f"**{len(uris)}/{len(tracks)} tracks** adicionados com sucesso.",
        "",
        "| # | Artista (r\u00e1dio) | M\u00fasica (r\u00e1dio) | Artista (Spotify) | M\u00fasica (Spotify) | Estado |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        t  = r["track"]
        m  = r.get("match") or {}
        sp_artist = m.get("spotify_artist", "")
        sp_title  = m.get("spotify_title", "")
        estado    = "\u2705 adicionado" if r["status"] == "added" else "\u274c n\u00e3o encontrado"
        summary.append(f"| {t['position']} | {t['artist']} | {t['title']} | {sp_artist} | {sp_title} | {estado} |")

    if not_found:
        summary += ["", f"\u26a0\ufe0f {len(not_found)} track(s) nao encontrados no Spotify."]

    write_summary(summary)


if __name__ == "__main__":
    try:
        main()
    except QuotaExceededError as exc:
        message = f"Spotify quota excedida — {exc}"
        print(f"\n  {message}")
        write_summary(["## RFM Top 25 -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(1)
    except RateLimitError as exc:
        message = f"Rate limit Spotify — aguardar aproximadamente {exc.retry_after}s."
        print(f"\n  {message}")
        write_summary(["## RFM Top 25 -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(1)
