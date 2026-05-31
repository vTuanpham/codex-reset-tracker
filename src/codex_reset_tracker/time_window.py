from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ResetWindow, TweetMatch


RELATIVE_RANGE_RE = re.compile(
    r"\b(?:within|in\s+the\s+next)\s+(\d{1,2})\s*(minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
RELATIVE_POINT_RE = re.compile(
    r"\bin\s+(\d{1,2})\s*(minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
AT_TIME_RE = re.compile(
    r"\b(?:at|around|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)


def attach_reset_window(
    match: TweetMatch,
    *,
    source_timezone_name: str,
    user_timezone_name: str,
    now: datetime | None = None,
) -> TweetMatch:
    if now is None:
        now = parse_created_at(match.tweet.created_at)
    window = estimate_reset_window(
        match.tweet.text,
        source_timezone_name=source_timezone_name,
        user_timezone_name=user_timezone_name,
        now=now,
    )
    if window is None:
        return match
    return replace(match, reset_window=window)


def estimate_reset_window(
    text: str,
    *,
    source_timezone_name: str,
    user_timezone_name: str,
    now: datetime | None = None,
) -> ResetWindow | None:
    source_tz = _zoneinfo(source_timezone_name)
    user_tz = _zoneinfo(user_timezone_name)
    current = _as_local(now, source_tz) if now else datetime.now(source_tz)
    normalized = re.sub(r"\s+", " ", text).strip()

    relative_range = _relative_range(normalized, current, source_tz, user_tz)
    if relative_range:
        return relative_range

    relative_point = _relative_point(normalized, current, source_tz, user_tz)
    if relative_point:
        return relative_point

    explicit_time = _explicit_time(normalized, current, source_tz, user_tz)
    if explicit_time:
        return explicit_time

    phrase_window = _phrase_window(normalized, current, source_tz, user_tz)
    if phrase_window:
        return phrase_window

    return None


def parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None

    if parsed is None:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _relative_range(
    text: str,
    current: datetime,
    source_tz: ZoneInfo,
    user_tz: ZoneInfo,
) -> ResetWindow | None:
    match = RELATIVE_RANGE_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    delta = _duration(amount, unit)
    return _window(
        "within " + match.group(1) + " " + match.group(2),
        current,
        current + delta,
        source_tz,
        user_tz,
        "high",
        (match.group(0),),
    )


def _relative_point(
    text: str,
    current: datetime,
    source_tz: ZoneInfo,
    user_tz: ZoneInfo,
) -> ResetWindow | None:
    match = RELATIVE_POINT_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    delta = _duration(amount, unit)
    center = current + delta
    margin = timedelta(minutes=10 if "min" in unit else 30)
    return _window(
        "around " + match.group(0),
        center - margin,
        center + margin,
        source_tz,
        user_tz,
        "medium",
        (match.group(0),),
    )


def _explicit_time(
    text: str,
    current: datetime,
    source_tz: ZoneInfo,
    user_tz: ZoneInfo,
) -> ResetWindow | None:
    match = AT_TIME_RE.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3).lower()
    if hour == 12:
        hour = 0
    if meridiem == "pm":
        hour += 12
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "tomorrow" in text.lower() or candidate < current - timedelta(minutes=15):
        candidate += timedelta(days=1)
    return _window(
        "around " + match.group(0),
        candidate - timedelta(minutes=45),
        candidate + timedelta(minutes=45),
        source_tz,
        user_tz,
        "medium",
        (match.group(0),),
    )


def _phrase_window(
    text: str,
    current: datetime,
    source_tz: ZoneInfo,
    user_tz: ZoneInfo,
) -> ResetWindow | None:
    lower = text.lower()
    today = current.date()
    tomorrow = today + timedelta(days=1)

    if re.search(r"\btomorrow\s+morning\b", lower):
        return _window(
            "tomorrow morning",
            _at(tomorrow, time(6, 0), source_tz),
            _at(tomorrow, time(12, 0), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("tomorrow morning",),
        )
    if re.search(r"\btomorrow\s+afternoon\b", lower):
        return _window(
            "tomorrow afternoon",
            _at(tomorrow, time(12, 0), source_tz),
            _at(tomorrow, time(17, 0), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("tomorrow afternoon",),
        )
    if re.search(r"\btomorrow\s+evening\b", lower):
        return _window(
            "tomorrow evening",
            _at(tomorrow, time(17, 0), source_tz),
            _at(tomorrow, time(21, 0), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("tomorrow evening",),
        )
    if re.search(r"\btomorrow\s+night\b", lower):
        return _window(
            "tomorrow night",
            _at(tomorrow, time(18, 0), source_tz),
            _at(tomorrow + timedelta(days=1), time(0, 30), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("tomorrow night",),
        )
    if re.search(r"\blater\s+today\b", lower):
        return _window(
            "later today",
            max(current + timedelta(minutes=30), _at(today, time(9, 0), source_tz)),
            _at(today, time(23, 59), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("later today",),
        )
    if re.search(r"\b(?:this\s+evening|evening)\b", lower):
        day = today if current <= _at(today, time(21, 0), source_tz) else tomorrow
        start = _at(day, time(17, 0), source_tz)
        if day == today:
            start = max(current, start)
        return _window(
            "this evening",
            start,
            _at(day, time(21, 0), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("evening",),
        )
    if re.search(r"\b(?:tonight|this\s+night)\b", lower):
        day = today if current <= _at(today, time(23, 59), source_tz) else tomorrow
        start = _at(day, time(18, 0), source_tz)
        if day == today:
            start = max(current, start)
        return _window(
            "tonight",
            start,
            _at(day + timedelta(days=1), time(0, 30), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("tonight",),
        )
    if re.search(r"\bthis\s+afternoon\b|\bafternoon\b", lower):
        day = today if current <= _at(today, time(17, 0), source_tz) else tomorrow
        start = _at(day, time(12, 0), source_tz)
        if day == today:
            start = max(current, start)
        return _window(
            "this afternoon",
            start,
            _at(day, time(17, 0), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("afternoon",),
        )
    if re.search(r"\bthis\s+morning\b|\bmorning\b", lower):
        day = today if current <= _at(today, time(12, 0), source_tz) else tomorrow
        start = _at(day, time(6, 0), source_tz)
        if day == today:
            start = max(current, start)
        return _window(
            "this morning",
            start,
            _at(day, time(12, 0), source_tz),
            source_tz,
            user_tz,
            "medium",
            ("morning",),
        )
    if re.search(r"\btomorrow\b", lower):
        return _window(
            "tomorrow",
            _at(tomorrow, time(0, 0), source_tz),
            _at(tomorrow, time(23, 59), source_tz),
            source_tz,
            user_tz,
            "low",
            ("tomorrow",),
        )
    if re.search(r"\btoday\b", lower):
        return _window(
            "today",
            current,
            _at(today, time(23, 59), source_tz),
            source_tz,
            user_tz,
            "low",
            ("today",),
        )
    if re.search(r"\bsoon\b|\bshortly\b", lower):
        return _window(
            "soon",
            current,
            current + timedelta(hours=4),
            source_tz,
            user_tz,
            "low",
            ("soon",),
        )
    return None


def _duration(amount: int, unit: str) -> timedelta:
    if unit.startswith("hour") or unit.startswith("hr"):
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


def _window(
    label: str,
    start: datetime,
    end: datetime,
    source_tz: ZoneInfo,
    user_tz: ZoneInfo,
    confidence: str,
    evidence: tuple[str, ...],
) -> ResetWindow:
    if end < start:
        end = start
    return ResetWindow(
        label=label,
        source_start_at=start.astimezone(source_tz).isoformat(timespec="minutes"),
        source_end_at=end.astimezone(source_tz).isoformat(timespec="minutes"),
        source_timezone=source_tz.key,
        user_start_at=start.astimezone(user_tz).isoformat(timespec="minutes"),
        user_end_at=end.astimezone(user_tz).isoformat(timespec="minutes"),
        user_timezone=user_tz.key,
        confidence=confidence,
        evidence=evidence,
    )


def _at(day, value: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, value, tzinfo=tz)


def _zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _as_local(value: datetime | None, tz: ZoneInfo) -> datetime:
    if value is None:
        return datetime.now(tz)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(tz)
