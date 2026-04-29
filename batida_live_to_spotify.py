#!/usr/bin/env python3
"""
Recolhe as musicas tocadas na Batida FM na ultima 1h via API JSON:
  https://listenapi.planetradio.co.uk/api9.2/events/bfm/{datetime}/{count}

Nota: o ultimo parametro e o NUMERO DE EVENTOS a devolver (nao horas).
Pedimos 100 e filtramos por janela temporal localmente.

Estrutura da resposta (array de eventos):
  [
    {
      "nowPlayingTrack":  "Woman",
      "nowPlayingArtist": "Little Simz",
      "nowPlayingTime":   "2026-04-29 23:29:30",
      ...
    },
    ...
  ]
"""

import os
import sys
import time
import datetime
import requests
from urllib.parse import quote

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
PLANETRADIO_API        = "https://listenapi.planetradio.co.uk/api9.2/events/bfm/{datetime}/{count}"
API_EVENT_COUNT        = 100
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 1
MAX_RETRIES            = 5


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
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
    """Executa um pedido ao Spotify com retry automatico em caso de 429."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            print(f"  [429] Rate limit. A aguardar {retry_after}s (tentativa {attempt + 1}/{MAX_RETRIES})...")
            time.sleep(retry_after)
            continue
        if not resp.ok:
            print(f"  HTTP {resp.status_code} {method} {url}: {resp.text[:300]}")
            resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def fetch_tracks() -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://rayo.pt",
        "Referer": "https://rayo.pt/",
    }
    utc_now       = datetime.datetime.now(datetime.UTC)
    lisbon_offset = 2 if 3 < utc_now.month < 11 else 1
    lisbon_now    = (utc_now + datetime.timedelta(hours=lisbon_offset)).replace(tzinfo=None)
    cutoff        = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)

    dt_str  = lisbon_now.strftime("%Y-%m-%d %H:%M:%S")
    api_url = PLANETRADIO_API.format(
        datetime=quote(dt_str, safe=""),
        count=API_EVENT_COUNT,
    )
    print(f"  Janela: {cutoff.strftime('%H:%M')} - {lisbon_now.strftime('%H:%M')} Lisboa")
    print(f"  URL: {api_url}")

    resp = requests.get(api_url, headers=headers, timeout=20)
    resp.raise_for_status()

    data = resp.json()
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
                ev_dt = datetime.datetime.strptime(start_raw[:19], "%Y-%m-%d %H:%M:%S")
                if ev_dt < cutoff:
                    continue
            except ValueError:
                pass
        key = (artist.upper(), title.upper())
        if key not in seen:
            seen.add(key)
            tracks.append({"artist": artist, "title": title})

    print(f"  {len(tracks)} tracks unicos na ultima {WINDOW_HOURS}h")
    return tracks


def search_track(token: str, artist: str, title: str) -> str | None:
    for query in [f'track:"{title}" artist:"{artist}"', f"{artist} {title}"]:
        resp  = spotify("GET", SPOTIFY_SEARCH_URL, token,
                        params={"q": query, "type": "track", "limit": 5, "market": "PT"})
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
        time.sleep(0.1)
    return None


def get_playlist_uris(token: str, playlist_id: str) -> list[str]:
    url    = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    uris   = []
    params = {"limit": 100}
    while url:
        data = spotify("GET", url, token, params=params).json()
        for e in data.get("items", []):
            entry = (e or {}).get("item") or (e or {}).get("track")
            if entry and entry.get("uri") and not entry.get("is_local"):
                uris.append(entry["uri"])
        url    = data.get("next")
        params = {}
    return uris


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


def main() -> None:
    print("=== Batida FM Live -> Spotify ===")

    print("\nA recolher tracks da Batida FM...")
    raw_tracks = fetch_tracks()
    if not raw_tracks:
        print("Nenhum track encontrado na janela de tempo, a sair.")
        write_summary(["## Batida FM Live -> Spotify", "", "Sem tracks novos na ultima 1h."])
        sys.exit(0)

    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_BATIDA_LIVE_PLAYLIST_ID"]

    print("\nA ler playlist actual...")
    current_uris = get_playlist_uris(token, playlist_id)
    current_set  = set(current_uris)
    print(f"  {len(current_uris)} tracks na playlist")

    print(f"\nA pesquisar {len(raw_tracks)} tracks no Spotify...")
    results  = []
    new_uris = []
    for t in raw_tracks:
        uri = search_track(token, t["artist"], t["title"])
        if not uri:
            results.append({"track": t, "status": "not_found"})
            print(f"  \u2717 {t['artist']} - {t['title']}")
        elif uri in current_set:
            results.append({"track": t, "status": "skipped"})
            print(f"  ~ {t['artist']} - {t['title']} (ja existe)")
        else:
            results.append({"track": t, "status": "added", "uri": uri})
            new_uris.append(uri)
            print(f"  \u2713 {t['artist']} - {t['title']}")

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

    now          = datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

    status_label = {"added": "\u2705 adicionado", "skipped": "\u23ed\ufe0f ja existe", "not_found": "\u274c nao encontrado"}
    summary = [
        "## Batida FM Live -> Spotify",
        f"> Actualizado em **{now}** &nbsp;\u2014&nbsp; [Abrir playlist]({playlist_url})",
        "",
        f"**{len(added)}/{len(raw_tracks)} tracks** adicionados &nbsp;|&nbsp; {len(skipped)} ja existentes &nbsp;|&nbsp; {len(not_found)} nao encontrados",
        "",
        "| Artista | Musica | Estado |",
        "|---|---|---|",
    ]
    for r in results:
        t = r["track"]
        summary.append(f"| {t['artist']} | {t['title']} | {status_label[r['status']]} |")

    if removed_count:
        summary += ["", f"_{removed_count} tracks antigos removidos para manter limite de {PLAYLIST_LIMIT}._"]

    write_summary(summary)


if __name__ == "__main__":
    main()
