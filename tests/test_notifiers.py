import asyncio
import base64
import contextlib
import io
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from codex_reset_tracker import notifiers
from codex_reset_tracker.config import NotificationsConfig
from codex_reset_tracker.models import TweetMatch, TweetRecord
from codex_reset_tracker.notifiers import (
    AlertMessage,
    NotificationError,
    NotificationManager,
    desktop_notification_backend,
    desktop_notification_command,
    format_alert,
    resolve_alert_title,
)


class RecordingNotifier:
    def __init__(self, name: str, calls: list[str], error: Exception | None = None):
        self.name = name
        self.calls = calls
        self.error = error

    async def send(self, message: AlertMessage) -> None:
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error


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
    def test_notification_manager_skips_channels_without_attempting_them(self):
        calls: list[str] = []
        notifiers_for_test = [
            RecordingNotifier("telegram", calls),
            RecordingNotifier("desktop", calls),
        ]
        with patch(
            "codex_reset_tracker.notifiers.build_notifiers",
            return_value=notifiers_for_test,
        ):
            manager = NotificationManager(NotificationsConfig())

        self.assertEqual(manager.channel_names, frozenset({"telegram", "desktop"}))
        result = asyncio.run(manager.send_match(match(), skip_channels={"telegram"}))
        self.assertEqual(result, {"desktop": {"ok": True}})
        self.assertEqual(calls, ["desktop"])

        calls.clear()
        self.assertEqual(
            asyncio.run(
                manager.send_match(match(), skip_channels=manager.channel_names)
            ),
            {},
        )
        self.assertEqual(calls, [])

        with patch("codex_reset_tracker.notifiers.build_notifiers", return_value=[]):
            unconfigured = NotificationManager(NotificationsConfig(channels={}))
        with self.assertRaises(NotificationError) as error:
            asyncio.run(unconfigured.send_match(match()))
        self.assertIsNone(error.exception.delivery)

    def test_notification_manager_raises_when_all_attempted_channels_fail(self):
        calls: list[str] = []
        failing = RecordingNotifier("desktop", calls, RuntimeError("offline"))
        with patch(
            "codex_reset_tracker.notifiers.build_notifiers", return_value=[failing]
        ):
            manager = NotificationManager(NotificationsConfig())

        with self.assertLogs("codex_reset_tracker.notifiers", level="ERROR"):
            with self.assertRaises(NotificationError) as error:
                asyncio.run(manager.send_match(match()))
        self.assertEqual(
            error.exception.delivery,
            {"desktop": {"ok": False, "error": "offline"}},
        )
        self.assertEqual(calls, ["desktop"])

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

    def test_unknown_authors_get_the_product_their_tweet_talks_about_most(self):
        def title_for(text):
            tweet = TweetRecord(
                id="9",
                author_username="someone",
                author_name="Some One",
                text=text,
                created_at="today",
                url="https://x.com/someone/status/9",
                source="test",
            )
            found = TweetMatch(tweet=tweet, matched_patterns=("reset",), excerpt=text)
            return resolve_alert_title("Potential quota reset", found)

        self.assertEqual(title_for("Opus and Sonnet limits reset, Claude Code too"), "Potential Claude quota reset")
        self.assertEqual(title_for("Codex limits reset, GPT-5 included"), "Potential Codex quota reset")
        # a tweet that weighs both says nothing about which one reset, so the generic title stays
        self.assertEqual(title_for("Codex limits reset while Claude's did not"), "Potential quota reset")

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
                with patch("codex_reset_tracker.notifiers.shutil.which", return_value="powershell.exe"):
                    command = desktop_notification_command(message)
                    backend = desktop_notification_backend()

        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-ExecutionPolicy", command)
        self.assertIn("ToastNotificationManager", command[-1])
        self.assertEqual(backend, "wsl-windows")

    def test_wsl_desktop_notification_uses_windows_path_when_service_path_is_minimal(self):
        message = AlertMessage(
            title="Codex reset",
            body="Quota reset from WSL",
            url="https://x.com/OpenAI/status/123",
            payload={},
        )

        def fake_exists(self):
            return str(self) == "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

        with patch("codex_reset_tracker.notifiers.platform.system", return_value="Linux"):
            with patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}):
                with patch("codex_reset_tracker.notifiers.shutil.which", return_value=None):
                    with patch("codex_reset_tracker.notifiers.Path.exists", fake_exists):
                        command = desktop_notification_command(message)
                        backend = desktop_notification_backend()

        self.assertEqual(command[0], "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        self.assertEqual(backend, "wsl-windows")

    def test_powershell_toast_xml_is_encoded_and_round_trips(self):
        excerpt = "It’s ready; ’); Write-Output 'INJECTED'; $env:COMSPEC; #"
        tweet_id = "123’); Write-Output 'TAG-INJECTED'; #"
        message = AlertMessage(
            title="Codex user's reset",
            body=excerpt,
            url="https://x.com/OpenAI/status/123",
            payload={
                "tweet_id": tweet_id,
                "author_username": "thsottiaux",
                "excerpt": excerpt,
            },
        )

        with patch(
            "codex_reset_tracker.notifiers.platform.system", return_value="Windows"
        ):
            with patch(
                "codex_reset_tracker.notifiers.shutil.which",
                return_value="powershell.exe",
            ):
                command = desktop_notification_command(message)

        script = command[-1]
        self.assertIn("[System.Convert]::FromBase64String", script)
        self.assertNotIn("It’s ready", script)
        self.assertNotIn("INJECTED", script)
        self.assertNotIn("COMSPEC", script)
        self.assertNotIn("TAG-INJECTED", script)

        decoded_values = [
            base64.b64decode(value).decode("utf-8")
            for value in re.findall(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)
        ]
        xml = next(value for value in decoded_values if value.startswith("<toast"))
        self.assertEqual(decoded_values[-1], tweet_id)
        root = ElementTree.fromstring(xml)
        self.assertEqual(
            [element.text for element in root.iter("text")],
            ["Codex user's reset", excerpt, "@thsottiaux"],
        )

        powershell = shutil.which("pwsh") or notifiers._windows_powershell_executable(
            wsl=notifiers._is_wsl()
        )
        if powershell:
            xml_statement = next(
                part for part in script.split("; ") if part.startswith("$xml.LoadXml(")
            )
            expression = xml_statement[len("$xml.LoadXml(") : -1]
            round_trip = (
                "$ErrorActionPreference = 'Stop'; "
                f"$value = {expression}; "
                "$document = New-Object System.Xml.XmlDocument; "
                "$document.LoadXml($value); "
                "[Console]::Write([System.Convert]::ToBase64String("
                "[System.Text.Encoding]::UTF8.GetBytes($value)))"
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", round_trip],
                capture_output=True,
                check=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(
                base64.b64decode(result.stdout.strip()).decode("utf-8"), xml
            )

    def test_toast_shows_the_tweet_author_window_and_a_link_to_open_it(self):
        message = AlertMessage(
            title="Potential Codex quota reset",
            body="Potential Codex quota reset\n\nAuthor: @OpenAI\n\nlong body repeating the title",
            url="https://x.com/OpenAI/status/123",
            payload={
                "tweet_id": "123",
                "excerpt": "Codex limits reset   tonight",
                "author_username": "OpenAI",
                "reset_window": {
                    "user_start_at": "19:00",
                    "user_end_at": "21:00",
                    "user_timezone": "Asia/Saigon",
                },
            },
        )

        with patch(
            "codex_reset_tracker.notifiers.platform.system", return_value="Windows"
        ):
            with patch(
                "codex_reset_tracker.notifiers.shutil.which",
                return_value="powershell.exe",
            ):
                script = desktop_notification_command(message)[-1]

        decoded_values = [
            base64.b64decode(value).decode("utf-8")
            for value in re.findall(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)
        ]
        xml = next(value for value in decoded_values if value.startswith("<toast"))
        self.assertIn("<text>Potential Codex quota reset</text>", xml)
        self.assertIn(
            "Codex limits reset tonight", xml
        )  # the tweet's words, whitespace collapsed
        self.assertNotIn("long body repeating the title", xml)
        self.assertIn("@OpenAI · reset 19:00 to 21:00 (Asia/Saigon)", xml)
        self.assertIn(
            'content="Open tweet" activationType="protocol" arguments="https://x.com/OpenAI/status/123"',
            xml,
        )
        self.assertEqual(decoded_values[-1], "123")

    def test_toast_offers_no_link_for_a_url_that_is_not_http(self):
        message = AlertMessage(
            title="t", body="b", url="javascript:alert(1)", payload={}
        )

        with patch(
            "codex_reset_tracker.notifiers.platform.system", return_value="Windows"
        ):
            with patch(
                "codex_reset_tracker.notifiers.shutil.which",
                return_value="powershell.exe",
            ):
                script = desktop_notification_command(message)[-1]

        xml = next(
            base64.b64decode(value).decode("utf-8")
            for value in re.findall(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)
            if base64.b64decode(value).startswith(b"<toast")
        )
        self.assertNotIn("Open tweet", xml)
        self.assertNotIn("javascript", xml)


if __name__ == "__main__":
    unittest.main()
