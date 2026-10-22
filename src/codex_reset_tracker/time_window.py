from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ResetWindow, TweetMatch

# Zone words that can follow a clock time ("3pm PT"). Ambiguous ones (CST, IST) are left out on purpose.
ZONES = {
    "pt": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "mt": "America/Denver",
    "mdt": "America/Denver",
    "ct": "America/Chicago",
    "cdt": "America/Chicago",
    "et": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "utc": "UTC",
    "gmt": "UTC",
    "bst": "Europe/London",
    "cet": "Europe/Paris",
    "cest": "Europe/Paris",
    "jst": "Asia/Tokyo",
}
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
FUZZY_WORDS = {"couple": 2, "few": 3, "several": 4}

NUM = r"\d+(?:\.\d+)?|an?|one|two|three|four|five|six|(?:a\s+)?couple(?:\s+of)?|(?:a\s+)?few|several|half\s+an?"
QTY = rf"(?P<lo>{NUM})(?:\s*(?:-|–|to|or)\s*(?P<hi>{NUM}))?"
UNIT = r"(?P<unit>min(?:ute)?s?|hours?|hrs?|days?)\b"
LEAD = r"(?:(?:about|around|roughly|approximately|approx\.?|~|just\s+under|under|another)\s*)?"
WITHIN_RE = re.compile(
    rf"\b(?:within|in\s+the\s+next|over\s+the\s+next|in\s+the\s+coming)\s+(?:the\s+)?{LEAD}(?:{QTY}\s*)?{UNIT}",
    re.IGNORECASE,
)
IN_RE = re.compile(rf"\b(?:in|after)\s+{LEAD}{QTY}\s*{UNIT}", re.IGNORECASE)

ZONE = rf"(?:\s*\b(?P<tz>{'|'.join(sorted(ZONES, key=len, reverse=True))})\b)?"
MERIDIEM = r"(?P<{}>[ap]\.?m\b\.?)"
RANGE_RE = re.compile(
    r"(?<![\d:/.$])(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*" + MERIDIEM.format("a1") + r"?\s*"
    r"(?:-|–|—|to|and|until|till)\s*"
    r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*" + MERIDIEM.format("a2") + ZONE + r"(?![\d:/])",
    re.IGNORECASE,
)
CLOCK_RE = re.compile(
    r"(?<![\d:/.$])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*" + MERIDIEM.format("ap") + r"?" + ZONE + r"(?![\d:/])",
    re.IGNORECASE,
)
NAMED_RE = re.compile(rf"\b(?P<word>noon|midday|midnight){ZONE}", re.IGNORECASE)
WEEKDAY_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PARTS = {"morning": (6, 12), "afternoon": (12, 17), "evening": (17, 21), "night": (18, 24.5)}
WEEKDAY_PART_RE = re.compile(rf"\b(?:on\s+)?({'|'.join(WEEKDAYS)})\s+({'|'.join(PARTS)})\b")
# Upper case only: "pt" or "et" are ordinary words, but a tweet that says "PT" means Pacific Time.
ZONE_WORD_RE = re.compile(r"\b(" + "|".join(sorted((zone.upper() for zone in ZONES), key=len, reverse=True)) + r")\b")
END_OF_DAY_RE = re.compile(r"\b(?:end\s+of\s+(?:the\s+)?day|eod|close\s+of\s+business)\b", re.IGNORECASE)
IMMINENT_RE = re.compile(r"\b(?:any\s+(?:minute|moment)\s+now|momentarily)\b", re.IGNORECASE)
LIVE_RE = re.compile(
    r"\b(?:just|already|have|has|had|we've|i've)\b[^.!?]{0,25}\breset(?:ted|ting)?\b"
    r"|\breset(?:ting)?\b[^.!?]{0,40}\b(?:now|right\s+now|as\s+we\s+speak)\b"
    r"|\breset\s+(?:is\s+)?(?:live|done|complete)\b|\b(?:been|are|is)\s+reset\b",
    re.IGNORECASE,
)
FUTURE_RE = re.compile(
    r"\b(?:will|'ll|won'?t|going\s+to|gonna|plan(?:ning)?\s+to|might|may|could|should|if|when|once|until|before)\b",
    re.IGNORECASE,
)

END_OF_DAY = 23 + 59 / 60
# label, pattern, start hour, end hour, day (1 = tomorrow, 0 = today or the next one), minutes to wait first
DAYPARTS = (
    ("tomorrow morning", r"tomorrow\s+morning", 6, 12, 1, 0),
    ("tomorrow afternoon", r"tomorrow\s+afternoon", 12, 17, 1, 0),
    ("tomorrow evening", r"tomorrow\s+evening", 17, 21, 1, 0),
    ("tomorrow night", r"tomorrow\s+night", 18, 24.5, 1, 0),
    ("later today", r"later\s+today", 9, END_OF_DAY, 0, 30),
    ("this evening", r"(?:this\s+)?evening", 17, 21, 0, 0),
    ("tonight", r"tonight|this\s+night", 18, 24.5, 0, 0),
    ("this afternoon", r"(?:this\s+)?afternoon", 12, 17, 0, 0),
    ("this morning", r"(?:this\s+)?morning", 6, 12, 0, 0),
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
    if zone := ZONE_WORD_RE.search(normalized):  # "Friday evening PT": the tweet names its own zone
        source_tz = _zoneinfo(ZONES[zone[1].lower()])
        current = current.astimezone(source_tz)
    for find in (_relative, _clock, _phrase, _already_live):
        window = find(normalized, current, source_tz, user_tz)
        if window:
            return window
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


def _relative(text: str, current: datetime, source_tz: ZoneInfo, user_tz: ZoneInfo) -> ResetWindow | None:
    """"within 2 hours", "in the next hour", "in about an hour", "in a couple of hours", "in 2-3 hours"."""
    within = WITHIN_RE.search(text)
    match = within or IN_RE.search(text)
    if not match:
        return None
    unit = _unit_minutes(match["unit"])
    low = _quantity(match["lo"]) if match["lo"] else 1.0
    high = _quantity(match["hi"]) if match["hi"] else low
    low_delta, high_delta = timedelta(minutes=low * unit), timedelta(minutes=high * unit)
    evidence = (match[0],)
    if within:
        return _window(match[0], current, current + high_delta, source_tz, user_tz, "high", evidence)
    if high != low:  # a stated range: "in 2-3 hours"
        start, end = current + low_delta, current + high_delta
    else:
        margin = min(timedelta(minutes=10 if unit == 1 else 30 if unit == 60 else 360), low_delta / 2)
        start, end = current + low_delta - margin, current + low_delta + margin
    return _window("around " + match[0], start, end, source_tz, user_tz, "medium", evidence)


def _clock(text: str, current: datetime, source_tz: ZoneInfo, user_tz: ZoneInfo) -> ResetWindow | None:
    """"at 3pm PT", "15:00 UTC", "by noon", "tomorrow at 9am", "between 3 and 5pm", "Friday 10am"."""
    span = _clock_span(text)
    if span is None:
        return None
    first, last, zone, matched = span
    if re.search(rf"\b(?:as\s+of|since)\s+{re.escape(matched)}", text, re.IGNORECASE):  # a time already behind us
        return _window("already reset", current, current + timedelta(hours=1), source_tz, user_tz, "medium", (matched,))
    tz = _zoneinfo(ZONES[zone.lower()]) if zone else source_tz
    local_now = current.astimezone(tz)
    day = _clock_day(text, local_now, first)
    start = datetime.combine(day, first, tzinfo=tz)
    if last is None:  # one time: a margin on both sides, tighter when the zone is stated
        margin = timedelta(minutes=20 if zone else 45)
        low, high, label = start - margin, start + margin, "around " + matched
    else:
        low, high, label = start, datetime.combine(day, last, tzinfo=tz), matched
        if high <= low:
            high += timedelta(days=1)
    return _window(label, low, high, source_tz, user_tz, "high" if zone else "medium", (matched,))


def _clock_span(text: str):
    """(first time, last time or None, zone word or None, matched text) for the first clock time in the text."""
    if match := RANGE_RE.search(text):
        first, last = int(match["h1"]), int(match["h2"])
        if 1 <= first <= 12 and 1 <= last <= 12:
            return (
                time(_hour24(first, match["a1"] or match["a2"]), int(match["m1"] or 0)),
                time(_hour24(last, match["a2"]), int(match["m2"] or 0)),
                match["tz"],
                match[0].strip(),
            )
    if match := NAMED_RE.search(text):
        return time(0 if match["word"].lower() == "midnight" else 12), None, match["tz"], match[0].strip()
    for match in CLOCK_RE.finditer(text):
        hour, minute, meridiem = int(match["h"]), int(match["m"] or 0), match["ap"]
        # A bare number is no time; it needs am/pm, or a 24-hour form with minutes ("15:00").
        if not (meridiem or (match["m"] and (hour > 12 or match["tz"]))):
            continue
        if hour > 23 or minute > 59 or (meridiem and not 1 <= hour <= 12):
            continue
        return time(_hour24(hour, meridiem), minute), None, match["tz"], match[0].strip()
    return None


def _hour24(hour: int, meridiem: str | None) -> int:
    if not meridiem:
        return hour
    return hour % 12 + (12 if meridiem[0].lower() == "p" else 0)


def _clock_day(text: str, local_now: datetime, at: time):
    """The date a clock time means: named by "tomorrow" or a weekday, else the next time that clock shows."""
    today = local_now.date()
    if re.search(r"\btomorrow\b", text, re.IGNORECASE):
        return today + timedelta(days=1)
    candidate = datetime.combine(today, at, tzinfo=local_now.tzinfo)
    passed = candidate < local_now - timedelta(minutes=15)
    if weekday := WEEKDAY_RE.search(text):
        ahead = (WEEKDAYS.index(weekday[1].lower()) - today.weekday()) % 7
        return today + timedelta(days=ahead or (7 if passed else 0))
    return today + timedelta(days=1 if passed else 0)


def _phrase(text: str, current: datetime, source_tz: ZoneInfo, user_tz: ZoneInfo) -> ResetWindow | None:
    lower = text.lower()
    today = current.date()
    tomorrow = today + timedelta(days=1)

    if found := WEEKDAY_PART_RE.search(lower):  # "Friday evening"
        start_hour, end_hour = PARTS[found[2]]
        ahead = (WEEKDAYS.index(found[1]) - today.weekday()) % 7
        if ahead == 0 and current > _hours(today, end_hour, source_tz):
            ahead = 7
        day = today + timedelta(days=ahead)
        start, end = _hours(day, start_hour, source_tz), _hours(day, end_hour, source_tz)
        return _window(found[0].removeprefix("on "), start, end, source_tz, user_tz, "medium", (found[0],))

    for label, pattern, start_hour, end_hour, offset, wait in DAYPARTS:
        found = re.search(rf"\b(?:{pattern})\b", lower)
        if not found:
            continue
        day = tomorrow
        if not offset:
            day = today if current <= _hours(today, end_hour, source_tz) else tomorrow
        start = _hours(day, start_hour, source_tz)
        if day == today:
            start = max(current + timedelta(minutes=wait), start)
        return _window(label, start, _hours(day, end_hour, source_tz), source_tz, user_tz, "medium", (found[0],))

    if weekday := WEEKDAY_RE.search(lower):  # "on Friday", "next Monday": a day, said without a time
        day = today + timedelta(days=(WEEKDAYS.index(weekday[1]) - today.weekday()) % 7 or 7)
        return _days(weekday[0], day, day, source_tz, user_tz, (weekday[0],))
    if found := re.search(r"\bthis\s+weekend\b", lower):
        to_saturday = max(0, 5 - today.weekday())
        first, last = today + timedelta(days=to_saturday), today + timedelta(days=6 - today.weekday())
        return _days("this weekend", first, last, source_tz, user_tz, (found[0],))
    if found := re.search(r"\bnext\s+week\b", lower):
        monday = today + timedelta(days=7 - today.weekday())
        return _days("next week", monday, monday + timedelta(days=6), source_tz, user_tz, (found[0],))
    if found := END_OF_DAY_RE.search(lower):
        end = _hours(today, END_OF_DAY, source_tz)
        return _window("end of day", current, end, source_tz, user_tz, "medium", (found[0],))
    if found := IMMINENT_RE.search(lower):
        end = current + timedelta(minutes=30)
        return _window("any minute", current, end, source_tz, user_tz, "medium", (found[0],))
    if re.search(r"\btomorrow\b", lower):
        return _days("tomorrow", tomorrow, tomorrow, source_tz, user_tz, ("tomorrow",))
    if re.search(r"\btoday\b", lower):
        end = _hours(today, END_OF_DAY, source_tz)
        return _window("today", current, end, source_tz, user_tz, "low", ("today",))
    if found := re.search(r"\bsoon\b|\bshortly\b|\bin\s+a\s+bit\b", lower):
        end = current + timedelta(hours=4)
        return _window("soon", current, end, source_tz, user_tz, "low", (found[0],))
    return None


def _already_live(text: str, current: datetime, source_tz: ZoneInfo, user_tz: ZoneInfo) -> ResetWindow | None:
    """No time at all, but said as done ("we just reset the limits"): treat the reset as live now."""
    found = LIVE_RE.search(text)
    if not found or FUTURE_RE.search(text):
        return None
    return _window("already reset", current, current + timedelta(hours=1), source_tz, user_tz, "medium", (found[0],))


def _quantity(text: str) -> float:
    words = [word for word in text.lower().split() if word != "of"]
    if words[0] == "half":
        return 0.5
    last = words[-1]
    return float(NUMBER_WORDS.get(last) or FUZZY_WORDS.get(last) or last)


def _unit_minutes(unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("d"):
        return 1440
    return 60 if unit.startswith("h") else 1


def _days(label: str, first, last, source_tz: ZoneInfo, user_tz: ZoneInfo, evidence: tuple[str, ...]) -> ResetWindow:
    """A whole-day window, from the first day's start to the last day's end: low confidence by nature."""
    start, end = _hours(first, 0, source_tz), _hours(last, END_OF_DAY, source_tz)
    return _window(label, start, end, source_tz, user_tz, "low", evidence)


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


def _hours(day, hours: float, tz: ZoneInfo) -> datetime:
    """Midnight of the day plus a (possibly fractional or past 24) number of hours, to the minute."""
    return datetime.combine(day, time(0), tzinfo=tz) + timedelta(minutes=round(hours * 60))


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
