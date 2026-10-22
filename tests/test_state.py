import json
import tempfile
import unittest
from pathlib import Path

from codex_reset_tracker.models import TweetMatch, TweetRecord
from codex_reset_tracker.state import StateStore


def tweet(text: str = "Codex quota reset") -> TweetRecord:
    return TweetRecord(
        id="42",
        author_username="OpenAI",
        author_name="OpenAI",
        text=text,
        created_at=None,
        url="https://x.com/OpenAI/status/42",
        source="test",
    )


class StateStoreTests(unittest.TestCase):
    def test_alert_dedupe_uses_tweet_text_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite3")
            first = TweetMatch(tweet=tweet("Codex quota reset"), matched_patterns=("codex",), excerpt="Codex quota reset")
            edited = TweetMatch(
                tweet=tweet("Codex quota reset now available"),
                matched_patterns=("codex",),
                excerpt="Codex quota reset now available",
            )

            self.assertFalse(store.was_alerted(first))
            store.mark_alerted(first, {"stdout": {"ok": True}})

            self.assertTrue(store.was_alerted(first))
            self.assertFalse(store.was_alerted(edited))
            store.close()

    def test_mark_seen_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite3")
            store.mark_seen(tweet())
            store.mark_seen(tweet("Codex quota reset edited"))
            store.close()

    def test_retry_merges_channel_results_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            store = StateStore(path)
            match = TweetMatch(tweet(), ("reset",), "Codex quota reset")
            self.assertIsNone(store.get_delivery(match.tweet))
            store.mark_alerted(
                match,
                {
                    "telegram": {"ok": True},
                    "desktop": {"ok": False, "error": "temporary failure"},
                },
            )
            store.mark_alerted(match, {"desktop": {"ok": True}})
            store.close()

            reopened = StateStore(path)
            self.assertEqual(
                reopened.get_delivery(match.tweet),
                {"telegram": {"ok": True}, "desktop": {"ok": True}},
            )
            self.assertIsNone(reopened.get_delivery(tweet("edited reset announcement")))
            reopened.close()

    def test_malformed_persisted_channel_results_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite3")
            match = TweetMatch(tweet(), ("reset",), "Codex quota reset")
            store.mark_alerted(match, {"desktop": {"ok": True}})
            for delivery in ([], {"desktop": True}, {"desktop": {}}, {"desktop": {"ok": 1}}):
                with self.subTest(delivery=delivery):
                    store._db.execute(
                        "UPDATE alerts SET delivery_json = ? WHERE alert_key = ?",
                        (json.dumps(delivery), match.alert_key),
                    )
                    with self.assertRaisesRegex(ValueError, "boolean channel results"):
                        store.get_delivery(match.tweet)
            store.close()


if __name__ == "__main__":
    unittest.main()
