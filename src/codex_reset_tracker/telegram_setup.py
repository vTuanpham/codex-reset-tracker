"""`codex-reset-tracker telegram`: connect a Telegram bot in one go, with no hand-editing of .env or JSON.

The user pastes the bot token (hidden) and sends the bot one message; the chat id is read from Telegram."""

from __future__ import annotations

import getpass
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .ops import OpsError

TOKEN_ENV = "CODQ_TELEGRAM_BOT_TOKEN"
CHAT_ENV = "CODQ_TELEGRAM_CHAT_ID"
WAIT_S = 120


def call(token: str, method: str, **params) -> dict | list:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=40) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
        if exc.code == 401:
            raise OpsError("Telegram rejected that token; copy it again from @BotFather (/mybots)") from exc
    except urllib.error.URLError as exc:
        raise OpsError(f"could not reach Telegram: {exc.reason}") from exc
    if not body.get("ok"):
        raise OpsError(body.get("description", "Telegram refused the request"))
    return body["result"]


def find_chat_id(updates: list) -> str | None:
    """The chat of the newest private message sent to the bot."""
    for update in reversed(updates):
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private" and "id" in chat:
            return str(chat["id"])
    return None


def upsert_env(path: Path, values: dict[str, str]) -> None:
    """Set KEY=value lines in a .env file, keeping every other line as it is."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for key, value in values.items():
        lines = [line for line in lines if not line.startswith(f"{key}=")] + [f"{key}={value}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)  # it holds a bot token


def enable_in_config(config_path: Path) -> None:
    if not config_path.exists():
        raise OpsError(f"{config_path} does not exist; run `uv run codex-reset-tracker init-config` first")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    channels = config.setdefault("notifications", {}).setdefault("channels", {})
    channels.setdefault("telegram", {}).update(enabled=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if line.startswith(f"{key}="):
            return line.partition("=")[2].strip().strip("\"'")
    return ""


def setup(config_path: Path, env_path: Path, *, ask=getpass.getpass, say=print) -> None:
    token = os.getenv(TOKEN_ENV) or env_value(env_path, TOKEN_ENV)
    if not token:
        say("Open Telegram, message @BotFather, send /newbot, and copy the token it gives you.")
        token = ask("Bot token (input is hidden): ").strip()
    bot = call(token, "getMe")
    say(f"Connected to @{bot['username']}.")
    say(f"Now open Telegram and send any message to @{bot['username']}. Waiting up to {WAIT_S // 60} minutes...")
    chat_id, deadline = None, time.monotonic() + WAIT_S
    while chat_id is None and time.monotonic() < deadline:
        chat_id = find_chat_id(call(token, "getUpdates", timeout=25, limit=20))
    if chat_id is None:
        raise OpsError(f"no message reached @{bot['username']}; send it one and run this command again")
    upsert_env(env_path, {TOKEN_ENV: token, CHAT_ENV: chat_id})
    enable_in_config(config_path)
    call(token, "sendMessage", chat_id=chat_id, text="Codex Reset Tracker is connected. Reset alerts will arrive here.")
    say(f"Saved to {env_path} and enabled in {config_path}. A confirmation message is on its way.")
    say("If the service is already running, restart it: uv run codex-reset-tracker service restart")
