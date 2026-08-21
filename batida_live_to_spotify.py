#!/usr/bin/env python3
"""
Recolhe as musicas tocadas na Batida FM na ultima 1h via API JSON:
  https://listenapi.planetradio.co.uk/api9.2/events/bfm/{datetime}/{count}
"""

import os
import sys
import time
import datetime
import requests
from urllib.parse import quote
from zoneinfo import ZoneInfo
from spotify_client import (
    QuotaExceededError,
    RateLimitError,
    get_access_token,
    get_playlist_uris_cached,
    normalize_track_key,
    search_track_cached,
    spotify_request as spotify,
)

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_PLAYLIST_URL   = "https://api.spotify.com/v1/playlists/{id}"
PLANETRADIO_API        = "https://listenapi.planetradio.co.uk/api9.2/events/bfm/{datetime}/{count}"
API_EVENT_COUNT        = 100
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 1
LISBON_TZ              = ZoneInfo("Europe/Lisbon")


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def fetch_tracks() -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://rayo.pt",
        "Referer": "https://rayo.pt/",
    }
    lisbon_now = datetime.datetime.now(LISBON_TZ)
    cutoff     = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)

    dt_str  = lisbon_now.strftime("%Y-%m-%d %H:%M:%S")
    api_url = PLANETRADIO_API.format(
        datetime=quote(dt_str, safe=""),
        count=API_EVENT_COUNT,
    )
    print(f"  Janela: {cutoff.strftime('%H:%M')} - {lisbon_now.strftime('%H:%M')} Lisboa")
    print(f"  URL: {api_url}")

    resp = requests.get(api_url, headers=headers, timeout=20)
    resp.raise_for_status()

    data   = resp.json()
    events = data if isinstance(data, list) else (
        data.get("events") or data.get("Events") or []
    )
    print(f"  {len(events)} eventos recebidos da API")

    seen, tracks = set(), []
    for ev in events:
        title  = (ev.get("nowPlayingTrack")  or "").strip()
        artist = (ev.get("nowPlayingArtist") or "").strip()
        if not title or not artist:
            continue
        start_raw = (ev.get("nowPlayingTime") or "")
        if start_raw:
            try:
                ev_dt = datetime.datetime.strptime(start_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=LISBON_TZ)
                if ev_dt < cutoff:
                    continue
            except ValueError:
                pass
        key = (artist.upper(), title.upper())
        if key not in seen:
            seen.add(key)
            tracks.append({"artist": artist, "title": title})

    print(f"  {len(tracks)} tracks unicos nas ultimas {WINDOW_HOURS}h")
    return tracks


def search_track(token: str, artist: str, title: str) -> dict | None:
    return search_track_cached(
        token,
        artist,
        title,
        queries=[f'track:"{title}" artist:"{artist}"', f"{artist} {title}"],
        limit=5,
        request_fn=spotify,
    )


def get_playlist_uris(
    token: str, playlist_id: str
) -> tuple[list[str], dict[tuple[str, str], dict[str, str]]]:
    return get_playlist_uris_cached(
        token,
        playlist_id,
        playlist_items_url=SPOTIFY_PLAYLIST_ITEMS,
        playlist_url=SPOTIFY_PLAYLIST_URL,
        request_fn=spotify,
    )


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
    print("=== Batida FM Live -> Spotify ===")

    print("\nA recolher tracks da Batida FM...")
    raw_tracks = fetch_tracks()
    if not raw_tracks:
        print("Nenhum track encontrado na janela de tempo, a sair.")
        write_summary(["## Batida FM Live -> Spotify", "", f"Sem tracks novos nas ultimas {WINDOW_HOURS}h."])
        sys.exit(0)

    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_BATIDA_LIVE_PLAYLIST_ID"]

    print("\nA ler playlist actual...")
    current_uris, playlist_lookup = get_playlist_uris(token, playlist_id)
    current_set  = set(current_uris)
    print(f"  {len(current_uris)} tracks na playlist")

    print(f"\nA pesquisar {len(raw_tracks)} tracks no Spotify...")
    results  = []
    new_uris = []
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
        msg = f"Spotify quota excedida; run adiada — {e}"
        print(f"\n  {msg}")
        write_summary(["## Batida FM Live -> Spotify", "", f"> ⚠️ {msg}"])
        sys.exit(0)
    except RateLimitError as e:
        msg = f"\u23f3 Rate limit atingido \u2014 Spotify pede para aguardar **{e.retry_after}s** antes de tentar de novo."
        print(f"\n  {msg}")
        write_summary(["## Batida FM Live -> Spotify", "", f"> \u26a0\ufe0f {msg}"])
        sys.exit(1)

    added     = [r for r in results if r["status"] == "added"]
    skipped   = [r for r in results if r["status"] == "skipped"]
    not_found = [r for r in results if r["status"] == "not_found"]
    print(f"\nNovos: {len(added)} | Ja na playlist: {len(skipped)} | Nao encontrados: {len(not_found)}")

    slots_needed                = min(len(new_uris), PLAYLIST_LIMIT)
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
        lisbon_str = datetime.datetime.now(LISBON_TZ).strftime("%d/%m/%Y %H:%M")
        update_playlist_description(token, playlist_id, f"Actualizado a {lisbon_str}")
    else:
        print("Sem alterações na playlist; descrição não atualizada.")

    now          = datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

    summary = [
        "## Batida FM Live -> Spotify",
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
        message = f"Spotify quota excedida; run adiada — {exc}"
        print(f"\n  {message}")
        write_summary(["## Batida FM Live -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(0)
    except RateLimitError as exc:
        message = f"Rate limit Spotify — aguardar aproximadamente {exc.retry_after}s."
        print(f"\n  {message}")
        write_summary(["## Batida FM Live -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(1)
