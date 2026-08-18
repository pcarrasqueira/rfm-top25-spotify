#!/usr/bin/env python3
"""
Recolhe as musicas tocadas na Radio Comercial na ultima 1h via API JSON:
  https://radiocomercial.pt/now_playing_logs/json/radio-comercial_YYYY-MM-DD.json
"""

import os
import sys
import time
import datetime
import requests
from zoneinfo import ZoneInfo
from spotify_client import (
    QuotaExceededError,
    RateLimitError,
    get_access_token,
    normalize_track_key,
    playlist_entry_to_match,
    spotify_request as spotify,
)

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_PLAYLIST_URL   = "https://api.spotify.com/v1/playlists/{id}"
COMERCIAL_LOG_URL      = "https://radiocomercial.pt/now_playing_logs/json/radio-comercial_{date}.json"
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 1


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def fetch_tracks() -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Referer": "https://radiocomercial.pt/passou",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    tz         = ZoneInfo("Europe/Lisbon")
    lisbon_now = datetime.datetime.now(tz).replace(tzinfo=None)
    cutoff     = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)
    date_str      = lisbon_now.strftime("%Y-%m-%d")
    url           = COMERCIAL_LOG_URL.format(date=date_str)

    resp    = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    records = resp.json().get("NOW_PLAYING_LOG", {}).get("NOW_PLAYING_RECORD", [])
    print(f"  {len(records)} registos totais hoje ({date_str})")
    print(f"  Janela: {cutoff.strftime('%H:%M')} - {lisbon_now.strftime('%H:%M')} Lisboa")

    seen, tracks = set(), []
    for rec in reversed(records):
        date_raw = rec.get("DATE", "")
        try:
            rec_dt = datetime.datetime.strptime(date_raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if rec_dt < cutoff:
            continue
        zenon  = rec.get("ZENON", {})
        title  = zenon.get("SONG_NAME",  "").strip()
        artist = zenon.get("ARTIST_NAME", "").strip()
        if not title or not artist:
            continue
        key = (artist.upper(), title.upper())
        if key not in seen:
            seen.add(key)
            tracks.append({"artist": artist, "title": title})
    print(f"  {len(tracks)} tracks unicos na ultima {WINDOW_HOURS}h")
    return tracks


def search_track(token: str, artist: str, title: str) -> dict | None:
    for query in [f'track:"{title}" artist:"{artist}"', f"{artist} {title}"]:
        resp  = spotify("GET", SPOTIFY_SEARCH_URL, token,
                        params={"q": query, "type": "track", "limit": 5, "market": "PT"})
        time.sleep(0.3)
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            item = items[0]
            return {
                "uri": item["uri"],
                "spotify_artist": item["artists"][0]["name"] if item.get("artists") else artist,
                "spotify_title": item["name"],
            }
    return None


def get_playlist_uris(
    token: str, playlist_id: str
) -> tuple[list[str], dict[tuple[str, str], dict[str, str]]]:
    url    = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    uris   = []
    lookup = {}
    params = {"limit": 100}
    while url:
        data = spotify("GET", url, token, params=params).json()
        for e in data.get("items", []):
            entry = (e or {}).get("item") or (e or {}).get("track")
            parsed = playlist_entry_to_match(entry)
            if parsed:
                match, keys = parsed
                uris.append(match["uri"])
                for key in keys:
                    lookup.setdefault(key, match)
        url    = data.get("next")
        params = {}
    return uris, lookup


def remove_items(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify("DELETE", url, token, json={"items": [{"uri": u} for u in uris[i:i + 100]]})


def add_items(token: str, playlist_id: str, uris: list[str]) -> None:
    url = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    for i in range(0, len(uris), 100):
        spotify("POST", url, token, json={"uris": uris[i:i + 100], "position": 0})
        time.sleep(0.2)


def trim_playlist(token: str, playlist_id: str, current_uris: list[str], slots_needed: int) -> tuple[list[str], int]:
    target  = PLAYLIST_LIMIT - slots_needed
    removed = 0
    if len(current_uris) > target:
        overflow     = len(current_uris) - target
        to_remove    = current_uris[-overflow:]
        removed      = len(to_remove)
        print(f"  A remover {removed} tracks antigos...")
        remove_items(token, playlist_id, to_remove)
        current_uris = current_uris[:-overflow]
    return current_uris, removed


def update_playlist_description(token: str, playlist_id: str, description: str) -> None:
    url = SPOTIFY_PLAYLIST_URL.format(id=playlist_id)
    spotify("PUT", url, token, json={"description": description})


def main() -> None:
    print("=== Radio Comercial Live -> Spotify ===")

    print("\nA recolher tracks da API...")
    raw_tracks = fetch_tracks()
    if not raw_tracks:
        print("Nenhum track encontrado na janela de tempo, a sair.")
        write_summary(["## Radio Comercial Live -> Spotify", "", "Sem tracks novos na ultima 1h."])
        sys.exit(0)

    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_COMERCIAL_LIVE_PLAYLIST_ID"]

    print("\nA ler playlist actual...")
    current_uris, playlist_lookup = get_playlist_uris(token, playlist_id)
    current_set  = set(current_uris)
    print(f"  {len(current_uris)} tracks na playlist")

    print(f"\nA pesquisar {len(raw_tracks)} tracks no Spotify...")
    results   = []
    new_uris  = []
    try:
        for t in raw_tracks:
            match = playlist_lookup.get(normalize_track_key(t["artist"], t["title"]))
            if match:
                results.append({"track": t, "status": "skipped", "match": match})
                print(f"  ~ {t['artist']} - {t['title']} (ja existe; cache da playlist)")
                continue

            match = search_track(token, t["artist"], t["title"])
            if not match:
                results.append({"track": t, "status": "not_found"})
                print(f"  \u2717 {t['artist']} - {t['title']}")
            elif match["uri"] in current_set:
                results.append({"track": t, "status": "skipped", "match": match})
                print(f"  ~ {t['artist']} - {t['title']} (ja existe)")
            else:
                results.append({"track": t, "status": "added", "match": match})
                new_uris.append(match["uri"])
                print(f"  \u2713 {t['artist']} - {t['title']}")
    except QuotaExceededError as e:
        msg = f"Spotify quota excedida — {e}"
        print(f"\n  {msg}")
        write_summary(["## Radio Comercial Live -> Spotify", "", f"> ⚠️ {msg}"])
        sys.exit(1)
    except RateLimitError as e:
        msg = f"\u23f3 Rate limit atingido \u2014 Spotify pede para aguardar **{e.retry_after}s** antes de tentar de novo."
        print(f"\n  {msg}")
        write_summary(["## Radio Comercial Live -> Spotify", "", f"> \u26a0\ufe0f {msg}"])
        sys.exit(1)

    added     = [r for r in results if r["status"] == "added"]
    skipped   = [r for r in results if r["status"] == "skipped"]
    not_found = [r for r in results if r["status"] == "not_found"]
    print(f"\nNovos: {len(added)} | Ja na playlist: {len(skipped)} | Nao encontrados: {len(not_found)}")

    slots_needed          = min(len(new_uris), PLAYLIST_LIMIT)
    current_uris, removed_count = trim_playlist(token, playlist_id, current_uris, slots_needed)

    if new_uris:
        space    = PLAYLIST_LIMIT - len(current_uris)
        new_uris = new_uris[:space]
        print(f"  A adicionar {len(new_uris)} tracks...")
        add_items(token, playlist_id, new_uris)
        print("Playlist actualizada!")
    else:
        print("Nenhum track novo para adicionar.")

    if new_uris:
        lisbon_str = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
        update_playlist_description(token, playlist_id, f"Actualizado a {lisbon_str}")
    else:
        print("Sem alterações na playlist; descrição não atualizada.")

    now          = datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

    summary = [
        "## Radio Comercial Live -> Spotify",
        f"> Actualizado em **{now}** &nbsp;\u2014&nbsp; [Abrir playlist]({playlist_url})",
        "",
        f"**{len(added)}/{len(raw_tracks)} tracks** adicionados &nbsp;|&nbsp; {len(skipped)} ja existentes &nbsp;|&nbsp; {len(not_found)} nao encontrados",
        "",
        "| Artista (r\u00e1dio) | M\u00fasica (r\u00e1dio) | Artista (Spotify) | M\u00fasica (Spotify) | Estado |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        t  = r["track"]
        m  = r.get("match") or {}
        sp_artist = m.get("spotify_artist", "")
        sp_title  = m.get("spotify_title", "")
        if r["status"] == "added":
            estado = "\u2705 adicionado"
        elif r["status"] == "skipped":
            estado = "\u23ed\ufe0f j\u00e1 existe"
        else:
            estado = "\u274c n\u00e3o encontrado"
        summary.append(f"| {t['artist']} | {t['title']} | {sp_artist} | {sp_title} | {estado} |")

    if removed_count:
        summary += ["", f"_{removed_count} tracks antigos removidos para manter limite de {PLAYLIST_LIMIT}._"]

    write_summary(summary)


if __name__ == "__main__":
    try:
        main()
    except QuotaExceededError as exc:
        message = f"Spotify quota excedida — {exc}"
        print(f"\n  {message}")
        write_summary(["## Radio Comercial Live -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(1)
    except RateLimitError as exc:
        message = f"Rate limit Spotify — aguardar aproximadamente {exc.retry_after}s."
        print(f"\n  {message}")
        write_summary(["## Radio Comercial Live -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(1)
