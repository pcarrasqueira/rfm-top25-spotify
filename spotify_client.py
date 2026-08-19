"""Shared Spotify Web API helpers.

The Web API rate limit is shared by every workflow using the same Spotify app,
so retry behaviour must be consistent across all scripts.
"""

from __future__ import annotations

import datetime
import email.utils
import os
import random
import time
import unicodedata
from collections.abc import Callable
from typing import Any

import requests


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_TIMEOUT = 15
MAX_RETRIES = 3
MAX_JITTER_SECONDS = 0.25
TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})


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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "Spotify development quota excedida (QUOTA_EXCEEDED); retries adiados",
            **kwargs,
        )


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
            raise QuotaExceededError(**error_context)

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
            raise QuotaExceededError(**error_context)

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
