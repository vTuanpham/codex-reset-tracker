import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from codex_reset_tracker.models import TweetMatch, TweetRecord
from codex_reset_tracker.time_window import attach_reset_window, estimate_reset_window, parse_created_at


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

    def test_tomorrow_morning_maps_from_source_time_to_user_time(self):
        now = datetime(2026, 5, 30, 22, 59, tzinfo=ZoneInfo("America/Los_Angeles"))

        window = estimate_reset_window(
            "Resetting the limits tomorrow morning to celebrate.",
            source_timezone_name="America/Los_Angeles",
            user_timezone_name="Asia/Saigon",
            now=now,
        )

        self.assertIsNotNone(window)
        self.assertEqual(window.label, "tomorrow morning")
        self.assertEqual(window.source_start_at, "2026-05-31T06:00-07:00")
        self.assertEqual(window.source_end_at, "2026-05-31T12:00-07:00")
        self.assertEqual(window.user_start_at, "2026-05-31T20:00+07:00")
        self.assertEqual(window.user_end_at, "2026-06-01T02:00+07:00")

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

    def test_attach_window_uses_tweet_created_at_as_phrase_anchor(self):
        tweet = TweetRecord(
            id="2055446089957036402",
            author_username="thsottiaux",
            author_name="Thomas",
            text="I will reset usage limits this evening.",
            created_at="Sat May 16 00:31:50 +0000 2026",
            url="https://x.com/thsottiaux/status/2055446089957036402",
            source="test",
        )
        match = TweetMatch(tweet=tweet, matched_patterns=("reset",), excerpt=tweet.text)

        with_window = attach_reset_window(
            match,
            source_timezone_name="Europe/Paris",
            user_timezone_name="Asia/Saigon",
        )

        self.assertIsNotNone(with_window.reset_window)
        self.assertEqual(with_window.reset_window.source_start_at, "2026-05-16T17:00+02:00")
        self.assertEqual(with_window.reset_window.user_start_at, "2026-05-16T22:00+07:00")


NOW = datetime(2026, 10, 22, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))  # a Thursday morning


def window(text, source="America/Los_Angeles", now=NOW):
    return estimate_reset_window(text, source_timezone_name=source, user_timezone_name="Asia/Saigon", now=now)


class SpokenTimeTests(unittest.TestCase):
    def assert_source_span(self, text, start, end, confidence=None, **kwargs):
        found = window(text, **kwargs)
        self.assertIsNotNone(found, text)
        self.assertEqual((found.source_start_at, found.source_end_at), (start, end), text)
        if confidence:
            self.assertEqual(found.confidence, confidence, text)

    def test_relative_phrases_in_words_and_ranges(self):
        cases = {
            "reset in an hour": ("2026-10-22T09:30-07:00", "2026-10-22T10:30-07:00", "medium"),
            "reset in about 2 hours": ("2026-10-22T10:30-07:00", "2026-10-22T11:30-07:00", "medium"),
            "reset in a couple of hours": ("2026-10-22T10:30-07:00", "2026-10-22T11:30-07:00", "medium"),
            "reset in half an hour": ("2026-10-22T09:15-07:00", "2026-10-22T09:45-07:00", "medium"),
            "reset in 2-3 hours": ("2026-10-22T11:00-07:00", "2026-10-22T12:00-07:00", "medium"),
            "reset within the hour": ("2026-10-22T09:00-07:00", "2026-10-22T10:00-07:00", "high"),
            "reset within 1-2 hours": ("2026-10-22T09:00-07:00", "2026-10-22T11:00-07:00", "high"),
            "reset in the next few hours": ("2026-10-22T09:00-07:00", "2026-10-22T12:00-07:00", "high"),
        }
        for text, (start, end, confidence) in cases.items():
            self.assert_source_span(text, start, end, confidence)

    def test_a_zone_in_the_tweet_beats_the_account_default(self):
        in_saigon = datetime(2026, 10, 22, 16, 0, tzinfo=ZoneInfo("Asia/Saigon"))  # 02:00 in Los Angeles

        found = window("Resetting limits at 3pm PT", source="Asia/Saigon", now=in_saigon)

        self.assertEqual(found.confidence, "high")
        self.assertEqual((found.user_start_at, found.user_end_at), ("2026-10-23T04:40+07:00", "2026-10-23T05:20+07:00"))
        # the source fields then show the zone the tweet named
        self.assert_source_span("reset at 18:00 UTC", "2026-10-22T17:40+00:00", "2026-10-22T18:20+00:00", "high")
        # 15:00 UTC has already passed (it is 16:00 UTC), so it means tomorrow's
        self.assert_source_span("reset at 15:00 UTC", "2026-10-23T14:40+00:00", "2026-10-23T15:20+00:00", "high")

    def test_clock_times_without_a_zone_use_the_account_zone_with_a_wider_margin(self):
        self.assert_source_span("reset by noon", "2026-10-22T11:15-07:00", "2026-10-22T12:45-07:00", "medium")
        self.assert_source_span("reset at midnight", "2026-10-22T23:15-07:00", "2026-10-23T00:45-07:00")
        self.assert_source_span("reset tomorrow at 9am", "2026-10-23T08:15-07:00", "2026-10-23T09:45-07:00")
        self.assert_source_span("reset Friday 10am", "2026-10-23T09:15-07:00", "2026-10-23T10:45-07:00")
        self.assert_source_span("reset at 8:30 a.m.", "2026-10-23T07:45-07:00", "2026-10-23T09:15-07:00")

    def test_clock_ranges_inherit_a_missing_meridiem_and_a_zone(self):
        self.assert_source_span("reset between 3 and 5pm ET", "2026-10-22T15:00-04:00", "2026-10-22T17:00-04:00", "high")
        self.assert_source_span("reset 3-5pm", "2026-10-22T15:00-07:00", "2026-10-22T17:00-07:00", "medium")

    def test_a_weekday_part_and_a_zone_named_anywhere_in_the_tweet(self):
        self.assert_source_span("limits reset Friday evening", "2026-10-23T17:00-07:00", "2026-10-23T21:00-07:00", "medium")
        self.assert_source_span("limits reset Thursday evening", "2026-10-22T17:00-07:00", "2026-10-22T21:00-07:00")
        # the same words from an account whose default zone is elsewhere: "ET" in the tweet wins
        self.assert_source_span(
            "limits reset Friday evening ET", "2026-10-23T17:00-04:00", "2026-10-23T21:00-04:00", source="Asia/Saigon"
        )

    def test_a_time_after_as_of_or_since_is_behind_us(self):
        self.assertEqual(window("Usage limits reset for everyone as of 10:00 UTC").label, "already reset")
        self.assertEqual(window("reset since 3pm PT").label, "already reset")

    def test_days_without_a_time(self):
        self.assert_source_span("reset on Monday", "2026-10-26T00:00-07:00", "2026-10-26T23:59-07:00", "low")
        self.assert_source_span("reset this weekend", "2026-10-24T00:00-07:00", "2026-10-25T23:59-07:00", "low")
        # the clocks go back on Nov 1, so the week's last minute is at -08:00
        self.assert_source_span("reset next week", "2026-10-26T00:00-07:00", "2026-11-01T23:59-08:00", "low")
        self.assert_source_span("reset by EOD", "2026-10-22T09:00-07:00", "2026-10-22T23:59-07:00", "medium")
        self.assert_source_span("reset any minute now", "2026-10-22T09:00-07:00", "2026-10-22T09:30-07:00")

    def test_a_reset_said_as_done_is_live_now_but_a_plan_is_not(self):
        for text in ("We just reset usage limits for everyone", "Resetting limits now", "limits have been reset"):
            self.assertEqual(window(text).label, "already reset", text)
        for text in ("We will reset the limits", "I might reset usage if it goes well", "reset when the model ships"):
            self.assertIsNone(window(text), text)

    def test_numbers_that_are_not_times_are_ignored(self):
        for text in (
            "5 million users, reset coming",
            "the 3 amazing models reset",
            "reset 2026 plan, $20 tier",
            "limits reset after 9 to 5 crowd",
        ):
            self.assertIsNone(window(text), text)

    def test_a_time_with_no_am_pm_still_falls_back_to_the_phrase_around_it(self):
        self.assertEqual(window("reset at 7:30 tonight").label, "tonight")


if __name__ == "__main__":
    unittest.main()
