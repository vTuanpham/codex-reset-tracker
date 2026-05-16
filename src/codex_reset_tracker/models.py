from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Sequence


@dataclass(frozen=True)
class TweetRecord:
    id: str
    author_username: str
    author_name: str
    text: str
    created_at: str | None
    url: str
    source: str

    @property
    def text_hash(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResetWindow:
    label: str
    source_start_at: str
    source_end_at: str
    source_timezone: str
    user_start_at: str
    user_end_at: str
    user_timezone: str
    confidence: str
    evidence: Sequence[str]


@dataclass(frozen=True)
class TweetMatch:
    tweet: TweetRecord
    matched_patterns: Sequence[str]
    excerpt: str
    reset_window: ResetWindow | None = None

    @property
    def alert_key(self) -> str:
        raw = f"{self.tweet.id}:{self.tweet.text_hash}"
        return sha256(raw.encode("utf-8")).hexdigest()

    @property
    def pattern_summary(self) -> str:
        return ", ".join(self.matched_patterns)
