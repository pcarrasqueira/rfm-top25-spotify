import datetime
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

import rfm_live_to_spotify as rfm


HISTORY_HTML = (Path(__file__).parent / "fixtures" / "rfm_history.html").read_text(encoding="utf-8")
NOW = datetime.datetime(2026, 10, 20, 14, 10, tzinfo=rfm.LISBON_TZ)
EXPECTED_TRACKS = [
    {"artist": "Rema, Selena Gomez", "title": "CALM DOWN"},
    {"artist": "ZARKO feat. LATTE", "title": "1,2,3"},
]


class RFMHistoryTests(unittest.TestCase):
    def test_reads_times_and_descriptions_from_the_same_song_block(self):
        self.assertEqual(rfm.parse_history(HISTORY_HTML, NOW), EXPECTED_TRACKS)

    def test_filters_old_tracks_but_keeps_the_cutoff(self):
        now = NOW.replace(hour=15, minute=5)
        self.assertEqual(rfm.parse_history(HISTORY_HTML, now), EXPECTED_TRACKS[1:])

    def test_valid_history_outside_window_returns_no_tracks(self):
        self.assertEqual(rfm.parse_history(HISTORY_HTML, NOW.replace(hour=16)), [])

    def test_previous_day_tracks_at_midnight(self):
        html = HISTORY_HTML.replace("14:01", "23:41").replace("14:05", "00:05")
        self.assertEqual(rfm.parse_history(html, NOW.replace(hour=0)), EXPECTED_TRACKS)

    def test_deduplicates_repeated_songs(self):
        html = HISTORY_HTML.replace("1,2,3", "CALM DOWN").replace("ZARKO feat. LATTE", "Rema, Selena Gomez")
        self.assertEqual(rfm.parse_history(html, NOW), EXPECTED_TRACKS[:1])

    def test_missing_or_changed_markup_is_an_error(self):
        for html in (
            "<html><body>Service unavailable</body></html>",
            '<section id="musicList"><p>Changed layout</p></section>',
            HISTORY_HTML.replace('class="t-hor"', 'class="changed"'),
            HISTORY_HTML.replace('class="t-desc"', 'class="changed"'),
            HISTORY_HTML.replace("14:01", "invalid"),
            HISTORY_HTML.replace("14:01", "25:01"),
            HISTORY_HTML.replace(">CALM DOWN<", "><"),
        ):
            with self.subTest(html=html), self.assertRaises(rfm.RFMHistoryError):
                rfm.parse_history(html, NOW)

    def test_empty_music_list_returns_no_tracks(self):
        self.assertEqual(rfm.parse_history('<section id="musicList"></section>', NOW), [])

    def test_fetch_failure_is_an_error(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("503 unavailable")
        for result in (requests.Timeout("timeout"), response):
            with self.subTest(result=result):
                with patch.object(rfm.requests, "get", side_effect=[result]):
                    with self.assertRaises(rfm.RFMHistoryError):
                        rfm.scrape_current()

    def test_fetch_passes_response_to_parser(self):
        response = Mock(text=HISTORY_HTML)
        with patch.object(rfm.requests, "get", return_value=response):
            with patch.object(rfm, "parse_history", return_value=EXPECTED_TRACKS) as parse:
                self.assertEqual(rfm.scrape_current(), EXPECTED_TRACKS)
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(parse.call_args.args[0], HISTORY_HTML)

    def test_main_reports_scrape_failure_and_exits_unsuccessfully(self):
        with patch.object(rfm, "scrape_current", side_effect=rfm.RFMHistoryError("Invalid HTML")):
            with patch.object(rfm, "write_summary") as summary, patch.object(rfm, "get_access_token") as token:
                with self.assertRaises(SystemExit) as raised:
                    rfm.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("Falha ao recolher", "\n".join(summary.call_args.args[0]))
        token.assert_not_called()

    def test_main_reports_empty_window_without_claiming_no_new_tracks(self):
        with patch.object(rfm, "scrape_current", return_value=[]):
            with patch.object(rfm, "write_summary") as summary, patch.object(rfm, "get_access_token") as token:
                with self.assertRaises(SystemExit) as raised:
                    rfm.main()
        self.assertEqual(raised.exception.code, 0)
        message = "\n".join(summary.call_args.args[0])
        self.assertIn("Sem músicas no historial", message)
        self.assertNotIn("novos", message)
        token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
