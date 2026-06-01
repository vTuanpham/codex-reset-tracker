from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .accounts import default_account_handles, default_account_timezones


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TwitterConfig:
    language: str = "en-US"
    proxy: str | None = None
    user_agent: str | None = None
    cookies_file: Path = Path("data/x_cookies.json")
    username_env: str = "CODQ_X_USERNAME"
    email_env: str = "CODQ_X_EMAIL"
    password_env: str = "CODQ_X_PASSWORD"
    totp_secret_env: str = "CODQ_X_TOTP_SECRET"

    @property
    def username(self) -> str | None:
        return os.getenv(self.username_env)

    @property
    def email(self) -> str | None:
        return os.getenv(self.email_env)

    @property
    def password(self) -> str | None:
        return os.getenv(self.password_env)

    @property
    def totp_secret(self) -> str | None:
        return os.getenv(self.totp_secret_env)


@dataclass(frozen=True)
class TimeConfig:
    user_timezone: str = "auto"
    default_source_timezone: str = "America/Los_Angeles"
    account_timezones: dict[str, str] = field(default_factory=default_account_timezones)

    def source_timezone_for(self, username: str) -> str:
        normalized = username.strip().lstrip("@").lower()
        for handle, timezone_name in self.account_timezones.items():
            if handle.strip().lstrip("@").lower() == normalized:
                return timezone_name
        return self.default_source_timezone


@dataclass(frozen=True)
class PollingConfig:
    interval_seconds: int = 1200
    jitter_seconds: int = 45
    request_delay_seconds: float = 2.0
    alert_on_first_scan: bool = False
    new_tweet_grace_seconds: int = 180
    tweet_count_per_account: int = 20
    search_count_per_query: int = 20
    max_alerts_per_scan: int = 10
    accounts: tuple[str, ...] = (
        *default_account_handles(),
    )
    search_queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchingConfig:
    case_sensitive: bool = False
    require_all_include_patterns: bool = True
    context_window_chars: int = 220
    include_patterns: tuple[str, ...] = (
        r"\breset(?:s|ting|ted)?\b",
    )
    exclude_patterns: tuple[str, ...] = (
        r"\b(?:password\s+reset|reset\s+(?:your\s+)?password|factory\s+reset|hard\s+reset|account\s+recovery)\b",
    )


@dataclass(frozen=True)
class NotificationsConfig:
    title: str = "Potential Codex quota reset"
    channels: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {"stdout": {"enabled": True}}
    )


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path = Path("data")
    state_path: Path = Path("data/state.sqlite3")
    runtime_dir: Path = Path("data/runtime")
    local_timezone: str = "auto"
    time: TimeConfig = field(default_factory=TimeConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


def load_config(path: Path) -> AppConfig:
    load_env(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON config {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object")

    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    data_dir = Path(_get(raw, "data_dir", "data"))
    state_path = Path(_get(raw, "state_path", str(data_dir / "state.sqlite3")))
    runtime_dir = Path(_get(raw, "runtime_dir", str(data_dir / "runtime")))

    twitter_raw = _dict(raw.get("twitter"), "twitter")
    polling_raw = _dict(raw.get("polling"), "polling")
    time_raw = _dict(raw.get("time"), "time")
    matching_raw = _dict(raw.get("matching"), "matching")
    notifications_raw = _dict(raw.get("notifications"), "notifications")

    local_timezone = _resolve_timezone_name(_get(raw, "local_timezone", "auto"))
    user_timezone = _resolve_timezone_name(
        _get(time_raw, "user_timezone", _get(raw, "local_timezone", "auto"))
    )
    accounts = _string_tuple(
        _get(polling_raw, "accounts", list(PollingConfig().accounts)),
        "polling.accounts",
    )
    require_all_include_patterns = bool(
        _get(matching_raw, "require_all_include_patterns", True)
    )

    return AppConfig(
        data_dir=data_dir,
        state_path=state_path,
        runtime_dir=runtime_dir,
        local_timezone=local_timezone,
        time=TimeConfig(
            user_timezone=user_timezone,
            default_source_timezone=str(
                _get(time_raw, "default_source_timezone", "America/Los_Angeles")
            ),
            account_timezones=_string_dict(
                _get(time_raw, "account_timezones", TimeConfig().account_timezones),
                "time.account_timezones",
            ),
        ),
        twitter=TwitterConfig(
            language=str(_get(twitter_raw, "language", "en-US")),
            proxy=_optional_str(twitter_raw.get("proxy")),
            user_agent=_optional_str(twitter_raw.get("user_agent")),
            cookies_file=Path(_get(twitter_raw, "cookies_file", data_dir / "x_cookies.json")),
            username_env=str(_get(twitter_raw, "username_env", "CODQ_X_USERNAME")),
            email_env=str(_get(twitter_raw, "email_env", "CODQ_X_EMAIL")),
            password_env=str(_get(twitter_raw, "password_env", "CODQ_X_PASSWORD")),
            totp_secret_env=str(_get(twitter_raw, "totp_secret_env", "CODQ_X_TOTP_SECRET")),
        ),
        polling=PollingConfig(
            interval_seconds=_positive_int(_get(polling_raw, "interval_seconds", 1200), "polling.interval_seconds"),
            jitter_seconds=_nonnegative_int(_get(polling_raw, "jitter_seconds", 45), "polling.jitter_seconds"),
            request_delay_seconds=_nonnegative_float(
                _get(polling_raw, "request_delay_seconds", 2.0),
                "polling.request_delay_seconds",
            ),
            alert_on_first_scan=bool(_get(polling_raw, "alert_on_first_scan", False)),
            new_tweet_grace_seconds=_nonnegative_int(
                _get(polling_raw, "new_tweet_grace_seconds", 180),
                "polling.new_tweet_grace_seconds",
            ),
            tweet_count_per_account=_positive_int(
                _get(polling_raw, "tweet_count_per_account", 20),
                "polling.tweet_count_per_account",
            ),
            search_count_per_query=_positive_int(
                _get(polling_raw, "search_count_per_query", 20),
                "polling.search_count_per_query",
            ),
            max_alerts_per_scan=_positive_int(
                _get(polling_raw, "max_alerts_per_scan", 10),
                "polling.max_alerts_per_scan",
            ),
            accounts=accounts,
            search_queries=_string_tuple(
                _get(polling_raw, "search_queries", []),
                "polling.search_queries",
            ),
        ),
        matching=MatchingConfig(
            case_sensitive=bool(_get(matching_raw, "case_sensitive", False)),
            require_all_include_patterns=require_all_include_patterns,
            context_window_chars=_positive_int(
                _get(matching_raw, "context_window_chars", 220),
                "matching.context_window_chars",
            ),
            include_patterns=_string_tuple(
                _get(matching_raw, "include_patterns", list(MatchingConfig().include_patterns)),
                "matching.include_patterns",
            ),
            exclude_patterns=_string_tuple(
                _get(matching_raw, "exclude_patterns", list(MatchingConfig().exclude_patterns)),
                "matching.exclude_patterns",
            ),
        ),
        notifications=NotificationsConfig(
            title=str(_get(notifications_raw, "title", "Potential Codex quota reset")),
            channels=_channels(notifications_raw.get("channels")),
        ),
    )


def load_env(config_path: Path | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    if config_path is not None:
        load_dotenv(config_path.parent / ".env", override=False)
    load_dotenv(Path(".env"), override=False)


def detect_local_timezone(default: str = "UTC") -> str:
    env_timezone = os.getenv("TZ")
    if _valid_timezone(env_timezone):
        return str(env_timezone)

    timezone_file = Path("/etc/timezone")
    try:
        timezone_name = timezone_file.read_text(encoding="utf-8").strip()
    except OSError:
        timezone_name = ""
    if _valid_timezone(timezone_name):
        return timezone_name

    localtime = Path("/etc/localtime")
    try:
        target = localtime.resolve()
    except OSError:
        target = Path()
    marker = "/usr/share/zoneinfo/"
    target_text = str(target)
    if marker in target_text:
        timezone_name = target_text.split(marker, 1)[1]
        if _valid_timezone(timezone_name):
            return timezone_name

    tzinfo = datetime.now().astimezone().tzinfo
    timezone_name = getattr(tzinfo, "key", None) or str(tzinfo)
    if _valid_timezone(timezone_name):
        return str(timezone_name)
    return default


def _resolve_timezone_name(value: Any) -> str:
    timezone_name = str(value or "auto").strip()
    if timezone_name.lower() == "auto":
        return detect_local_timezone()
    if not is_valid_timezone(timezone_name):
        raise ConfigError(f"Invalid timezone: {timezone_name}")
    return timezone_name


def is_valid_timezone(value: str | None) -> bool:
    return _valid_timezone(value)


def _valid_timezone(value: str | None) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(str(value))
    except ZoneInfoNotFoundError:
        return False
    return True


def _get(raw: dict[str, Any], key: str, default: Any) -> Any:
    return raw[key] if key in raw else default


def _dict(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


def _channels(value: Any) -> dict[str, dict[str, Any]]:
    if value is None:
        return {"stdout": {"enabled": True}}
    if not isinstance(value, dict):
        raise ConfigError("notifications.channels must be a JSON object")
    channels: dict[str, dict[str, Any]] = {}
    for name, channel_config in value.items():
        if not isinstance(channel_config, dict):
            raise ConfigError(f"notifications.channels.{name} must be a JSON object")
        channels[str(name)] = dict(channel_config)
    return channels


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list of strings")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return tuple(result)


def _string_dict(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        handle = str(key).strip().lstrip("@")
        timezone_name = str(item).strip()
        if handle and timezone_name:
            result[handle] = timezone_name
    return result


def _positive_int(value: Any, name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ConfigError(f"{name} must be > 0")
    return parsed


def _nonnegative_int(value: Any, name: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ConfigError(f"{name} must be >= 0")
    return parsed


def _nonnegative_float(value: Any, name: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ConfigError(f"{name} must be >= 0")
    return parsed
