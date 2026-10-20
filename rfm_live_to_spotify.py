#!/usr/bin/env python3
"""
Scrapa o historial de musicas tocadas na RFM (rfm.pt/que-musica-era)
e adiciona as novas a uma playlist Spotify, mantendo um limite de 300 tracks
e sem duplicados. Corre hora a hora via GitHub Actions.
"""

import os
import re
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
    get_playlist_uris_cached,
    normalize_track_key,
    search_track_cached,
    spotify_request as spotify,
)

SPOTIFY_TOKEN_URL      = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL     = "https://api.spotify.com/v1/search"
SPOTIFY_PLAYLIST_ITEMS = "https://api.spotify.com/v1/playlists/{id}/items"
SPOTIFY_PLAYLIST_URL   = "https://api.spotify.com/v1/playlists/{id}"
RFM_HISTORY_URL        = "https://rfm.pt/que-musica-era"
RFM_HISTORY_SEARCH_URL = "https://rfm.pt/ajax/quemusicaera/getquemusicaera_rfm.aspx"
PLAYLIST_LIMIT         = 300
WINDOW_HOURS           = 1
RFM_TIME_RE            = re.compile(r"\b\d{1,2}:\d{2}\b")
LISBON_TZ              = ZoneInfo("Europe/Lisbon")


class RFMHistoryError(RuntimeError):
    """O historial da RFM não pôde ser obtido ou interpretado."""


def write_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def scrape_current(now: datetime.datetime | None = None) -> list[dict]:
    lisbon_now = now if now is not None else datetime.datetime.now(LISBON_TZ)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; rfm-live-bot/1.0)"}
    try:
        resp = requests.get(RFM_HISTORY_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        histories = [(lisbon_now.date(), resp.text)]
        cutoff = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)
        hour = lisbon_now.replace(minute=0, second=0, microsecond=0)
        first_hour = cutoff.replace(minute=0, second=0, microsecond=0)
        while hour > first_hour:
            hour -= datetime.timedelta(hours=1)
            previous = requests.post(
                RFM_HISTORY_SEARCH_URL,
                headers=headers,
                data={
                    "hora": str(hour.hour),
                    "dia": "today" if hour.date() == lisbon_now.date() else "yesterday",
                },
                timeout=20,
            )
            previous.raise_for_status()
            # The AJAX endpoint returns song blocks, without the page container.
            histories.append((hour.date(), f'<section id="musicList">{previous.text}</section>'))
    except requests.RequestException as exc:
        raise RFMHistoryError(f"Erro ao obter o historial da RFM: {exc}") from exc

    tracks, seen = [], set()
    # Pages and their songs are processed newest first, including across midnight.
    for history_date, html in histories:
        for track in parse_history(html, lisbon_now, history_date=history_date):
            key = normalize_track_key(track["artist"], track["title"])
            if key not in seen:
                seen.add(key)
                tracks.append(track)
    print(f"  {len(tracks)} tracks unicos na ultima {WINDOW_HOURS}h")
    return tracks


def parse_history(
    html: str,
    lisbon_now: datetime.datetime,
    *,
    history_date: datetime.date | None = None,
) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    history = soup.select_one("#musicList")
    if history is None:
        raise RFMHistoryError("Historial #musicList não encontrado; o formato da página pode ter mudado.")

    rows = history.select("ul.g-mx")
    if not rows and (history.find() is not None or history.get_text(strip=True)):
        raise RFMHistoryError("Não foi possível reconhecer os blocos de músicas no historial da RFM.")

    cutoff      = lisbon_now - datetime.timedelta(hours=WINDOW_HOURS)
    timed_tracks = []
    for index, row in enumerate(rows, start=1):
        time_cell = row.select_one(".t-hor")
        description = row.select(".t-desc ul > li")
        if time_cell is None or len(description) != 2:
            raise RFMHistoryError(f"Registo {index}: hora, título ou artista em falta no historial da RFM.")

        time_match = RFM_TIME_RE.fullmatch(time_cell.get_text(" ", strip=True))
        if time_match is None:
            raise RFMHistoryError(f"Registo {index}: hora inválida no historial da RFM.")
        try:
            hour, minute = (int(part) for part in time_match.group().split(":"))
            record_dt = datetime.datetime.combine(
                history_date if history_date is not None else lisbon_now.date(),
                datetime.time(hour, minute),
                tzinfo=LISBON_TZ,
            )
        except ValueError as exc:
            raise RFMHistoryError(f"Registo {index}: hora inválida no historial da RFM.") from exc
        if history_date is None and record_dt > lisbon_now + datetime.timedelta(minutes=5):
            record_dt -= datetime.timedelta(days=1)

        title = description[0].get_text(" ", strip=True)
        artist = description[1].get_text(" ", strip=True)
        if not title or not artist:
            raise RFMHistoryError(f"Registo {index}: título ou artista vazio no historial da RFM.")
        if not cutoff <= record_dt <= lisbon_now + datetime.timedelta(minutes=5):
            continue

        timed_tracks.append((record_dt, {"artist": artist, "title": title}))

    tracks, seen = [], set()
    for _, track in sorted(timed_tracks, key=lambda item: item[0], reverse=True):
        key = normalize_track_key(track["artist"], track["title"])
        if key not in seen:
            seen.add(key)
            tracks.append(track)
    return tracks


def search_track(token: str, artist: str, title: str) -> dict | None:
    return search_track_cached(
        token,
        artist,
        title,
        queries=[f"track:{title} artist:{artist}", f"{artist} {title}"],
        limit=10,
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
        spotify("POST", url, token, json={"uris": uris[i:i + 100], "position": i})
        time.sleep(0.2)


def trim_playlist(token: str, playlist_id: str, current_uris: list[str]) -> tuple[list[str], int]:
    target  = PLAYLIST_LIMIT
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
    print("=== RFM Live -> Spotify ===")

    print("\nA recolher tracks actuais da RFM...")
    try:
        raw_tracks = scrape_current()
    except RFMHistoryError as exc:
        message = f"Falha ao recolher músicas da RFM: {exc}"
        print(message)
        write_summary(["## RFM Live -> Spotify", "", f"> ❌ {message}"])
        sys.exit(1)
    if not raw_tracks:
        message = f"Sem músicas no historial da RFM na última {WINDOW_HOURS}h; leitura concluída sem erros."
        print(message)
        write_summary(["## RFM Live -> Spotify", "", message])
        sys.exit(0)
    print(f"  {len(raw_tracks)} tracks encontrados")

    print("\nA autenticar no Spotify...")
    token       = get_access_token()
    playlist_id = os.environ["SPOTIFY_LIVE_PLAYLIST_ID"]

    print(f"\nA ler playlist actual...")
    current_uris, playlist_lookup = get_playlist_uris(token, playlist_id)
    current_set  = set(current_uris)
    print(f"  {len(current_uris)} tracks na playlist")

    print("\nA pesquisar tracks no Spotify...")
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
                current_set.add(match["uri"])
                print(f"  \u2713 {t['artist']} - {t['title']}")
    except QuotaExceededError as e:
        msg = f"Spotify quota excedida; run adiada — {e}"
        print(f"\n  {msg}")
        write_summary(["## RFM Live -> Spotify", "", f"> ⚠️ {msg}"])
        sys.exit(0)
    except RateLimitError as e:
        msg = f"\u23f3 Rate limit atingido \u2014 Spotify pede para aguardar **{e.retry_after}s** antes de tentar de novo."
        print(f"\n  {msg}")
        write_summary(["## RFM Live -> Spotify", "", f"> \u26a0\ufe0f {msg}"])
        sys.exit(1)

    added     = [r for r in results if r["status"] == "added"]
    skipped   = [r for r in results if r["status"] == "skipped"]
    not_found = [r for r in results if r["status"] == "not_found"]
    print(f"\nNovos: {len(added)} | Ja na playlist: {len(skipped)} | Nao encontrados: {len(not_found)}")

    if new_uris:
        print(f"  A adicionar {len(new_uris)} tracks...")
        add_items(token, playlist_id, new_uris)
        current_uris = new_uris + current_uris
        print("Playlist actualizada!")
    else:
        print("Nenhum track novo para adicionar.")

    # Only remove old songs after every addition has been confirmed.
    # Also finish trimming a previous run that stopped after a successful add.
    current_uris, removed_count = trim_playlist(token, playlist_id, current_uris)

    if new_uris:
        lisbon_str = datetime.datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M")
        update_playlist_description(token, playlist_id, f"Actualizado a {lisbon_str}")
    else:
        print("Sem alterações na playlist; descrição não atualizada.")

    now          = datetime.datetime.now(datetime.UTC).strftime("%d/%m/%Y %H:%M UTC")
    playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

    summary = [
        "## RFM Live -> Spotify",
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
        write_summary(["## RFM Live -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(0)
    except RateLimitError as exc:
        message = f"Rate limit Spotify — aguardar aproximadamente {exc.retry_after}s."
        print(f"\n  {message}")
        write_summary(["## RFM Live -> Spotify", "", f"> ⚠️ {message}"])
        sys.exit(1)
