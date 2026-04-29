#!/usr/bin/env python3
"""
Recolhe as musicas tocadas na Radio Comercial nas ultimas 2h via API JSON:
  https://radiocomercial.pt/now_playing_logs/json/radio-comercial_YYYY-MM-DD.json

O campo DATE de cada registo esta em hora de Lisboa (Europe/Lisbon).
Filtramos tudo com DATE >= agora - 2h para corresponder ao intervalo
entre execucoes hora-a-hora (com margem de 1h extra para sobreposicao).
"""

import os
import sys
import time
import datetime
import requests

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
COMERCIAL_LOG_URL      = "https://radiocomercial.pt/now_playing_logs/json/radio-comercial_{date}.json"
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 2   # janela de tempo a processar


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
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if not resp.ok:
        print(f"  HTTP {resp.status_code} {method} {url}: {resp.text[:300]}")
        resp.raise_for_status()
    return resp


def fetch_tracks() -> list[dict]:
    """
    Busca o JSON do dia actual e filtra os registos das ultimas WINDOW_HOURS horas.
    O campo DATE esta em hora local de Lisboa (sem timezone).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Referer": "https://radiocomercial.pt/passou",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    utc_now       = datetime.datetime.utcnow()
    lisbon_offset = 2 if 3 < utc_now.month < 11 else 1
    lisbon_now    = utc_now + datetime.timedelta(hours=lisbon_offset)
    cutoff        = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)

    date_str = lisbon_now.strftime("%Y-%m-%d")
    url      = COMERCIAL_LOG_URL.format(date=date_str)

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    records = resp.json().get("NOW_PLAYING_LOG", {}).get("NOW_PLAYING_RECORD", [])
    print(f"  {len(records)} registos totais hoje ({date_str})")
    print(f"  Janela: {cutoff.strftime('%H:%M')} - {lisbon_now.strftime('%H:%M')} Lisboa")

    seen   = set()
    tracks = []
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

    print(f"  {len(tracks)} tracks unicos nas ultimas {WINDOW_HOURS}h")
    return tracks


def search_track(token: str, artist: str, title: str) -> str | None:
    for query in [f'track:"{title}" artist:"{artist}"', f"{artist} {title}"]:
        resp  = spotify("GET", SPOTIFY_SEARCH_URL, token,
                        params={"q": query, "type": "track", "limit": 5, "market": "PT"})
        items = resp.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["uri"]
        time.sleep(0.05)
    return None


def get_playlist_uris(token: str, playlist_id: str) -> list[str]:
    url    = SPOTIFY_PLAYLIST_ITEMS.format(id=playlist_id)
    uris   = []
    params = {"fields": "next,items(track(uri))", "limit": 100}
    while url:
        data = spotify("GET", url, token, params=params).json()
        for e in data.get("items", []):
            track = e.get("track")
            if track and track.get("uri"):
                uris.append(track["uri"])
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


def main() -> None:
    print("=== Radio Comercial Live -> Spotify ===")

    print("\nA recolher tracks da API...")
    raw_tracks = fetch_tracks()
    if not raw_tracks:
        print("Nenhum track encontrado na janela de tempo, a sair.")
        write_summary([
            "## Radio Comercial Live -> Spotify",
            f"> {datetime.datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')}",
            "",
            "Sem tracks novos nas ultimas 2h.",
        ])
        sys.exit(0)

    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_COMERCIAL_LIVE_PLAYLIST_ID"]

    print("\nA ler playlist actual...")
    current_uris = get_playlist_uris(token, playlist_id)
    current_set  = set(current_uris)
    print(f"  {len(current_uris)} tracks na playlist")

    print(f"\nA pesquisar {len(raw_tracks)} tracks no Spotify...")
    new_uris  = []
    added     = []
    skipped   = []
    not_found = []
    for t in raw_tracks:
        uri = search_track(token, t["artist"], t["title"])
        if not uri:
            not_found.append(t)
            print(f"  \u2717 {t['artist']} - {t['title']}")
        elif uri in current_set:
            skipped.append(t)
        else:
            new_uris.append(uri)
            added.append(t)
            print(f"  \u2713 {t['artist']} - {t['title']}")

    print(f"\nNovos: {len(added)} | Ja na playlist: {len(skipped)} | Nao encontrados: {len(not_found)}")

    removed_count = 0
    if new_uris:
        # Garantir que new_uris nao excede o limite por si so
        if len(new_uris) > PLAYLIST_LIMIT:
            print(f"  Trimming new_uris de {len(new_uris)} para {PLAYLIST_LIMIT}")
            new_uris = new_uris[:PLAYLIST_LIMIT]
            added    = added[:PLAYLIST_LIMIT]

        # Calcular quantos tracks antigos remover para caber dentro do limite
        total_after = len(current_uris) + len(new_uris)
        if total_after > PLAYLIST_LIMIT:
            overflow      = total_after - PLAYLIST_LIMIT
            # overflow nunca pode exceder o que existe na playlist
            overflow      = min(overflow, len(current_uris))
            to_remove     = current_uris[-overflow:]   # os mais antigos (fim da lista)
            removed_count = len(to_remove)
            print(f"  A remover {removed_count} tracks antigos (limite {PLAYLIST_LIMIT})")
            remove_items(token, playlist_id, to_remove)

        print(f"  A adicionar {len(new_uris)} tracks...")
        add_items(token, playlist_id, new_uris)
        print("Playlist actualizada!")
    else:
        print("Nenhum track novo para adicionar.")

    now          = datetime.datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
    summary = [
        "## Radio Comercial Live -> Spotify",
        f"> {now} | [Abrir playlist]({playlist_url})",
        "",
        "| Novos | Ja existentes | Nao encontrados |",
        "|---|---|---|",
        f"| {len(added)} | {len(skipped)} | {len(not_found)} |",
        "",
    ]
    if added:
        summary += [
            "### Adicionados",
            "| Artista | Musica |",
            "|---|---|",
        ] + [f"| {t['artist']} | {t['title']} |" for t in added] + [""]

    if not_found:
        summary += ["### Nao encontrados no Spotify"] + \
                   [f"- {t['artist']} - {t['title']}" for t in not_found] + [""]

    if removed_count:
        summary.append(f"_{removed_count} tracks antigos removidos para manter limite de {PLAYLIST_LIMIT}._")

    write_summary(summary)


if __name__ == "__main__":
    main()
