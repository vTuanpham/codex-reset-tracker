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


if __name__ == "__main__":
    unittest.main()
