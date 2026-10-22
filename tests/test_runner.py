import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codex_reset_tracker.config import AppConfig, NotificationsConfig, PollingConfig, TimeConfig
from codex_reset_tracker.matcher import RegexMatcher
from codex_reset_tracker.models import TweetRecord
from codex_reset_tracker.notifiers import NotificationError, NotificationManager
from codex_reset_tracker.runner import QuotaResetTracker
from codex_reset_tracker.runner import LAST_EFFECTIVE_SCAN_AT_KEY
from codex_reset_tracker.runner import LAST_SCAN_AT_KEY
from codex_reset_tracker.state import StateStore


def tweet(
    tweet_id: str,
    text: str,
    created_at: str | None = None,
    author_username: str = "OpenAI",
) -> TweetRecord:
    return TweetRecord(
        id=tweet_id,
        author_username=author_username,
        author_name=author_username,
        text=text,
        created_at=created_at,
        url=f"https://x.com/{author_username}/status/{tweet_id}",
        source="test",
    )


class FakeSource:
    def __init__(self, batches, search_batches=None):
        self.batches = list(batches)
        self.search_batches = list(search_batches or [[]])

    async def connect(self):
        return None

    async def iter_account_tweets(self, accounts, count):
        for item in self.batches.pop(0):
            yield item

    async def iter_search_tweets(self, queries, count):
        for item in self.search_batches.pop(0):
            yield item


class RecordingNotifier:
    channel_names = frozenset({"recording"})

    def __init__(self):
        self.matches = []

    async def send_match(self, match):
        self.matches.append(match)
        return {"recording": {"ok": True}}


class RecordingChannel:
    def __init__(self, name, failures=0):
        self.name = name
        self.failures = failures
        self.messages = []

    async def send(self, message):
        self.messages.append(message)
        if self.failures:
            self.failures -= 1
            raise NotificationError("temporary test failure")


class RunnerTests(unittest.TestCase):
    def test_partial_delivery_retries_after_restart_without_resending_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            state = StateStore(path)
            post = tweet("new", "Codex quota reset", datetime.now(timezone.utc).isoformat())
            telegram = RecordingChannel("telegram")
            desktop = RecordingChannel("desktop", failures=2)
            notifier = NotificationManager(NotificationsConfig())
            notifier.notifiers = [telegram, desktop]
            config = AppConfig(
                state_path=path,
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(
                    accounts=("OpenAI",), search_queries=(), alert_on_first_scan=True
                ),
            )
            tracker = QuotaResetTracker(
                config, source=FakeSource([[post]]), state=state, notifier=notifier
            )
            with self.assertLogs(level="ERROR"):
                first = asyncio.run(tracker.scan_once())
            self.assertEqual(first.alerted, 1)
            self.assertFalse(state.get_delivery(post)["desktop"]["ok"])
            state.close()

            state = StateStore(path)
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[post], [post], [post]], search_batches=[[], [], []]),
                state=state,
                notifier=notifier,
            )
            tracker.catchup_cutoff = datetime(2099, 1, 1, tzinfo=timezone.utc)
            tracker.reprocess_seen_cutoff = None
            with self.assertLogs(level="ERROR"):
                failed_retry = asyncio.run(tracker.scan_once())
            self.assertEqual(failed_retry.alerted, 0)
            self.assertFalse(state.get_delivery(post)["desktop"]["ok"])

            recovered = asyncio.run(tracker.scan_once())
            duplicate = asyncio.run(tracker.scan_once())
            self.assertEqual(recovered.alerted, 1)
            self.assertEqual(duplicate.alerted, 0)
            self.assertEqual(len(telegram.messages), 1)
            self.assertEqual(len(desktop.messages), 3)
            self.assertEqual(
                state.get_delivery(post),
                {"telegram": {"ok": True}, "desktop": {"ok": True}},
            )
            state.close()

    def test_all_channel_failures_do_not_block_other_tweets_or_future_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            first_post = tweet(
                "first",
                "Codex quota reset",
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            )
            second_post = tweet("second", "Codex limits reset")
            telegram = RecordingChannel("telegram", failures=1)
            desktop = RecordingChannel("desktop", failures=1)
            notifier = NotificationManager(NotificationsConfig())
            notifier.notifiers = [telegram, desktop]
            config = AppConfig(
                state_path=state.path,
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(
                    accounts=("OpenAI",), search_queries=(), alert_on_first_scan=True
                ),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[first_post, second_post]]),
                state=state,
                notifier=notifier,
            )
            with self.assertLogs(level="ERROR"):
                first = asyncio.run(tracker.scan_once())
            self.assertEqual(first.alerted, 1)
            self.assertTrue(state.has_seen(first_post))
            self.assertFalse(state.get_delivery(first_post)["desktop"]["ok"])
            self.assertFalse(state.get_delivery(first_post)["telegram"]["ok"])
            state.close()

            state = StateStore(config.state_path)
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[first_post, second_post]]),
                state=state,
                notifier=notifier,
            )
            self.assertGreater(
                tracker.catchup_cutoff, datetime.fromisoformat(first_post.created_at)
            )

            second = asyncio.run(tracker.scan_once())
            self.assertEqual(second.alerted, 1)
            self.assertTrue(state.has_seen(first_post))
            self.assertEqual(len(telegram.messages), 3)
            self.assertEqual(len(desktop.messages), 3)
            state.close()

    def test_retry_ignores_disabled_channels_and_does_not_backfill_new_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            post = tweet("old", "Codex quota reset", "2000-01-01T00:00:00+00:00")
            config = AppConfig(
                state_path=state.path,
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            match = RegexMatcher(config.matching).match(post)
            state.mark_alerted(match, {"telegram": {"ok": True}, "desktop": {"ok": False}})
            state.mark_seen(post)
            telegram = RecordingChannel("telegram")
            stdout = RecordingChannel("stdout")
            desktop = RecordingChannel("desktop")
            notifier = NotificationManager(NotificationsConfig())
            notifier.notifiers = [telegram, stdout]
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[post], [post]], search_batches=[[], []]),
                state=state,
                notifier=notifier,
            )
            tracker.reprocess_seen_cutoff = None
            skipped = asyncio.run(tracker.scan_once())
            self.assertEqual(skipped.alerted, 0)
            notifier.notifiers.append(desktop)
            retried = asyncio.run(tracker.scan_once())
            self.assertEqual(retried.alerted, 1)
            self.assertEqual(telegram.messages, [])
            self.assertEqual(stdout.messages, [])
            self.assertEqual(len(desktop.messages), 1)
            state.close()

    def test_failed_attempts_count_toward_the_scan_alert_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            desktop = RecordingChannel("desktop", failures=10)
            notifier = NotificationManager(NotificationsConfig())
            notifier.notifiers = [desktop]
            config = AppConfig(
                state_path=state.path,
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(
                    accounts=("OpenAI",),
                    search_queries=(),
                    alert_on_first_scan=True,
                    max_alerts_per_scan=1,
                ),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[tweet("first", "Codex reset"), tweet("second", "Codex reset")]]),
                state=state,
                notifier=notifier,
            )
            with self.assertLogs(level="ERROR"):
                summary = asyncio.run(tracker.scan_once())
            self.assertEqual(summary.alerted, 0)
            self.assertEqual(len(desktop.messages), 1)
            state.close()

    def test_first_scan_baselines_without_alerting_old_visible_tweets(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [
                        [
                            tweet(
                                "1",
                                "Codex usage limits have been refreshed. Quota reset is rolling out.",
                            )
                        ]
                    ]
                ),
                state=state,
                notifier=notifier,
            )

            summary = asyncio.run(tracker.scan_once())

            self.assertEqual(summary.scanned, 1)
            self.assertEqual(summary.alerted, 0)
            self.assertEqual(notifier.matches, [])
            self.assertTrue(state.has_seen(tweet("1", "anything")))
            state.close()

    def test_after_baseline_only_unseen_new_tweets_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.mark_seen(tweet("old", "Codex quota reset"))
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [
                        [
                            tweet(
                                "old",
                                "Codex usage limits have been refreshed. Quota reset is rolling out.",
                            ),
                            tweet(
                                "new",
                                "Codex usage limits have been refreshed later today. Quota reset is rolling out.",
                            ),
                        ]
                    ]
                ),
                state=state,
                notifier=notifier,
            )

            summary = asyncio.run(tracker.scan_once())

            self.assertEqual(summary.scanned, 2)
            self.assertEqual(summary.alerted, 1)
            self.assertEqual(notifier.matches[0].tweet.id, "new")
            self.assertIsNotNone(notifier.matches[0].reset_window)
            state.close()

    def test_created_at_before_start_is_suppressed_even_if_unseen(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.mark_seen(tweet("baseline", "already initialized"))
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [
                        [
                            tweet(
                                "old-unseen",
                                "Codex usage limits have been refreshed. Quota reset is rolling out.",
                                created_at="2000-01-01T00:00:00+00:00",
                            )
                        ]
                    ]
                ),
                state=state,
                notifier=notifier,
            )

            summary = asyncio.run(tracker.scan_once())

            self.assertEqual(summary.alerted, 0)
            self.assertEqual(notifier.matches, [])
            self.assertTrue(state.has_seen(tweet("old-unseen", "anything")))
            state.close()

    def test_startup_catches_up_since_last_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.set_metadata(LAST_SCAN_AT_KEY, "2026-05-20T04:00:00+00:00")
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [
                        [
                            tweet(
                                "during-downtime",
                                "Codex quota reset happened while the service was stopped.",
                                created_at="2026-05-20T06:00:00+00:00",
                            )
                        ]
                    ]
                ),
                state=state,
                notifier=notifier,
            )
            tracker.catchup_cutoff = datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc)

            summary = asyncio.run(tracker.scan_once())

            self.assertEqual(summary.alerted, 1)
            self.assertEqual(notifier.matches[0].tweet.id, "during-downtime")
            state.close()

    def test_startup_catchup_is_capped_at_one_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.set_metadata(LAST_SCAN_AT_KEY, "2026-05-18T00:00:00+00:00")
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [
                        [
                            tweet(
                                "too-old",
                                "Codex quota reset happened too far back.",
                                created_at="2026-05-18T12:00:00+00:00",
                            )
                        ]
                    ]
                ),
                state=state,
                notifier=notifier,
            )
            tracker.catchup_cutoff = datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)

            summary = asyncio.run(tracker.scan_once())

            self.assertEqual(summary.alerted, 0)
            self.assertEqual(notifier.matches, [])
            self.assertTrue(state.has_seen(tweet("too-old", "anything")))
            state.close()

    def test_empty_scan_does_not_advance_effective_scan_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.set_metadata(LAST_EFFECTIVE_SCAN_AT_KEY, "2026-05-20T04:00:00+00:00")
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[]]),
                state=state,
                notifier=notifier,
            )

            summary = asyncio.run(tracker.scan_once())

            self.assertEqual(summary.scanned, 0)
            self.assertIsNotNone(state.get_metadata(LAST_SCAN_AT_KEY))
            self.assertEqual(
                state.get_metadata(LAST_EFFECTIVE_SCAN_AT_KEY),
                "2026-05-20T04:00:00+00:00",
            )
            state.close()

    def test_startup_catchup_prefers_effective_scan_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.set_metadata(LAST_SCAN_AT_KEY, "2026-05-20T08:00:00+00:00")
            state.set_metadata(LAST_EFFECTIVE_SCAN_AT_KEY, "2026-05-20T04:00:00+00:00")
            notifier = RecordingNotifier()
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[]]),
                state=state,
                notifier=notifier,
            )

            cutoff = tracker._startup_catchup_cutoff(
                datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
            )

            self.assertEqual(cutoff, datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc))
            state.close()

    def test_diagnostic_dump_records_match_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.mark_seen(tweet("baseline", "already initialized"))
            notifier = RecordingNotifier()
            dump_path = Path(tmp) / "stream.jsonl"
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(accounts=("OpenAI",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [
                        [
                            tweet(
                                "new",
                                "Codex usage limits have been refreshed later today. Quota reset is rolling out.",
                            )
                        ]
                    ]
                ),
                state=state,
                notifier=notifier,
                dump_stream_path=dump_path,
            )

            summary = asyncio.run(tracker.scan_once())
            records = [
                json.loads(line)
                for line in dump_path.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(summary.alerted, 1)
            self.assertEqual(records[0]["decision"], "alerted")
            self.assertEqual(records[0]["tweet"]["id"], "new")
            self.assertEqual(records[0]["match"]["reset_window"]["label"], "later today")
            state.close()

    def test_matching_profile_change_reprocesses_recent_seen_tweet(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            seen_tweet = tweet(
                "2060964284117782996",
                "Five million users would agree. Resetting the limits tomorrow morning to celebrate.\n\n"
                "Time to go /fast",
                created_at="2026-05-31T05:59:10+00:00",
                author_username="thsottiaux",
            )
            state.mark_seen(seen_tweet)
            notifier = RecordingNotifier()
            dump_path = Path(tmp) / "stream.jsonl"
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                runtime_dir=Path(tmp) / "runtime",
                time=TimeConfig(user_timezone="Asia/Saigon"),
                polling=PollingConfig(accounts=("thsottiaux",), search_queries=()),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource([[seen_tweet]]),
                state=state,
                notifier=notifier,
                dump_stream_path=dump_path,
            )
            tracker.reprocess_seen_cutoff = datetime(2026, 5, 30, tzinfo=timezone.utc)

            summary = asyncio.run(tracker.scan_once())
            records = [
                json.loads(line)
                for line in dump_path.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(summary.alerted, 1)
            self.assertEqual(notifier.matches[0].tweet.id, "2060964284117782996")
            self.assertEqual(notifier.matches[0].reset_window.label, "tomorrow morning")
            self.assertEqual(records[0]["decision"], "reprocessed_alerted")
            state.close()

    def test_matching_search_result_from_untrusted_author_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.sqlite3")
            state.mark_seen(tweet("baseline", "already initialized"))
            notifier = RecordingNotifier()
            dump_path = Path(tmp) / "stream.jsonl"
            config = AppConfig(
                state_path=Path(tmp) / "state.sqlite3",
                runtime_dir=Path(tmp) / "runtime",
                polling=PollingConfig(
                    accounts=("OpenAI",),
                    search_queries=("reset",),
                ),
            )
            tracker = QuotaResetTracker(
                config,
                source=FakeSource(
                    [[]],
                    search_batches=[
                        [
                            tweet(
                                "stray",
                                "Codex reset is happening.",
                                author_username="not_openai",
                            ),
                            tweet(
                                "trusted",
                                "Reset is happening.",
                                author_username="OpenAI",
                            ),
                        ]
                    ],
                ),
                state=state,
                notifier=notifier,
                dump_stream_path=dump_path,
            )

            summary = asyncio.run(tracker.scan_once())
            records = [
                json.loads(line)
                for line in dump_path.read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(summary.scanned, 2)
            self.assertEqual(summary.alerted, 1)
            self.assertEqual(notifier.matches[0].tweet.id, "trusted")
            self.assertEqual(records[0]["decision"], "untrusted_author")
            self.assertEqual(records[0]["tweet"]["author_username"], "not_openai")
            state.close()


if __name__ == "__main__":
    unittest.main()
