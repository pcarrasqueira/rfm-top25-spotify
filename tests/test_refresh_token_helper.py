from contextlib import redirect_stdout
import io
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit


class RefreshTokenHelperTests(unittest.TestCase):
    def test_authorisation_and_token_exchange_use_the_registered_loopback(self):
        script = Path(__file__).parents[1] / "get_refresh_token.py"
        response = Mock()
        response.json.return_value = {"access_token": "fake-access", "refresh_token": "fake-refresh"}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "refresh_token.txt"
            with patch("builtins.input", side_effect=["fake-client", "fake-secret", "fake-code"]), patch("requests.post", return_value=response) as post, patch("builtins.open", side_effect=lambda *args, **kwargs: token_file.open(*args[1:], **kwargs)), redirect_stdout(output):
                runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(token_file.read_text(), "fake-refresh")
        auth_url = next(line for line in output.getvalue().splitlines() if line.startswith("https://accounts.spotify.com/authorize?"))
        redirect_uri = parse_qs(urlsplit(auth_url).query)["redirect_uri"][0]
        self.assertEqual(redirect_uri, "http://127.0.0.1:8888/callback")
        self.assertEqual(post.call_args.kwargs["data"]["redirect_uri"], redirect_uri)
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
