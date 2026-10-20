from contextlib import redirect_stdout
import datetime
import io
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import antena3_live_to_spotify as antena3
import comercial_live_to_spotify as comercial
import m80_live_to_spotify as m80


REAL_DATETIME = datetime.datetime
LISBON = ZoneInfo("Europe/Lisbon")


def fixed_clock(instant):
    class FixedDateTime(REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    return FixedDateTime


def record(when, title):
    return {"DATE": when, "ZENON": {"SONG_NAME": title, "ARTIST_NAME": "Artist"}}


class HistoryWindowTests(unittest.TestCase):
    def test_midnight_combines_both_days_and_keeps_only_the_window(self):
        now = REAL_DATETIME(2026, 10, 21, 0, 24, tzinfo=LISBON)
        records_by_date = {
            "2026-10-20": [record("2026-10-20 23:23:00", "Too old"), record("2026-10-20 23:24:00", "Cutoff"), record("2026-10-20 23:50:00", "Yesterday")],
            "2026-10-21": [record("2026-10-21 00:10:00", "Today"), record("2026-10-21 00:30:00", "Future")],
        }
        for module in (comercial, m80):
            with self.subTest(station=module.__name__):
                def get(url, **kwargs):
                    date = url.rsplit("_", 1)[-1].removesuffix(".json")
                    response = Mock()
                    response.json.return_value = {"NOW_PLAYING_LOG": {"NOW_PLAYING_RECORD": records_by_date[date]}}
                    return response
                with patch.object(module.datetime, "datetime", fixed_clock(now)), patch.object(module.requests, "get", side_effect=get) as request, redirect_stdout(io.StringIO()):
                    tracks = module.fetch_tracks()
                self.assertEqual(request.call_count, 2)
                self.assertEqual([track["title"] for track in tracks], ["Today", "Yesterday", "Cutoff"])

    def test_daytime_only_requests_one_day(self):
        now = REAL_DATETIME(2026, 10, 20, 14, 24, tzinfo=LISBON)
        for module in (comercial, m80):
            with self.subTest(station=module.__name__):
                response = Mock()
                response.json.return_value = {"NOW_PLAYING_LOG": {"NOW_PLAYING_RECORD": [record("2026-10-20 14:10:00", "Song")]}}
                with patch.object(module.datetime, "datetime", fixed_clock(now)), patch.object(module.requests, "get", return_value=response) as request, redirect_stdout(io.StringIO()):
                    self.assertEqual(module.fetch_tracks(), [{"title": "Song", "artist": "Artist"}])
                request.assert_called_once()

    def test_antena3_uses_lisbon_time_on_both_sides_of_clock_changes(self):
        instants = (
            REAL_DATETIME(2026, 3, 28, 13, 10, tzinfo=datetime.timezone.utc),
            REAL_DATETIME(2026, 3, 30, 12, 10, tzinfo=datetime.timezone.utc),
            REAL_DATETIME(2026, 10, 24, 12, 10, tzinfo=datetime.timezone.utc),
            REAL_DATETIME(2026, 10, 26, 13, 10, tzinfo=datetime.timezone.utc),
        )
        html = "<ul>" + '<li><span>13:05</span><span>13:05</span><b>Song</b><i>Artist</i></li>' * 51 + "</ul>"
        for instant in instants:
            with self.subTest(instant=instant):
                with patch.object(antena3.datetime, "datetime", fixed_clock(instant)), patch.object(antena3, "get_epg_program_names", return_value=set()), patch.object(antena3.requests, "get", return_value=Mock(text=html)), redirect_stdout(io.StringIO()):
                    self.assertEqual(antena3.fetch_tracks(), [{"title": "Song", "artist": "Artist"}])


if __name__ == "__main__":
    unittest.main()
