"""Exercise the five Live entry points against an in-memory Spotify playlist."""

from contextlib import ExitStack, contextmanager, redirect_stdout
import importlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests


STATIONS = (
    ("rfm_live_to_spotify", "scrape_current", "SPOTIFY_LIVE_PLAYLIST_ID"),
    ("comercial_live_to_spotify", "fetch_tracks", "SPOTIFY_COMERCIAL_LIVE_PLAYLIST_ID"),
    ("m80_live_to_spotify", "fetch_tracks", "SPOTIFY_M80_LIVE_PLAYLIST_ID"),
    ("antena3_live_to_spotify", "fetch_tracks", "SPOTIFY_ANTENA3_LIVE_PLAYLIST_ID"),
    ("batida_live_to_spotify", "fetch_tracks", "SPOTIFY_BATIDA_LIVE_PLAYLIST_ID"),
)


class Playlist:
    def __init__(self, uris=(), fail_post=None, fail_delete=False):
        self.uris = list(uris)
        self.calls = []
        self.post_count = 0
        self.fail_post = fail_post
        self.fail_delete = fail_delete

    def request(self, method, url, token, *, json):
        self.calls.append((method, len(self.uris)))
        if method == "POST":
            self.post_count += 1
            if self.post_count == self.fail_post:
                raise requests.Timeout("addition failed")
            if len(json["uris"]) > 100:
                raise AssertionError("Spotify only accepts 100 songs per request")
            position = json["position"]
            self.uris[position:position] = json["uris"]
        elif method == "DELETE":
            if self.fail_delete:
                raise requests.ConnectionError("trim failed")
            removed = {item["uri"] for item in json["items"]}
            self.uris[:] = [uri for uri in self.uris if uri not in removed]
        else:
            raise AssertionError(f"Unexpected Spotify request: {method}")


class LostResponseSpotify(Playlist):
    """The first addition succeeds at the server, but its response is lost."""

    @staticmethod
    def entry(uri):
        return {"uri": uri, "name": uri.rsplit(":", 1)[-1], "artists": [{"name": "Artist"}]}

    def api_request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if method == "GET" and path.endswith("/items"):
            offset = int(parse_qs(urlsplit(url).query).get("offset", [0])[0])
            end = offset + 100
            payload = {
                "items": [{"item": self.entry(uri)} for uri in self.uris[offset:end]],
                "next": f"{url.split('?')[0]}?offset={end}" if end < len(self.uris) else None,
            }
        elif method == "GET" and path.endswith("/search"):
            payload = {"tracks": {"items": [self.entry("spotify:track:New")]}}
        elif method == "GET":
            payload = {"snapshot_id": f"snapshot-{len(self.calls)}"}
        else:
            self.request(method, url, "fake-token", json=kwargs["json"])
            if method == "POST" and self.post_count == 1:
                raise requests.Timeout("response lost after the playlist changed")
            payload = {"snapshot_id": f"snapshot-{len(self.calls)}"}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode()
        response.url = url
        return response


@contextmanager
def station_run(station, playlist, tracks, resolve=None):
    name, source, env_key = station
    module = importlib.import_module(name)
    if resolve is None:
        resolve = lambda token, artist, title: {
            "uri": f"spotify:track:{title}",
            "spotify_artist": artist,
            "spotify_title": title,
        }
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(patch.dict(os.environ, {env_key: "fake-playlist"}))
        stack.enter_context(patch.object(module, source, return_value=tracks))
        stack.enter_context(patch.object(module, "get_access_token", return_value="fake-token"))
        stack.enter_context(patch.object(module, "get_playlist_uris", side_effect=lambda *args: (list(playlist.uris), {})))
        stack.enter_context(patch.object(module, "search_track", side_effect=resolve))
        stack.enter_context(patch.object(module, "spotify", side_effect=playlist.request))
        stack.enter_context(patch.object(module.time, "sleep"))
        stack.enter_context(patch.object(module, "update_playlist_description"))
        stack.enter_context(patch.object(module, "write_summary"))
        yield module


class LivePlaylistTests(unittest.TestCase):
    def test_next_run_reconciles_a_lost_response_using_real_client_and_snapshot_cache(self):
        old = [f"spotify:track:old-{i}" for i in range(300)]
        tracks = [{"artist": "Artist", "title": "New"}]
        for name, source, env_key in STATIONS:
            with self.subTest(station=name), tempfile.TemporaryDirectory() as state_dir:
                module = importlib.import_module(name)
                service = LostResponseSpotify(old)
                with ExitStack() as stack:
                    stack.enter_context(redirect_stdout(io.StringIO()))
                    stack.enter_context(patch.dict(os.environ, {env_key: "fake-playlist", "SPOTIFY_STATE_DIR": state_dir}))
                    stack.enter_context(patch.object(module, source, return_value=tracks))
                    stack.enter_context(patch.object(module, "get_access_token", return_value="fake-token"))
                    stack.enter_context(patch("spotify_client.requests.request", side_effect=service.api_request))
                    stack.enter_context(patch.object(module.time, "sleep"))
                    stack.enter_context(patch.object(module, "update_playlist_description"))
                    stack.enter_context(patch.object(module, "write_summary"))
                    with self.assertRaises(requests.Timeout):
                        module.main()
                    self.assertEqual(service.uris, ["spotify:track:New"] + old)
                    module.main()
                self.assertEqual(service.post_count, 1)
                self.assertEqual(service.calls, [("POST", 300), ("DELETE", 301)])
                self.assertEqual(service.uris, ["spotify:track:New"] + old[:-1])

    def test_radio_aliases_resolving_to_the_same_uri_are_only_added_once(self):
        tracks = [{"artist": "Artist", "title": "Song"}, {"artist": "Artist feat. Guest", "title": "Song"}]
        for station in STATIONS:
            with self.subTest(station=station[0]):
                playlist = Playlist()
                with station_run(station, playlist, tracks) as module:
                    module.main()
                self.assertEqual(playlist.uris, ["spotify:track:Song"])

    def test_full_playlist_accepts_new_songs_before_old_songs_are_deleted(self):
        old = [f"spotify:track:old-{i}" for i in range(300)]
        tracks = [{"artist": "Artist", "title": "Newest"}, {"artist": "Artist", "title": "Next"}]
        for station in STATIONS:
            with self.subTest(station=station[0]):
                playlist = Playlist(old)
                with station_run(station, playlist, tracks) as module:
                    module.main()
                self.assertEqual(playlist.calls, [("POST", 300), ("DELETE", 302)])
                self.assertEqual(playlist.uris, ["spotify:track:Newest", "spotify:track:Next"] + old[:-2])

    def test_failed_add_does_not_delete_existing_songs(self):
        old = [f"spotify:track:old-{i}" for i in range(300)]
        for station in STATIONS:
            with self.subTest(station=station[0]):
                playlist = Playlist(old, fail_post=1)
                with station_run(station, playlist, [{"artist": "Artist", "title": "New"}]) as module:
                    with self.assertRaises(requests.Timeout):
                        module.main()
                self.assertEqual(playlist.uris, old)
                self.assertEqual(playlist.calls, [("POST", 300)])

    def test_failed_second_batch_keeps_old_songs_and_confirmed_first_batch(self):
        old = [f"spotify:track:old-{i}" for i in range(300)]
        tracks = [{"artist": "Artist", "title": str(i)} for i in range(101)]
        for station in STATIONS:
            with self.subTest(station=station[0]):
                playlist = Playlist(old, fail_post=2)
                with station_run(station, playlist, tracks) as module:
                    with self.assertRaises(requests.Timeout):
                        module.main()
                self.assertEqual(playlist.uris[100:], old)
                self.assertEqual(playlist.calls, [("POST", 300), ("POST", 400)])

    def test_multiple_batches_preserve_newest_first_order(self):
        tracks = [{"artist": "Artist", "title": str(i)} for i in range(205)]
        for station in STATIONS:
            with self.subTest(station=station[0]):
                playlist = Playlist(["spotify:track:old"])
                with station_run(station, playlist, tracks) as module:
                    module.main()
                self.assertEqual(playlist.uris, [f"spotify:track:{i}" for i in range(205)] + ["spotify:track:old"])

    def test_next_run_finishes_failed_trim_without_readding_songs(self):
        old = [f"spotify:track:old-{i}" for i in range(300)]
        tracks = [{"artist": "Artist", "title": "New"}]
        for station in STATIONS:
            with self.subTest(station=station[0]):
                playlist = Playlist(old, fail_delete=True)
                with station_run(station, playlist, tracks) as module:
                    with self.assertRaises(requests.ConnectionError):
                        module.main()
                self.assertEqual(len(playlist.uris), 301)
                playlist.fail_delete = False
                playlist.calls.clear()
                with station_run(station, playlist, tracks) as module:
                    module.main()
                self.assertEqual(playlist.calls, [("DELETE", 301)])
                self.assertEqual(playlist.uris, ["spotify:track:New"] + old[:-1])


if __name__ == "__main__":
    unittest.main()
