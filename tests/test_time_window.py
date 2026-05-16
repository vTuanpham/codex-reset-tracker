import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from codex_reset_tracker.time_window import estimate_reset_window, parse_created_at


class TimeWindowTests(unittest.TestCase):
    def test_later_today_uses_current_local_time(self):
        now = datetime(2026, 5, 16, 9, 30, tzinfo=ZoneInfo("Asia/Saigon"))

        window = estimate_reset_window(
            "Codex quota reset later today",
            source_timezone_name="Asia/Saigon",
            user_timezone_name="Asia/Saigon",
            now=now,
        )

        self.assertIsNotNone(window)
        self.assertEqual(window.label, "later today")
        self.assertEqual(window.user_start_at, "2026-05-16T10:00+07:00")
        self.assertEqual(window.user_end_at, "2026-05-16T23:59+07:00")

    def test_evening_maps_from_source_time_to_user_time(self):
        now = datetime(2026, 5, 16, 9, 30, tzinfo=ZoneInfo("America/Los_Angeles"))

        window = estimate_reset_window(
            "Codex quota reset this evening",
            source_timezone_name="America/Los_Angeles",
            user_timezone_name="Asia/Saigon",
            now=now,
        )

        self.assertIsNotNone(window)
        self.assertEqual(window.source_start_at, "2026-05-16T17:00-07:00")
        self.assertEqual(window.source_end_at, "2026-05-16T21:00-07:00")
        self.assertEqual(window.user_start_at, "2026-05-17T07:00+07:00")
        self.assertEqual(window.user_end_at, "2026-05-17T11:00+07:00")

    def test_relative_hours(self):
        now = datetime(2026, 5, 16, 9, 30, tzinfo=ZoneInfo("Asia/Saigon"))

        window = estimate_reset_window(
            "Codex quota reset in 2 hours",
            source_timezone_name="Asia/Saigon",
            user_timezone_name="Asia/Saigon",
            now=now,
        )

        self.assertIsNotNone(window)
        self.assertEqual(window.user_start_at, "2026-05-16T11:00+07:00")
        self.assertEqual(window.user_end_at, "2026-05-16T12:00+07:00")

    def test_parse_created_at_iso(self):
        parsed = parse_created_at("2026-05-16T09:30:00+07:00")

        self.assertEqual(parsed.isoformat(), "2026-05-16T02:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
