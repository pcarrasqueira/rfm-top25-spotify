#!/usr/bin/env python3
"""
Scrapa o historial de musicas tocadas na Antena 3 (antena3.rtp.pt/ja-tocou/)
e adiciona as novas a uma playlist Spotify, mantendo um limite de 300 tracks
e sem duplicados. Corre hora a hora via GitHub Actions.

Entradas cujo titulo OU artista coincida com um programa da grelha EPG da Antena 3 sao
automaticamente ignoradas (ex: "Manhãs da 3", "Logo Se Vê", "Portugália", etc.)

Estrutura de cada <li> na pagina: [HH:MM, Titulo, Artista]
O selector aponta so a lista de musicas (ul apos h1 'Ja tocou').
"""

import os
import re
import sys
import time
import datetime
import unicodedata
import requests
from bs4 import BeautifulSoup

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
ANTENA3_URL            = "https://antena3.rtp.pt/ja-tocou/"
EPG_URL                = "https://www.rtp.pt/EPG/json/rtp-channels-page/list-grid/radio/3/{date}"
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 3
TIME_RE                = re.compile(r"^\d{1,2}:\d{2}$")
DEBUG                  = os.environ.get("A3_DEBUG", "").lower() in ("1", "true", "yes")


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def normalize(text: str) -> str:
    """Lowercase + remove acentos para comparacao robusta."""
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
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if not resp.ok:
        print(f"  HTTP {resp.status_code} {method} {url}: {resp.text[:300]}")
        resp.raise_for_status()
    return resp


def fetch_tracks() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    utc_now       = datetime.datetime.now(datetime.UTC)
    lisbon_offset = 1 if 3 < utc_now.month < 11 else 0  # WEST=UTC+1 verao, WET=UTC+0 inverno
    lisbon_now    = (utc_now + datetime.timedelta(hours=lisbon_offset)).replace(tzinfo=None)
    cutoff        = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)
    print(f"  Janela: {cutoff.strftime('%H:%M')} - {lisbon_now.strftime('%H:%M')} Lisboa")

    epg_programs = get_epg_program_names(lisbon_now.date())

    resp = requests.get(ANTENA3_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Encontrar o <ul> especifico da lista 'Ja tocou' (logo apos o <h1>)
    song_list = None
    for h1 in soup.find_all("h1"):
        if "tocou" in h1.get_text(strip=True).lower():
            song_list = h1.find_next_sibling("ul")
            break

    if not song_list:
        # Fallback: primeiro <ul> com pelo menos um <li> que contenha um HH:MM
        print("  [WARN] Nao encontrei <h1>Ja tocou</h1> - a usar fallback")
        for ul in soup.find_all("ul"):
            lis = ul.find_all("li", recursive=False)
            if any(TIME_RE.search(li.get_text()) for li in lis):
                song_list = ul
                break

    if not song_list:
        print("  [ERROR] Lista de musicas nao encontrada na pagina")
        return []

    seen        = set()
    tracks      = []
    skipped_epg = 0
    total_li    = 0

    for li in song_list.find_all("li", recursive=False):
        parts    = [t.strip() for t in li.stripped_strings]
        total_li += 1

        # Campo de hora (HH:MM) normalmente em parts[0]
        time_idx = next((i for i, p in enumerate(parts) if TIME_RE.match(p)), None)
        if time_idx is None:
            if DEBUG:
                print(f"  [DBG] sem hora: {parts}")
            continue

        time_str  = parts[time_idx]
        remaining = [p for i, p in enumerate(parts) if i != time_idx]
        if not remaining:
            continue

        # Estrutura: [HH:MM, Titulo, Artista]
        title  = remaining[0]
        artist = remaining[1] if len(remaining) > 1 else ""

        if DEBUG:
            print(f"  [DBG] {time_str} | titulo={title!r} | artista={artist!r}")

        if epg_programs and (
            normalize(title)  in epg_programs or
            (artist and normalize(artist) in epg_programs)
        ):
            skipped_epg += 1
            if DEBUG:
                print(f"  [DBG] EPG filtrado: {title!r} / {artist!r}")
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
            if DEBUG:
                print(f"  [DBG] fora janela ({rec_dt.strftime('%H:%M')} < {cutoff.strftime('%H:%M')}): {title!r}")
            continue

        key = (title.upper(), artist.upper())
        if key not in seen:
            seen.add(key)
            tracks.append({"artist": artist, "title": title})

    if DEBUG:
        print(f"  [DBG] total <li> processados: {total_li}")
    if skipped_epg:
        print(f"  {skipped_epg} entradas ignoradas (programas EPG)")
    print(f"  {len(tracks)} tracks unicos nas ultimas {WINDOW_HOURS}h")
    return tracks


def search_track(token: str, artist: str, title: str) -> str | None:
    if artist:
        queries = [f'track:"{title}" artist:"{artist}"', f"{artist} {title}"]
    else:
        queries = [f'track:"{title}"', title]
    for query in queries:
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
    print("=== Antena 3 Live -> Spotify ===")

    print("\nA recolher tracks da Antena 3...")
    raw_tracks = fetch_tracks()
    if not raw_tracks:
        print("Nenhum track encontrado na janela de tempo, a sair.")
        write_summary(["## Antena 3 Live -> Spotify", "", "Sem tracks novos nas ultimas 3h."])
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
        "## Antena 3 Live -> Spotify",
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
