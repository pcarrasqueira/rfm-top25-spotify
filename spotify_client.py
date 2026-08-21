"""Shared Spotify Web API helpers.

The Web API rate limit is shared by every workflow using the same Spotify app,
so retry behaviour must be consistent across all scripts.
"""

from __future__ import annotations

import datetime
import email.utils
import json
import math
import os
from pathlib import Path
import random
import time
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

import requests


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 3
MAX_JITTER_SECONDS = 0.25
TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})
STATE_DIR_ENV = "SPOTIFY_STATE_DIR"
STATE_VERSION = 2
SEARCH_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60
NEGATIVE_SEARCH_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_QUOTA_BLOCK_SECONDS = 6 * 60 * 60
SEARCH_CACHE_FILENAME = "search-cache.json"
PLAYLIST_CACHE_FILENAME = "playlist-cache.json"
QUOTA_STATE_FILENAME = "quota-state.json"


class SpotifyAPIError(RuntimeError):
    """Base error carrying the useful context from a Spotify response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        method: str | None = None,
        url: str | None = None,
        reason: str | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.method = method
        self.url = url
        self.reason = reason
        self.body = body


class RateLimitError(SpotifyAPIError):
    """A temporary 429 after the retry budget was exhausted."""

    def __init__(self, retry_after: float, **kwargs: Any) -> None:
        self.retry_after = max(1, int(round(retry_after)))
        super().__init__(
            f"Spotify rate limit: aguardar aproximadamente {self.retry_after}s",
            **kwargs,
        )


class QuotaExceededError(SpotifyAPIError):
    """A development-mode quota 429 that should not be retried blindly."""

    def __init__(
        self,
        retry_after: float | None = None,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.retry_after = (
            max(1, int(math.ceil(retry_after)))
            if retry_after is not None
            else None
        )
        super().__init__(
            message
            or "Spotify development quota excedida (QUOTA_EXCEEDED); retries adiados",
            **kwargs,
        )


class QuotaCircuitOpenError(QuotaExceededError):
    """A persisted quota block prevents another request from being attempted."""

    def __init__(self, blocked_until: float, **kwargs: Any) -> None:
        remaining = max(1, int(math.ceil(blocked_until - time.time())))
        self.blocked_until = blocked_until
        super().__init__(
            retry_after=remaining,
            message=(
                "Spotify quota em pausa até "
                f"{datetime.datetime.fromtimestamp(blocked_until, datetime.UTC):%d/%m/%Y %H:%M UTC}"
            ),
            status_code=429,
            reason="QUOTA_EXCEEDED",
            **kwargs,
        )


def _state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV, ".cache/spotify"))


def _state_path(filename: str) -> Path:
    return _state_dir() / filename


def _read_state(filename: str) -> dict[str, Any]:
    path = _state_path(filename)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(filename: str, payload: dict[str, Any]) -> None:
    path = _state_path(filename)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as exc:
        # A cache must never make a playlist update fail.
        print(f"  Aviso: não foi possível guardar estado Spotify: {exc}")


def _remove_state(filename: str) -> None:
    try:
        _state_path(filename).unlink(missing_ok=True)
    except OSError as exc:
        print(f"  Aviso: não foi possível limpar estado Spotify: {exc}")


def _quota_block_remaining(now: float | None = None) -> tuple[float, float] | None:
    payload = _read_state(QUOTA_STATE_FILENAME)
    try:
        blocked_until = float(payload.get("blocked_until", 0))
    except (TypeError, ValueError):
        blocked_until = 0
    current_time = time.time() if now is None else now
    if blocked_until > current_time:
        return blocked_until, blocked_until - current_time
    if payload:
        _remove_state(QUOTA_STATE_FILENAME)
    return None


def record_quota_block(retry_after: float | None) -> float:
    """Persist a shared cooldown after Spotify reports QUOTA_EXCEEDED."""

    current_time = time.time()
    requested_delay = (
        retry_after
        if retry_after is not None and retry_after > 0
        else DEFAULT_QUOTA_BLOCK_SECONDS
    )
    requested_delay = max(60.0, requested_delay)
    existing = _quota_block_remaining(current_time)
    existing_until = existing[0] if existing else 0.0
    blocked_until = max(existing_until, current_time + requested_delay)
    _write_state(
        QUOTA_STATE_FILENAME,
        {
            "version": STATE_VERSION,
            "blocked_until": blocked_until,
            "recorded_at": current_time,
            "retry_after": int(math.ceil(requested_delay)),
            "reason": "QUOTA_EXCEEDED",
        },
    )
    return blocked_until


def ensure_quota_available() -> None:
    """Raise before making any Spotify call while the shared cooldown is active."""

    blocked = _quota_block_remaining()
    if blocked:
        blocked_until, _remaining = blocked
        raise QuotaCircuitOpenError(blocked_until)


def _response_body(response: requests.Response) -> str:
    return (response.text or "").strip()[:500]


def _error_reason(response: requests.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error.get("reason") if isinstance(error, dict) else None


def parse_retry_after(value: str | None) -> float | None:
    """Parse Spotify's seconds value, with support for HTTP-date fallback."""

    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (parsed - now).total_seconds())


def _normal_retry_delay(attempt: int, retry_after: str | None) -> float:
    parsed = parse_retry_after(retry_after)
    if parsed is not None:
        return max(1.0, parsed)
    return float(min(2**attempt, 30))


def _sleep_before_retry(
    *,
    attempt: int,
    max_retries: int,
    retry_after: str | None,
    sleep_fn: Callable[[float], None],
    jitter_fn: Callable[[float, float], float],
    message: str,
) -> None:
    delay = _normal_retry_delay(attempt, retry_after)
    jitter = max(0.0, jitter_fn(0.0, MAX_JITTER_SECONDS))
    sleep_for = delay + jitter
    print(
        f"  {message}; tentativa {attempt + 1}/{max_retries + 1}. "
        f"A aguardar {sleep_for:.2f}s antes da próxima tentativa..."
    )
    sleep_fn(sleep_for)


def _log_rate_limit(
    method: str,
    url: str,
    response: requests.Response,
    attempt: int,
    max_retries: int,
) -> tuple[str | None, str]:
    reason = _error_reason(response)
    retry_header = response.headers.get("Retry-After")
    print(
        f"  HTTP 429 {method} {url}: attempt {attempt + 1}/{max_retries + 1} "
        f"reason={reason or 'unknown'} "
        f"Retry-After={retry_header or 'missing'}"
    )
    body = _response_body(response)
    if body:
        print(f"  429 body: {body}")
    return reason, retry_header or ""


def spotify_request(
    method: str,
    url: str,
    token: str,
    *,
    max_retries: int = MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
    **kwargs: Any,
) -> requests.Response:
    """Call the Web API, retrying temporary rate limits and server failures."""

    ensure_quota_available()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    headers.update(kwargs.pop("headers", {}))

    for attempt in range(max_retries + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_retries:
                print(f"  {type(exc).__name__} {method} {url}: retries esgotados")
                raise
            _sleep_before_retry(
                attempt=attempt,
                max_retries=max_retries,
                retry_after=None,
                sleep_fn=sleep_fn,
                jitter_fn=jitter_fn,
                message=f"{type(exc).__name__} ao chamar {method} {url}",
            )
            continue

        if response.status_code != 429:
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < max_retries:
                retry_header = response.headers.get("Retry-After")
                body = _response_body(response)
                print(
                    f"  HTTP {response.status_code} {method} {url}: "
                    f"tentativa {attempt + 1}/{max_retries + 1}"
                )
                if body:
                    print(f"  Response body: {body}")
                _sleep_before_retry(
                    attempt=attempt,
                    max_retries=max_retries,
                    retry_after=retry_header,
                    sleep_fn=sleep_fn,
                    jitter_fn=jitter_fn,
                    message=f"erro transitório HTTP {response.status_code}",
                )
                continue

            if not response.ok:
                body = _response_body(response)
                print(f"  HTTP {response.status_code} {method} {url}")
                if body:
                    print(f"  Response body: {body}")
                response.raise_for_status()
            return response

        reason, retry_header = _log_rate_limit(method, url, response, attempt, max_retries)
        body = _response_body(response)
        error_context = {
            "status_code": response.status_code,
            "method": method,
            "url": url,
            "reason": reason,
            "body": body,
        }
        if reason == "QUOTA_EXCEEDED":
            retry_after = parse_retry_after(retry_header or None)
            record_quota_block(retry_after)
            raise QuotaExceededError(retry_after=retry_after, **error_context)

        if attempt >= max_retries:
            delay = _normal_retry_delay(attempt, retry_header or None)
            raise RateLimitError(delay, **error_context)

        _sleep_before_retry(
            attempt=attempt,
            max_retries=max_retries,
            retry_after=retry_header or None,
            sleep_fn=sleep_fn,
            jitter_fn=jitter_fn,
            message="rate limit Spotify",
        )

    raise AssertionError("unreachable")


def get_access_token(
    *,
    max_retries: int = MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
) -> str:
    """Refresh the OAuth token with bounded handling of transient failures."""

    ensure_quota_available()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"],
    }
    auth = (
        os.environ["SPOTIFY_CLIENT_ID"],
        os.environ["SPOTIFY_CLIENT_SECRET"],
    )

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                SPOTIFY_TOKEN_URL,
                data=data,
                auth=auth,
                timeout=timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_retries:
                print(f"  {type(exc).__name__} ao obter token: retries esgotados")
                raise
            _sleep_before_retry(
                attempt=attempt,
                max_retries=max_retries,
                retry_after=None,
                sleep_fn=sleep_fn,
                jitter_fn=jitter_fn,
                message=f"{type(exc).__name__} ao obter o token",
            )
            continue

        if response.status_code != 429:
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < max_retries:
                retry_header = response.headers.get("Retry-After")
                body = _response_body(response)
                print(
                    f"  HTTP {response.status_code} ao obter token: "
                    f"tentativa {attempt + 1}/{max_retries + 1}"
                )
                if body:
                    print(f"  Token response body: {body}")
                _sleep_before_retry(
                    attempt=attempt,
                    max_retries=max_retries,
                    retry_after=retry_header,
                    sleep_fn=sleep_fn,
                    jitter_fn=jitter_fn,
                    message=f"erro transitório HTTP {response.status_code} ao obter o token",
                )
                continue

            if not response.ok:
                body = _response_body(response)
                print(f"  HTTP {response.status_code} ao obter token")
                if body:
                    print(f"  Token response body: {body}")
                response.raise_for_status()
            return response.json()["access_token"]

        reason, retry_header = _log_rate_limit(
            "POST", SPOTIFY_TOKEN_URL, response, attempt, max_retries
        )
        body = _response_body(response)
        error_context = {
            "status_code": response.status_code,
            "method": "POST",
            "url": SPOTIFY_TOKEN_URL,
            "reason": reason,
            "body": body,
        }
        if reason == "QUOTA_EXCEEDED":
            retry_after = parse_retry_after(retry_header or None)
            record_quota_block(retry_after)
            raise QuotaExceededError(retry_after=retry_after, **error_context)

        if attempt >= max_retries:
            delay = _normal_retry_delay(attempt, retry_header or None)
            raise RateLimitError(delay, **error_context)
        _sleep_before_retry(
            attempt=attempt,
            max_retries=max_retries,
            retry_after=retry_header or None,
            sleep_fn=sleep_fn,
            jitter_fn=jitter_fn,
            message="rate limit ao renovar o token",
        )

    raise AssertionError("unreachable")


def normalize_track_key(artist: str, title: str) -> tuple[str, str]:
    """Build a forgiving key for matching radio metadata to playlist items."""

    def normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.lower().strip())
        without_accents = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(without_accents.split())

    return normalize(artist), normalize(title)


def _track_cache_key(artist: str, title: str, market: str) -> str:
    normalized_artist, normalized_title = normalize_track_key(artist, title)
    return f"{market.upper()}\x1f{normalized_artist}\x1f{normalized_title}"


def _search_cache_lookup(
    artist: str,
    title: str,
    market: str,
    now: float | None = None,
) -> tuple[bool, dict[str, str] | None]:
    payload = _read_state(SEARCH_CACHE_FILENAME)
    if payload.get("version") != STATE_VERSION:
        return False, None

    entries = payload.get("tracks")
    if not isinstance(entries, dict):
        return False, None
    entry = entries.get(_track_cache_key(artist, title, market))
    if not isinstance(entry, dict):
        return False, None

    try:
        cached_at = float(entry.get("cached_at", 0))
    except (TypeError, ValueError):
        return False, None
    current_time = time.time() if now is None else now
    found = entry.get("found", True) is not False
    ttl = SEARCH_CACHE_TTL_SECONDS if found else NEGATIVE_SEARCH_CACHE_TTL_SECONDS
    if cached_at <= 0 or current_time - cached_at > ttl:
        return False, None
    if not found:
        return True, None

    uri = entry.get("uri")
    spotify_artist = entry.get("spotify_artist")
    spotify_title = entry.get("spotify_title")
    if not all(isinstance(value, str) and value for value in (uri, spotify_artist, spotify_title)):
        return False, None
    return True, {
        "uri": uri,
        "spotify_artist": spotify_artist,
        "spotify_title": spotify_title,
    }


def _save_search_result(
    artist: str,
    title: str,
    market: str,
    match: dict[str, str] | None,
) -> None:
    payload = _read_state(SEARCH_CACHE_FILENAME)
    if payload.get("version") != STATE_VERSION:
        payload = {"version": STATE_VERSION, "tracks": {}}
    entries = payload.setdefault("tracks", {})
    if not isinstance(entries, dict):
        entries = {}
        payload["tracks"] = entries

    entry: dict[str, Any] = {
        "cached_at": time.time(),
        "found": match is not None,
    }
    if match:
        entry.update(
            {
                "uri": match["uri"],
                "spotify_artist": match["spotify_artist"],
                "spotify_title": match["spotify_title"],
            }
        )
    entries[_track_cache_key(artist, title, market)] = entry
    _write_state(SEARCH_CACHE_FILENAME, payload)


def search_track_cached(
    token: str,
    artist: str,
    title: str,
    *,
    queries: Iterable[str],
    limit: int,
    market: str = "PT",
    sleep_seconds: float = 0.3,
    request_fn: Callable[..., requests.Response] | None = None,
) -> dict[str, str] | None:
    """Search Spotify once and reuse the result across all scheduled workflows."""

    cache_hit, cached_match = _search_cache_lookup(artist, title, market)
    if cache_hit:
        if cached_match:
            print(f"  ↺ {artist} - {title} (cache persistente Spotify)")
        else:
            print(f"  ↺ {artist} - {title} (não encontrado; cache persistente Spotify)")
        return cached_match

    request = request_fn or spotify_request
    for query in queries:
        response = request(
            "GET",
            "https://api.spotify.com/v1/search",
            token,
            params={"q": query, "type": "track", "limit": limit, "market": market},
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        items = response.json().get("tracks", {}).get("items", [])
        if not items:
            continue
        item = items[0]
        uri = item.get("uri")
        if not uri:
            continue
        artists = item.get("artists") or []
        spotify_artist = artists[0].get("name") if artists and isinstance(artists[0], dict) else artist
        match = {
            "uri": uri,
            "spotify_artist": spotify_artist or artist,
            "spotify_title": item.get("name") or title,
        }
        _save_search_result(artist, title, market, match)
        return match

    _save_search_result(artist, title, market, None)
    return None


def playlist_entry_to_match(
    entry: dict[str, Any] | None,
) -> tuple[dict[str, str], list[tuple[str, str]]] | None:
    """Extract a searchable radio-to-Spotify match from a playlist item."""

    if not isinstance(entry, dict) or entry.get("is_local"):
        return None
    uri = entry.get("uri")
    title = (entry.get("name") or "").strip()
    raw_artists = entry.get("artists") or []
    artists = [
        (artist.get("name") or "").strip()
        for artist in raw_artists
        if isinstance(artist, dict) and artist.get("name")
    ]
    if not uri or not title or not artists:
        return None

    match = {
        "uri": uri,
        "spotify_artist": artists[0],
        "spotify_title": title,
    }
    keys = [normalize_track_key(artist, title) for artist in artists]
    return match, keys


def _playlist_cache_lookup(
    playlist_id: str,
    snapshot_id: str | None,
) -> tuple[list[str], dict[tuple[str, str], dict[str, str]]] | None:
    if not snapshot_id:
        return None
    payload = _read_state(PLAYLIST_CACHE_FILENAME)
    if payload.get("version") != STATE_VERSION:
        return None
    playlists = payload.get("playlists")
    if not isinstance(playlists, dict):
        return None
    entry = playlists.get(playlist_id)
    if not isinstance(entry, dict) or entry.get("snapshot_id") != snapshot_id:
        return None

    uris: list[str] = []
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    entries = entry.get("entries")
    if not isinstance(entries, list):
        return None
    for cached_entry in entries:
        if not isinstance(cached_entry, dict):
            continue
        match = cached_entry.get("match")
        if not isinstance(match, dict):
            continue
        uri = match.get("uri")
        if not isinstance(uri, str) or not uri:
            continue
        uris.append(uri)
        keys = cached_entry.get("keys") or []
        for key in keys:
            if isinstance(key, list) and len(key) == 2 and all(isinstance(part, str) for part in key):
                lookup[(key[0], key[1])] = match
    return uris, lookup


def _save_playlist_cache(
    playlist_id: str,
    snapshot_id: str | None,
    entries: list[dict[str, Any]],
) -> None:
    payload = _read_state(PLAYLIST_CACHE_FILENAME)
    if payload.get("version") != STATE_VERSION:
        payload = {"version": STATE_VERSION, "playlists": {}}
    playlists = payload.setdefault("playlists", {})
    if not isinstance(playlists, dict):
        playlists = {}
        payload["playlists"] = playlists
    playlists[playlist_id] = {
        "snapshot_id": snapshot_id,
        "cached_at": time.time(),
        "entries": entries,
    }
    _write_state(PLAYLIST_CACHE_FILENAME, payload)


def get_playlist_uris_cached(
    token: str,
    playlist_id: str,
    *,
    playlist_items_url: str,
    playlist_url: str,
    request_fn: Callable[..., requests.Response] | None = None,
) -> tuple[list[str], dict[tuple[str, str], dict[str, str]]]:
    """Reuse playlist items when Spotify's snapshot_id is unchanged."""

    request = request_fn or spotify_request
    metadata = request(
        "GET",
        playlist_url.format(id=playlist_id),
        token,
        params={"fields": "snapshot_id"},
    ).json()
    snapshot_id = metadata.get("snapshot_id") if isinstance(metadata, dict) else None
    cached = _playlist_cache_lookup(playlist_id, snapshot_id)
    if cached is not None:
        print(f"  {len(cached[0])} tracks na playlist (cache snapshot_id)")
        return cached

    url = playlist_items_url.format(id=playlist_id)
    uris: list[str] = []
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    cached_entries: list[dict[str, Any]] = []
    params: dict[str, Any] = {"limit": 100}
    while url:
        data = request("GET", url, token, params=params).json()
        for entry_wrapper in data.get("items", []):
            entry = (entry_wrapper or {}).get("item") or (entry_wrapper or {}).get("track")
            parsed = playlist_entry_to_match(entry)
            if parsed:
                match, keys = parsed
                uris.append(match["uri"])
                for key in keys:
                    lookup.setdefault(key, match)
                cached_entries.append(
                    {
                        "match": match,
                        "keys": [list(key) for key in keys],
                    }
                )
        url = data.get("next")
        params = {}

    _save_playlist_cache(playlist_id, snapshot_id, cached_entries)
    return uris, lookup
