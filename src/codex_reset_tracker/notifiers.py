from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import smtplib
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

from .config import NotificationsConfig
from .models import TweetMatch

LOGGER = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    async def send(self, message: "AlertMessage") -> None:
        ...


@dataclass(frozen=True)
class AlertMessage:
    title: str
    body: str
    url: str
    payload: dict[str, Any]


class NotificationError(RuntimeError):
    pass


class NotificationManager:
    def __init__(self, config: NotificationsConfig):
        self.config = config
        self.notifiers = build_notifiers(config)

    async def send_match(self, match: TweetMatch) -> dict[str, Any]:
        if not self.notifiers:
            raise NotificationError("No notification channels are enabled")

        message = format_alert(self.config.title, match)
        results: dict[str, Any] = {}
        successes = 0
        for notifier in self.notifiers:
            try:
                await notifier.send(message)
            except Exception as exc:
                LOGGER.exception("notification channel %s failed", notifier.name)
                results[notifier.name] = {"ok": False, "error": str(exc)}
            else:
                successes += 1
                results[notifier.name] = {"ok": True}

        if successes == 0:
            raise NotificationError(f"All notification channels failed: {results}")
        return results


def build_notifiers(config: NotificationsConfig) -> list[Notifier]:
    channels = config.channels
    notifiers: list[Notifier] = []
    if _enabled(channels, "stdout"):
        notifiers.append(StdoutNotifier())
    if _enabled(channels, "telegram"):
        notifiers.append(TelegramNotifier(channels["telegram"]))
    if _enabled(channels, "email"):
        notifiers.append(EmailNotifier(channels["email"]))
    if _enabled(channels, "webhook"):
        notifiers.append(WebhookNotifier(channels["webhook"]))
    if _enabled(channels, "desktop"):
        notifiers.append(DesktopNotifier())
    return notifiers


def _enabled(channels: dict[str, dict[str, Any]], name: str) -> bool:
    return bool(channels.get(name, {}).get("enabled", False))


def format_alert(title: str, match: TweetMatch) -> AlertMessage:
    tweet = match.tweet
    window = match.reset_window
    window_text = ""
    if window is not None:
        evidence = ", ".join(window.evidence)
        window_text = (
            f"Approx reset window: {window.user_start_at} to {window.user_end_at} "
            f"({window.user_timezone}; translated from {window.source_start_at} "
            f"to {window.source_end_at} {window.source_timezone}; "
            f"{window.confidence} confidence; cue: {evidence})\n"
        )
    body = (
        f"{title}\n\n"
        f"Author: @{tweet.author_username} ({tweet.author_name})\n"
        f"Source: {tweet.source}\n"
        f"Matched: {match.pattern_summary}\n"
        f"{window_text}"
        f"Created: {tweet.created_at or 'unknown'}\n"
        f"URL: {tweet.url}\n\n"
        f"{match.excerpt}"
    )
    payload = {
        "title": title,
        "author_username": tweet.author_username,
        "author_name": tweet.author_name,
        "tweet_id": tweet.id,
        "tweet_url": tweet.url,
        "source": tweet.source,
        "created_at": tweet.created_at,
        "matched_patterns": list(match.matched_patterns),
        "excerpt": match.excerpt,
        "reset_window": None
        if window is None
        else {
            "label": window.label,
            "source_start_at": window.source_start_at,
            "source_end_at": window.source_end_at,
            "source_timezone": window.source_timezone,
            "user_start_at": window.user_start_at,
            "user_end_at": window.user_end_at,
            "user_timezone": window.user_timezone,
            "confidence": window.confidence,
            "evidence": list(window.evidence),
        },
    }
    return AlertMessage(title=title, body=body, url=tweet.url, payload=payload)


class StdoutNotifier:
    name = "stdout"

    async def send(self, message: AlertMessage) -> None:
        print(message.body, flush=True)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, config: dict[str, Any]):
        self.bot_token = _required_env(config, "bot_token_env")
        self.chat_id = _required_env(config, "chat_id_env")

    async def send(self, message: AlertMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: AlertMessage) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": message.body[:4000],
                "disable_web_page_preview": "false",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise NotificationError(f"Telegram returned HTTP {response.status}")


class EmailNotifier:
    name = "email"

    def __init__(self, config: dict[str, Any]):
        self.smtp_host = _required_env(config, "smtp_host_env")
        self.smtp_port = int(config.get("smtp_port", 587))
        self.smtp_username = _optional_env(config, "smtp_username_env")
        self.smtp_password = _optional_env(config, "smtp_password_env")
        self.sender = _required_env(config, "from_env")
        self.recipients = _csv(_required_env(config, "to_env"))
        self.starttls = bool(config.get("starttls", True))

    async def send(self, message: AlertMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: AlertMessage) -> None:
        email = EmailMessage()
        email["Subject"] = message.title
        email["From"] = self.sender
        email["To"] = ", ".join(self.recipients)
        email.set_content(message.body)

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as server:
            if self.starttls:
                server.starttls()
            if self.smtp_username:
                server.login(self.smtp_username, self.smtp_password or "")
            server.send_message(email)


class WebhookNotifier:
    name = "webhook"

    def __init__(self, config: dict[str, Any]):
        self.url = _required_env(config, "url_env")
        self.format = str(config.get("format", "generic")).lower()

    async def send(self, message: AlertMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: AlertMessage) -> None:
        if self.format == "discord":
            payload = {"content": message.body[:1900]}
        elif self.format == "slack":
            payload = {"text": message.body}
        else:
            payload = message.payload | {"body": message.body}

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status >= 300:
                    raise NotificationError(f"Webhook returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise NotificationError(f"Webhook returned HTTP {exc.code}") from exc


class DesktopNotifier:
    name = "desktop"

    async def send(self, message: AlertMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: AlertMessage) -> None:
        system = platform.system().lower()
        if system == "linux":
            if not shutil.which("notify-send"):
                raise NotificationError("notify-send is not installed")
            subprocess.run(
                ["notify-send", message.title, message.body[:700]],
                check=True,
                timeout=10,
            )
        elif system == "darwin":
            script = (
                'display notification '
                f"{json.dumps(message.body[:700])} "
                "with title "
                f"{json.dumps(message.title)}"
            )
            subprocess.run(["osascript", "-e", script], check=True, timeout=10)
        elif system == "windows":
            escaped_title = message.title.replace("'", "''")
            escaped_body = message.body[:700].replace("'", "''")
            command = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.BalloonTipTitle = '{title}'; "
                "$n.BalloonTipText = '{body}'; "
                "$n.Visible = $true; $n.ShowBalloonTip(5000); "
                "Start-Sleep -Seconds 6; $n.Dispose()"
            ).format(title=escaped_title, body=escaped_body)
            subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, timeout=15)
        else:
            raise NotificationError(f"Desktop notification is not supported on {system}")


def _required_env(config: dict[str, Any], key: str) -> str:
    env_name = str(config.get(key, "")).strip()
    if not env_name:
        raise NotificationError(f"Missing config key: {key}")
    value = os.getenv(env_name)
    if not value:
        raise NotificationError(f"Environment variable is required: {env_name}")
    return value


def _optional_env(config: dict[str, Any], key: str) -> str | None:
    env_name = str(config.get(key, "")).strip()
    if not env_name:
        return None
    value = os.getenv(env_name)
    return value or None


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
