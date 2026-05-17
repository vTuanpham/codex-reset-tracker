import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from codex_reset_tracker.config import AppConfig, PollingConfig
from codex_reset_tracker.models import TweetRecord
from codex_reset_tracker.runner import QuotaResetTracker
from codex_reset_tracker.state import StateStore


def tweet(tweet_id: str, text: str, created_at: str | None = None) -> TweetRecord:
    return TweetRecord(
        id=tweet_id,
        author_username="OpenAI",
        author_name="OpenAI",
        text=text,
        created_at=created_at,
        url=f"https://x.com/OpenAI/status/{tweet_id}",
        source="test",
    )


class FakeSource:
    def __init__(self, batches):
        self.batches = list(batches)

    async def connect(self):
        return None

    async def iter_account_tweets(self, accounts, count):
        for item in self.batches.pop(0):
            yield item

    async def iter_search_tweets(self, queries, count):
        if False:
            yield None


class RecordingNotifier:
    def __init__(self):
        self.matches = []

    async def send_match(self, match):
        self.matches.append(match)
        return {"recording": {"ok": True}}


class RunnerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
