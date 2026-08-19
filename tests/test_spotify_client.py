import json
import datetime
import unittest
from unittest.mock import Mock, patch

import requests

import batida_live_to_spotify
import rfm_live_to_spotify
from spotify_client import (
    QuotaExceededError,
    RateLimitError,
    get_access_token,
    normalize_track_key,
    parse_retry_after,
    playlist_entry_to_match,
    spotify_request,
)


def make_response(status_code: int, payload: dict, headers: dict | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.headers.update(headers or {})
    response._content = json.dumps(payload).encode("utf-8")
    response.url = "https://api.spotify.com/v1/test"
    return response


class SpotifyClientTests(unittest.TestCase):
    def test_parse_retry_after_seconds(self) -> None:
        self.assertEqual(parse_retry_after("3"), 3.0)

    def test_retries_temporary_429_using_retry_after(self) -> None:
        request = Mock(
            side_effect=[
                make_response(429, {"error": {"status": 429, "message": "Too many requests"}}, {"Retry-After": "3"}),
                make_response(200, {"ok": True}),
            ]
        )
        sleep = Mock()

        with patch("spotify_client.requests.request", request):
            response = spotify_request(
                "GET",
                "https://api.spotify.com/v1/test",
                "token",
                max_retries=1,
                sleep_fn=sleep,
                jitter_fn=lambda _minimum, _maximum: 0.0,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(3.0)

    def test_retries_transient_5xx_using_backoff(self) -> None:
        request = Mock(
            side_effect=[
                make_response(502, {"error": {"message": "Bad Gateway"}}),
                make_response(200, {"ok": True}),
            ]
        )
        sleep = Mock()

        with patch("spotify_client.requests.request", request):
            response = spotify_request(
                "GET",
                "https://api.spotify.com/v1/test",
                "token",
                max_retries=1,
                sleep_fn=sleep,
                jitter_fn=lambda _minimum, _maximum: 0.0,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_retries_transient_connection_error(self) -> None:
        request = Mock(
            side_effect=[
                requests.ConnectionError("connection reset"),
                make_response(200, {"ok": True}),
            ]
        )
        sleep = Mock()

        with patch("spotify_client.requests.request", request):
            response = spotify_request(
                "GET",
                "https://api.spotify.com/v1/test",
                "token",
                max_retries=1,
                sleep_fn=sleep,
                jitter_fn=lambda _minimum, _maximum: 0.0,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_get_access_token_retries_transient_5xx(self) -> None:
        request = Mock(
            side_effect=[
                make_response(503, {"error": "temporarily unavailable"}),
                make_response(200, {"access_token": "access-token"}),
            ]
        )
        sleep = Mock()

        with (
            patch.dict(
                "os.environ",
                {
                    "SPOTIFY_REFRESH_TOKEN": "refresh-token",
                    "SPOTIFY_CLIENT_ID": "client-id",
                    "SPOTIFY_CLIENT_SECRET": "client-secret",
                },
            ),
            patch("spotify_client.requests.post", request),
        ):
            token = get_access_token(
                max_retries=1,
                sleep_fn=sleep,
                jitter_fn=lambda _minimum, _maximum: 0.0,
            )

        self.assertEqual(token, "access-token")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_quota_exceeded_is_not_retried(self) -> None:
        request = Mock(
            return_value=make_response(
                429,
                {
                    "error": {
                        "status": 429,
                        "message": "Too many requests",
                        "reason": "QUOTA_EXCEEDED",
                    }
                },
            )
        )

        with patch("spotify_client.requests.request", request):
            with self.assertRaises(QuotaExceededError) as context:
                spotify_request("GET", "https://api.spotify.com/v1/test", "token")

        self.assertEqual(request.call_count, 1)
        self.assertEqual(context.exception.reason, "QUOTA_EXCEEDED")

    def test_exhausted_rate_limit_exposes_retry_after(self) -> None:
        request = Mock(
            return_value=make_response(
                429,
                {"error": {"status": 429, "message": "Too many requests"}},
                {"Retry-After": "2"},
            )
        )
        sleep = Mock()

        with patch("spotify_client.requests.request", request):
            with self.assertRaises(RateLimitError) as context:
                spotify_request(
                    "GET",
                    "https://api.spotify.com/v1/test",
                    "token",
                    max_retries=2,
                    sleep_fn=sleep,
                    jitter_fn=lambda _minimum, _maximum: 0.0,
                )

        self.assertEqual(request.call_count, 3)
        self.assertEqual(context.exception.retry_after, 2)
        self.assertEqual(sleep.call_count, 2)

    def test_playlist_entry_can_be_used_as_track_cache(self) -> None:
        entry = {
            "uri": "spotify:track:123",
            "name": "Coração",
            "artists": [{"name": "Bárbara Tinoco"}, {"name": "Convidado"}],
        }

        parsed = playlist_entry_to_match(entry)

        self.assertIsNotNone(parsed)
        match, keys = parsed
        self.assertEqual(match["uri"], "spotify:track:123")
        self.assertIn(normalize_track_key("barbara tinoco", "coracao"), keys)

    def test_rfm_scraper_filters_tracks_older_than_one_hour(self) -> None:
        now = datetime.datetime.now(rfm_live_to_spotify.LISBON_TZ)
        current_time = now.strftime("%H:%M")
        old_time = (now - datetime.timedelta(hours=2)).strftime("%H:%M")
        html = f"""
        <ul>
          <li><span>{current_time}</span><ul><li>Atual</li><li>Artista Atual</li></ul></li>
          <li><span>{old_time}</span><ul><li>Antiga</li><li>Artista Antigo</li></ul></li>
        </ul>
        """
        response = Mock(text=html)

        with patch("rfm_live_to_spotify.requests.get", return_value=response):
            tracks = rfm_live_to_spotify.scrape_current()

        self.assertEqual(tracks, [{"artist": "Artista Atual", "title": "Atual"}])

    def test_batida_uses_one_hour_window(self) -> None:
        self.assertEqual(batida_live_to_spotify.WINDOW_HOURS, 1)


if __name__ == "__main__":
    unittest.main()
