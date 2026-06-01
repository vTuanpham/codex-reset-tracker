import unittest
from unittest.mock import patch

from codex_reset_tracker.config import (
    ConfigError,
    detect_local_timezone,
    parse_config,
)


class ConfigTests(unittest.TestCase):
    def test_auto_timezone_uses_tz_env_when_available(self):
        with patch.dict("os.environ", {"TZ": "Asia/Saigon"}):
            config = parse_config({"local_timezone": "auto", "time": {"user_timezone": "auto"}})

        self.assertEqual(config.local_timezone, "Asia/Saigon")
        self.assertEqual(config.time.user_timezone, "Asia/Saigon")

    def test_detect_local_timezone_falls_back_when_env_is_invalid(self):
        with patch.dict("os.environ", {"TZ": "Not/AZone"}):
            timezone_name = detect_local_timezone(default="UTC")

        self.assertTrue(timezone_name)

    def test_explicit_invalid_timezone_fails(self):
        with self.assertRaisesRegex(ConfigError, "Invalid timezone"):
            parse_config({"time": {"user_timezone": "Not/AZone"}})

    def test_missing_search_queries_default_empty(self):
        config = parse_config({"polling": {"accounts": ["OpenAI", "ClaudeDevs"]}})

        self.assertEqual(config.polling.search_queries, ())


if __name__ == "__main__":
    unittest.main()
