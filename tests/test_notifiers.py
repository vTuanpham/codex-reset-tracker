import asyncio
import contextlib
import io
import unittest
from unittest.mock import patch

from codex_reset_tracker.config import NotificationsConfig
from codex_reset_tracker.models import TweetMatch, TweetRecord
from codex_reset_tracker.notifiers import (
    AlertMessage,
    NotificationManager,
    desktop_notification_backend,
    desktop_notification_command,
    format_alert,
    resolve_alert_title,
)


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

    def test_resolve_alert_title_switches_to_claude_for_anthropic_accounts(self):
        anthropic_tweet = TweetRecord(
            id="anth-1",
            author_username="claudeai",
            author_name="Claude",
            text="We reset rate limits today",
            created_at="today",
            url="https://x.com/claudeai/status/1",
            source="test",
        )
        anthropic_match = TweetMatch(
            tweet=anthropic_tweet,
            matched_patterns=("reset",),
            excerpt=anthropic_tweet.text,
        )

        title = resolve_alert_title("Potential Codex quota reset", anthropic_match)
        self.assertEqual(title, "Potential Claude quota reset")

    def test_resolve_alert_title_keeps_codex_for_openai_accounts(self):
        title = resolve_alert_title("Potential Codex quota reset", match())
        self.assertEqual(title, "Potential Codex quota reset")

    def test_wsl_desktop_notification_routes_to_windows_powershell(self):
        message = AlertMessage(
            title="Codex reset",
            body="Quota reset from WSL",
            url="https://x.com/OpenAI/status/123",
            payload={},
        )

        with patch("codex_reset_tracker.notifiers.platform.system", return_value="Linux"):
            with patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}):
                with patch("codex_reset_tracker.notifiers.shutil.which", return_value="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                    command = desktop_notification_command(message)
                    backend = desktop_notification_backend()

        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-ExecutionPolicy", command)
        self.assertIn("System.Windows.Forms.NotifyIcon", command[-1])
        self.assertEqual(backend, "wsl-windows")

    def test_powershell_desktop_command_escapes_single_quotes(self):
        message = AlertMessage(
            title="Codex user's reset",
            body="It's ready",
            url="https://x.com/OpenAI/status/123",
            payload={},
        )

        with patch("codex_reset_tracker.notifiers.platform.system", return_value="Windows"):
            with patch("codex_reset_tracker.notifiers.shutil.which", return_value="powershell.exe"):
                command = desktop_notification_command(message)

        self.assertIn("'Codex user''s reset'", command[-1])
        self.assertIn("'It''s ready'", command[-1])


if __name__ == "__main__":
    unittest.main()
