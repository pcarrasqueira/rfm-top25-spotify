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
        self.assertEqual(rfm.parse_history(HISTORY_HTML, NOW), EXPECTED_TRACKS[::-1])

    def test_filters_old_tracks_but_keeps_the_cutoff(self):
        now = NOW.replace(hour=15, minute=5)
        self.assertEqual(rfm.parse_history(HISTORY_HTML, now), EXPECTED_TRACKS[1:])

    def test_valid_history_outside_window_returns_no_tracks(self):
        self.assertEqual(rfm.parse_history(HISTORY_HTML, NOW.replace(hour=16)), [])

    def test_previous_day_tracks_at_midnight(self):
        html = HISTORY_HTML.replace("14:01", "23:41").replace("14:05", "00:05")
        self.assertEqual(rfm.parse_history(html, NOW.replace(hour=0)), EXPECTED_TRACKS[::-1])

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
        previous = Mock(text="")
        with patch.object(rfm.requests, "get", return_value=response), patch.object(rfm.requests, "post", return_value=previous):
            with patch.object(rfm, "parse_history", return_value=EXPECTED_TRACKS) as parse:
                self.assertEqual(rfm.scrape_current(NOW), EXPECTED_TRACKS)
        response.raise_for_status.assert_called_once_with()
        previous.raise_for_status.assert_called_once_with()
        self.assertEqual(parse.call_args_list[0].args[0], HISTORY_HTML)
        self.assertEqual(parse.call_count, 2)

    @staticmethod
    def row(time, title, artist="Artist"):
        return (
            f'<ul class="g-mx"><li class="t-hor">{time}</li>'
            f'<li class="t-desc"><ul><li>{title}</li><li>{artist}</li></ul></li></ul>'
        )

    def test_fetches_previous_hour_and_merges_newest_first(self):
        current = Mock(text='<section id="musicList">' + self.row("14:01", "First") + self.row("14:40", "Latest") + '</section>')
        previous = Mock(text=self.row("13:47", "Too old") + self.row("13:49", "Previous") + self.row("13:55", "First"))
        with patch.object(rfm.requests, "get", return_value=current), patch.object(rfm.requests, "post", return_value=previous) as post:
            tracks = rfm.scrape_current(NOW.replace(minute=48))
        self.assertEqual([track["title"] for track in tracks], ["Latest", "First", "Previous"])
        self.assertEqual(post.call_args.kwargs["data"], {"hora": "13", "dia": "today"})
        self.assertEqual(post.call_args.args[0], rfm.RFM_HISTORY_SEARCH_URL)

    def test_fetches_yesterday_at_midnight(self):
        current = Mock(text='<section id="musicList">' + self.row("00:05", "Today") + '</section>')
        previous = Mock(text=self.row("23:01", "Too old") + self.row("23:45", "Yesterday"))
        with patch.object(rfm.requests, "get", return_value=current), patch.object(rfm.requests, "post", return_value=previous) as post:
            tracks = rfm.scrape_current(NOW.replace(hour=0))
        self.assertEqual([track["title"] for track in tracks], ["Today", "Yesterday"])
        self.assertEqual(post.call_args.kwargs["data"], {"hora": "23", "dia": "yesterday"})

    def test_previous_hour_failure_does_not_return_partial_history(self):
        with patch.object(rfm.requests, "get", return_value=Mock(text=HISTORY_HTML)):
            with patch.object(rfm.requests, "post", side_effect=requests.Timeout("previous hour unavailable")):
                with self.assertRaises(rfm.RFMHistoryError):
                    rfm.scrape_current(NOW)

    def test_unrecognised_previous_page_is_an_error(self):
        with patch.object(rfm.requests, "get", return_value=Mock(text=HISTORY_HTML)):
            with patch.object(rfm.requests, "post", return_value=Mock(text="Service unavailable")):
                with self.assertRaises(rfm.RFMHistoryError):
                    rfm.scrape_current(NOW)

    def test_empty_previous_hour_keeps_current_tracks(self):
        with patch.object(rfm.requests, "get", return_value=Mock(text=HISTORY_HTML)):
            with patch.object(rfm.requests, "post", return_value=Mock(text="")):
                self.assertEqual(rfm.scrape_current(NOW), EXPECTED_TRACKS[::-1])

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
