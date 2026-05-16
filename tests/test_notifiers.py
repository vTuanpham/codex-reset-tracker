import asyncio
import contextlib
import io
import unittest

from codex_reset_tracker.config import NotificationsConfig
from codex_reset_tracker.models import TweetMatch, TweetRecord
from codex_reset_tracker.notifiers import NotificationManager, format_alert


def match() -> TweetMatch:
    tweet = TweetRecord(
        id="123",
        author_username="OpenAI",
        author_name="OpenAI",
        text="Codex quota reset",
        created_at="today",
        url="https://x.com/OpenAI/status/123",
        source="test",
    )
    return TweetMatch(tweet=tweet, matched_patterns=("codex", "quota", "reset"), excerpt=tweet.text)


class NotifierTests(unittest.TestCase):
    def test_format_alert_contains_url_and_author(self):
        message = format_alert("Potential Codex quota reset", match())

        self.assertIn("@OpenAI", message.body)
        self.assertIn("https://x.com/OpenAI/status/123", message.body)
        self.assertEqual(message.payload["tweet_id"], "123")

    def test_stdout_notifier_succeeds(self):
        manager = NotificationManager(
            NotificationsConfig(channels={"stdout": {"enabled": True}})
        )

        with contextlib.redirect_stdout(io.StringIO()):
            result = asyncio.run(manager.send_match(match()))

        self.assertEqual(result["stdout"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
