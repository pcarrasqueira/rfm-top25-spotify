#!/usr/bin/env python3
"""
Scrapa o historial de musicas tocadas na Antena 3 (antena3.rtp.pt/ja-tocou/)
e adiciona as novas a uma playlist Spotify, mantendo um limite de 300 tracks
e sem duplicados. Corre hora a hora via GitHub Actions.

Entradas cujo titulo OU artista coincida com um programa da grelha EPG da Antena 3 sao
automaticamente ignoradas (ex: "Manhãs da 3", "Logo Se Vê", "Portugália", etc.)

Entradas sem artista sao ignoradas.

Estrutura de cada <li> na pagina: ['HH:MM', 'HH:MM', 'Titulo', 'Artista']
O horario aparece duplicado no HTML - filtramos todos os tokens HH:MM.
O UL das musicas tem 100+ items directos com horas.
"""

import os
import re
import sys
import time
import datetime
import unicodedata
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_PLAYLIST_URL   = "https://api.spotify.com/v1/playlists/{id}"
ANTENA3_URL            = "https://antena3.rtp.pt/ja-tocou/"
EPG_URL                = "https://www.rtp.pt/EPG/json/rtp-channels-page/list-grid/radio/3/{date}"
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 1
TIME_RE                = re.compile(r"^\d{1,2}:\d{2}$")
TIME_RE_IN             = re.compile(r"\d{1,2}:\d{2}")
DEBUG                  = os.environ.get("A3_DEBUG", "").lower() in ("1", "true", "yes")


class RateLimitError(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Spotify rate limit: aguardar {retry_after}s")
        self.retry_after = retry_after


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def get_epg_program_names(lisbon_date: datetime.date) -> set[str]:
    date_str = lisbon_date.strftime("%d-%m-%Y")
    url      = EPG_URL.format(date=date_str)
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [EPG] Aviso: nao foi possivel obter a grelha ({exc})")
        return set()

    names   = set()
    result  = data.get("result", {})
    periods = list(result.values()) if isinstance(result, dict) else []
    for period in periods:
        if not isinstance(period, list):
            continue
        for entry in period:
            if name := entry.get("name", "").strip():
                names.add(normalize(name))
            for sub in entry.get("sub_program", []):
                if sub_name := sub.get("name", "").strip():
                    names.add(normalize(sub_name))

    print(f"  [EPG] {len(names)} programas carregados para {date_str}")
    return names


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
    for attempt in range(5):
        resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            print(f"  HTTP 429 {method} {url}: Too many requests, aguardar {retry_after}s")
            time.sleep(retry_after)
            continue
        if not resp.ok:
            print(f"  HTTP {resp.status_code} {method} {url}: {resp.text[:300]}")
            resp.raise_for_status()
        return resp
    raise RateLimitError(60)


def get_song_ul(soup: BeautifulSoup):
    for ul in soup.find_all("ul"):
        lis = ul.find_all("li", recursive=False)
        if len(lis) > 50:
            sample = [li.get_text(strip=True) for li in lis[:5]]
            if any(TIME_RE_IN.search(t) for t in sample):
                return ul
    return None


def fetch_tracks() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    utc_now       = datetime.datetime.now(datetime.UTC)
    lisbon_offset = 1 if 3 < utc_now.month < 11 else 0
    lisbon_now    = (utc_now + datetime.timedelta(hours=lisbon_offset)).replace(tzinfo=None)
    cutoff        = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)
    print(f"  Janela: {cutoff.strftime('%H:%M')} - {lisbon_now.strftime('%H:%M')} Lisboa")

    epg_programs = get_epg_program_names(lisbon_now.date())

    resp = requests.get(ANTENA3_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup      = BeautifulSoup(resp.text, "lxml")
    song_list = get_song_ul(soup)

    if not song_list:
        print("  [ERROR] Lista de musicas nao encontrada na pagina")
        return []

    seen              = set()
    tracks            = []
    skipped_epg       = 0
    skipped_no_artist = 0

    for li in song_list.find_all("li", recursive=False):
        parts = [t.strip() for t in li.stripped_strings]

        time_tokens    = [p for p in parts if TIME_RE.match(p)]
        content_tokens = [p for p in parts if not TIME_RE.match(p)]

        if not time_tokens or not content_tokens:
            continue

        time_str = time_tokens[0]
        title    = content_tokens[0]
        artist   = content_tokens[1] if len(content_tokens) > 1 else ""

        if DEBUG:
            print(f"  [DBG] {time_str} | titulo={title!r} | artista={artist!r}")

        if not artist:
            skipped_no_artist += 1
            continue

        if epg_programs and (
            normalize(title) in epg_programs or
            normalize(artist) in epg_programs
        ):
            skipped_epg += 1
            continue

        try:
            rec_dt = datetime.datetime.strptime(
                f"{lisbon_now.strftime('%Y-%m-%d')} {time_str}", "%Y-%m-%d %H:%M"
            )
            if rec_dt > lisbon_now:
                rec_dt -= datetime.timedelta(days=1)
        except ValueError:
            continue

        if rec_dt < cutoff:
            continue

        key = (title.upper(), artist.upper())
        if key not in seen:
            seen.add(key)
            tracks.append({"artist": artist, "title": title})

    if skipped_no_artist:
        print(f"  {skipped_no_artist} entradas ignoradas (sem artista)")
    if skipped_epg:
        print(f"  {skipped_epg} entradas ignoradas (programas EPG)")
    print(f"  {len(tracks)} tracks unicos na ultima {WINDOW_HOURS}h")
    return tracks


def search_track(token: str, artist: str, title: str) -> dict | None:
    for query in [f'track:"{title}" artist:"{artist}"', f"{artist} {title}"]:
        resp  = spotify("GET", SPOTIFY_SEARCH_URL, token,
                        params={"q": query, "type": "track", "limit": 5, "market": "PT"})
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


def update_playlist_description(token: str, playlist_id: str, description: str) -> None:
    url = SPOTIFY_PLAYLIST_URL.format(id=playlist_id)
    spotify("PUT", url, token, json={"description": description})


def main() -> None:
    print("=== Antena 3 Live -> Spotify ===")

    print("\nA recolher tracks da Antena 3...")
    raw_tracks = fetch_tracks()
    if not raw_tracks:
        print("Nenhum track encontrado na janela de tempo, a sair.")
        write_summary(["## Antena 3 Live -> Spotify", "", "Sem tracks novos na ultima 1h."])
        sys.exit(0)

    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_ANTENA3_LIVE_PLAYLIST_ID"]

    print("\nA ler playlist actual...")
    current_uris = get_playlist_uris(token, playlist_id)
    current_set  = set(current_uris)
    print(f"  {len(current_uris)} tracks na playlist")

    print(f"\nA pesquisar {len(raw_tracks)} tracks no Spotify...")
    results  = []
    new_uris = []
    try:
        for t in raw_tracks:
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
    except RateLimitError as e:
        msg = f"\u23f3 Rate limit atingido \u2014 Spotify pede para aguardar **{e.retry_after}s** antes de tentar de novo."
        print(f"\n  {msg}")
        write_summary(["## Antena 3 Live -> Spotify", "", f"> \u26a0\ufe0f {msg}"])
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

    lisbon_str = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
    update_playlist_description(token, playlist_id, f"Actualizado a {lisbon_str}")

    now          = datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

    summary = [
        "## Antena 3 Live -> Spotify",
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
    main()
