import json
import tempfile
import unittest
from pathlib import Path

from codex_reset_tracker.config import ConfigError
from codex_reset_tracker.twikit_source import _load_cookie_mapping, _normalize_cookies


class CookieNormalizationTests(unittest.TestCase):
    def test_accepts_twikit_cookie_mapping(self):
        cookies = _normalize_cookies({"auth_token": "abc", "ct0": "def"})

        self.assertEqual(cookies, {"auth_token": "abc", "ct0": "def"})

    def test_accepts_cookie_editor_list_export(self):
        cookies = _normalize_cookies(
            [
                {"domain": ".x.com", "name": "auth_token", "value": "abc"},
                {"domain": "x.com", "name": "ct0", "value": "def"},
                {"domain": "example.com", "name": "ignored", "value": "no"},
            ]
        )

        self.assertEqual(cookies, {"auth_token": "abc", "ct0": "def"})

    def test_accepts_wrapped_cookie_list(self):
        cookies = _normalize_cookies(
            {"cookies": [{"domain": ".twitter.com", "name": "twid", "value": "u=1"}]}
        )

        self.assertEqual(cookies, {"twid": "u=1"})

    def test_empty_cookie_file_has_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cookies.json"
            path.write_text(json.dumps([]), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "No usable cookies"):
                _load_cookie_mapping(path)


if __name__ == "__main__":
    unittest.main()
