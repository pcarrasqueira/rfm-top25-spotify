#!/usr/bin/env python3
"""
Scrapa o historial de musicas tocadas na RFM (rfm.pt/que-musica-era)
e adiciona as novas a uma playlist Spotify, mantendo um limite de 300 tracks
e sem duplicados. Corre hora a hora via GitHub Actions.

Nota API Spotify (Fev 2026):
  - DELETE /playlists/{id}/items espera body: {"items": [{"uri": "spotify:track:xxx"}, ...]}
  - POST /playlists/{id}/items espera body: {"uris": [...], "position": 0}
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
PLAYLIST_LIMIT         = 300


def write_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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


def scrape_current() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; rfm-live-bot/1.0)"}
    try:
        resp = requests.get(RFM_HISTORY_URL, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Erro ao scrape RFM: {e}")
        return []

    soup   = BeautifulSoup(resp.text, "lxml")
    tracks = []
    seen   = set()
    for li in soup.select("ul li"):
        children = li.find_all("li")
        if len(children) >= 2:
            title  = children[0].get_text(strip=True)
            artist = children[1].get_text(strip=True)
            if not title or not artist or title == "Quando" or artist == "Periodo":
                continue
            key = (artist.upper(), title.upper())
            if key not in seen:
                seen.add(key)
                tracks.append({"artist": artist, "title": title})
    return tracks


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
    """Remove tracks da playlist. O endpoint /items espera {"items": [{"uri": ...}]}."""
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify("DELETE", url, token, json={"items": [{"uri": u} for u in uris[i:i + 100]]})


def add_items(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify("POST", url, token, json={"uris": uris[i:i + 100], "position": 0})
        time.sleep(0.2)


def main() -> None:
    print("=== RFM Live → Spotify ===")

    # 1. Scrape hora actual
    print("\nA recolher tracks actuais da RFM...")
    raw_tracks = scrape_current()
    if not raw_tracks:
        print("Nenhum track encontrado, a sair.")
        sys.exit(0)
    print(f"  {len(raw_tracks)} tracks encontrados:")
    for t in raw_tracks:
        print(f"    - {t['artist']} — {t['title']}")

    # 2. Auth
    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_LIVE_PLAYLIST_ID"]

    # 3. Estado actual da playlist
    print(f"\nA ler playlist {playlist_id}...")
    current      = get_playlist_items(token, playlist_id)
    current_uris = [i["uri"] for i in current]
    current_set  = set(current_uris)
    print(f"  {len(current)} tracks actuais na playlist")

    # 4. Pesquisar apenas tracks novos
    print("\nA pesquisar tracks novos no Spotify...")
    new_uris   = []
    added      = []
    skipped    = []
    not_found  = []
    for t in raw_tracks:
        uri = search_track(token, t["artist"], t["title"])
        if not uri:
            print(f"  ✗ {t['artist']} — {t['title']} (não encontrado)")
            not_found.append(t)
            continue
        if uri in current_set:
            print(f"  = {t['artist']} — {t['title']} (já existe)")
            skipped.append(t)
            continue
        print(f"  ✓ {t['artist']} — {t['title']} (novo)")
        new_uris.append(uri)
        added.append(t)

    # 5. Gerir limite de 300 tracks
    removed_count = 0
    if new_uris:
        total_after = len(current_uris) + len(new_uris)
        if total_after > PLAYLIST_LIMIT:
            overflow  = total_after - PLAYLIST_LIMIT
            to_remove = current_uris[-overflow:]
            removed_count = len(to_remove)
            print(f"\n  Limite atingido — a remover {removed_count} tracks antigos")
            remove_items(token, playlist_id, to_remove)

        print(f"\nA adicionar {len(new_uris)} tracks novos...")
        add_items(token, playlist_id, new_uris)
        print("Playlist atualizada com sucesso! ✓")
    else:
        print("\nNenhum track novo para adicionar.")

    # 6. Job Summary
    now = datetime.datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

    summary = [
        "## 📻 Últimas no Ar — RFM Live → Spotify",
        f"> Atualizado em **{now}** &nbsp;—&nbsp; [{playlist_id}]({playlist_url})",
        "",
    ]

    if added:
        summary += [
            f"### ✅ {len(added)} track(s) adicionados",
            "",
            "| Artista | Música |",
            "|---|---|",
        ]
        for t in added:
            summary.append(f"| {t['artist']} | {t['title']} |")
        summary.append("")
    else:
        summary += ["### ℹ️ Sem tracks novos nesta hora", ""]

    if skipped:
        summary += [
            f"<details><summary>= {len(skipped)} já existentes (clica para ver)</summary>",
            "",
            "| Artista | Música |",
            "|---|---|",
        ]
        for t in skipped:
            summary.append(f"| {t['artist']} | {t['title']} |")
        summary += ["", "</details>", ""]

    if not_found:
        summary += [
            f"⚠️ {len(not_found)} track(s) não encontrados no Spotify:",
        ]
        for t in not_found:
            summary.append(f"- {t['artist']} — {t['title']}")
        summary.append("")

    if removed_count:
        summary.append(f"🗑️ {removed_count} track(s) antigos removidos para manter limite de {PLAYLIST_LIMIT}.")

    write_summary(summary)


if __name__ == "__main__":
    main()
