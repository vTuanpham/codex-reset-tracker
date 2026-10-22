from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .accounts import account_group_for_handle
from .config import NotificationsConfig
from .models import TweetMatch

LOGGER = logging.getLogger(__name__)
TOAST_APP_ID = "codex-reset-tracker"
TOAST_ICON = Path(__file__).resolve().parents[2] / "assets" / "icon.png"


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

        message = format_alert(resolve_alert_title(self.config.title, match), match)
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


def resolve_alert_title(base_title: str, match: TweetMatch) -> str:
    product = _detect_product(match)
    if product is None:
        return base_title
    if re.search(r"\b(?:codex|claude)\b", base_title, flags=re.IGNORECASE):
        return re.sub(
            r"\b(?:codex|claude)\b",
            product,
            base_title,
            count=1,
            flags=re.IGNORECASE,
        )
    if re.search(r"\bquota\b", base_title, flags=re.IGNORECASE):
        return re.sub(
            r"\bquota\b",
            f"{product} quota",
            base_title,
            count=1,
            flags=re.IGNORECASE,
        )
    return f"{base_title} ({product})"


def _detect_product(match: TweetMatch) -> str | None:
    tweet = match.tweet
    group = account_group_for_handle(tweet.author_username)
    if group is not None:
        if group.startswith("anthropic"):
            return "Claude"
        if group.startswith("openai"):
            return "Codex"

    text = " ".join(
        [
            tweet.author_username,
            tweet.author_name,
            tweet.text,
            match.excerpt,
        ]
    ).lower()
    if "claude" in text or "anthropic" in text:
        return "Claude"
    if "codex" in text or "openai" in text or "chatgpt" in text:
        return "Codex"
    return None


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


class DesktopNotifier:
    name = "desktop"

    async def send(self, message: AlertMessage) -> None:
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: AlertMessage) -> None:
        command = desktop_notification_command(message)
        subprocess.run(command, check=True, timeout=15)


def desktop_notification_command(message: AlertMessage) -> list[str]:
    system = platform.system().lower()
    if system == "linux" and _is_wsl():
        executable = _windows_powershell_executable(wsl=True)
        if executable is None:
            raise NotificationError(
                "WSL desktop notification requires Windows interop and powershell.exe. "
                "Make sure WSL interop is enabled and powershell.exe is available on PATH."
            )
        return _windows_notification_command(message, executable)
    if system == "linux":
        if not shutil.which("notify-send"):
            raise NotificationError(
                "notify-send is not installed. On WSL, enable Windows interop so "
                "powershell.exe is available, or install notify-send for native Linux notifications."
            )
        return ["notify-send", message.title, message.body[:700]]
    if system == "darwin":
        script = (
            'display notification '
            f"{json.dumps(message.body[:700])} "
            "with title "
            f"{json.dumps(message.title)}"
        )
        return ["osascript", "-e", script]
    if system == "windows":
        executable = _windows_powershell_executable(wsl=False)
        if executable is None:
            raise NotificationError("Windows desktop notification requires powershell.exe")
        return _windows_notification_command(message, executable)
    raise NotificationError(f"Desktop notification is not supported on {system}")


def desktop_notification_backend() -> str:
    system = platform.system().lower()
    if system == "linux" and _is_wsl():
        return "wsl-windows" if _windows_powershell_executable(wsl=True) else "wsl-windows-unavailable"
    if system == "linux":
        return "linux-notify-send" if shutil.which("notify-send") else "linux-notify-send-unavailable"
    if system == "darwin":
        return "macos-osascript" if shutil.which("osascript") else "macos-osascript-unavailable"
    if system == "windows":
        return "windows-powershell" if _windows_powershell_executable(wsl=False) else "windows-powershell-unavailable"
    return f"unsupported-{system}"


def desktop_notifications_available() -> bool:
    backend = desktop_notification_backend()
    return not backend.endswith("-unavailable") and not backend.startswith("unsupported-")


def _windows_powershell_executable(*, wsl: bool) -> str | None:
    candidates = (
        (
            "powershell.exe",
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "/mnt/c/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe",
        )
        if wsl
        else ("powershell.exe", "powershell")
    )
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate)
        if path.is_absolute() and path.exists():
            return candidate
    return None


def _windows_notification_command(message: AlertMessage, executable: str) -> list[str]:
    """A Windows toast under its own name and icon, with the tweet one click away."""
    text, footer = _toast_text(message)
    url = message.url if message.url.startswith(("https://", "http://")) else ""
    xml = _toast_xml(message.title, text, footer, url)
    tag = str((message.payload or {}).get("tweet_id") or "alert")[:60]  # a repeat of one tweet replaces it
    key = rf"HKCU:\Software\Classes\AppUserModelId\{TOAST_APP_ID}"
    command = "; ".join(
        [
            f"$key = {_powershell_single_quoted(key)}",
            "if (-not (Test-Path $key)) { New-Item $key -Force | Out-Null }",
            "Set-ItemProperty $key -Name DisplayName -Value 'Codex Reset Tracker'",
            "Set-ItemProperty $key -Name ShowInSettings -Value 0 -Type DWord",
            f"$src = {_powershell_single_quoted(_windows_path(TOAST_ICON))}",
            # Toasts read the icon from a local copy; a \\wsl$ path may not be readable.
            "if ($src -and (Test-Path $src)) { "
            f"$dir = Join-Path $env:LOCALAPPDATA '{TOAST_APP_ID}'; "
            "New-Item -ItemType Directory -Force $dir | Out-Null; "
            "$icon = Join-Path $dir 'icon.png'; Copy-Item $src $icon -Force; "
            "Set-ItemProperty $key -Name IconUri -Value $icon }",
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType=WindowsRuntime] | Out-Null",
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
            "ContentType=WindowsRuntime] | Out-Null",
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
            f"$xml.LoadXml({_powershell_single_quoted(xml)})",
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
            f"$toast.Tag = {_powershell_single_quoted(tag)}",
            f"$toast.Group = '{TOAST_APP_ID}'",
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP_ID}')"
            ".Show($toast)",
        ]
    )
    return [
        executable,
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]


def _toast_text(message: AlertMessage) -> tuple[str, str]:
    """The tweet's own words, and a one-line footer with who said it and the reset window."""
    payload = message.payload or {}
    text = " ".join((payload.get("excerpt") or message.body).split())
    if text.startswith(message.title):  # a body that repeats the title says nothing new
        text = text[len(message.title) :].lstrip()
    footer = [f"@{payload['author_username']}"] if payload.get("author_username") else []
    window = payload.get("reset_window")
    if window:
        footer.append(f"reset {window['user_start_at']} to {window['user_end_at']} ({window['user_timezone']})")
    return text[:220], " · ".join(footer)


def _toast_xml(title: str, text: str, footer: str, url: str) -> str:
    def esc(value: str) -> str:
        return html.escape(value, quote=True)

    launch = f' activationType="protocol" launch="{esc(url)}"' if url else ""
    open_button = f'<action content="Open tweet" activationType="protocol" arguments="{esc(url)}"/>' if url else ""
    subtle = f'<text hint-style="captionSubtle">{esc(footer)}</text>' if footer else ""
    return (
        f'<toast scenario="reminder"{launch}><visual><binding template="ToastGeneric">'
        f'<text>{esc(title)}</text><text hint-maxLines="4">{esc(text)}</text>{subtle}'
        "</binding></visual>"
        f'<actions>{open_button}<action content="Dismiss" activationType="system" arguments="dismiss"/></actions>'
        '<audio src="ms-winsoundevent:Notification.Reminder"/></toast>'
    )


def _windows_path(path: Path) -> str:
    """The path as Windows sees it, or "" when it does not exist or cannot be translated."""
    if not path.is_file():
        return ""
    if platform.system().lower() == "windows":
        return str(path)
    try:
        done = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_wsl() -> bool:
    if os.getenv("WSL_INTEROP") or os.getenv("WSL_DISTRO_NAME"):
        return True
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return False
    return "microsoft" in release.lower() or "wsl" in release.lower()


def _required_env(config: dict[str, Any], key: str) -> str:
    env_name = str(config.get(key, "")).strip()
    if not env_name:
        raise NotificationError(f"Missing config key: {key}")
    value = os.getenv(env_name)
    if not value:
        raise NotificationError(f"Environment variable is required: {env_name}")
    return value
