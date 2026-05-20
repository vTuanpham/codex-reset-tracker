from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import TweetMatch, TweetRecord


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._db.close()

    def seen_count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) AS count FROM seen_tweets").fetchone()
        return int(row["count"])

    def get_metadata(self, key: str) -> str | None:
        row = self._db.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute(
                """
                INSERT INTO metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def has_seen(self, tweet: TweetRecord) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM seen_tweets WHERE tweet_id = ?",
            (tweet.id,),
        ).fetchone()
        return row is not None

    def mark_seen(self, tweet: TweetRecord) -> None:
        now = utc_now_iso()
        with self._db:
            self._db.execute(
                """
                INSERT INTO seen_tweets (
                    tweet_id, author_username, text_hash, first_seen_at, last_seen_at, url
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tweet_id) DO UPDATE SET
                    author_username = excluded.author_username,
                    text_hash = excluded.text_hash,
                    last_seen_at = excluded.last_seen_at,
                    url = excluded.url
                """,
                (
                    tweet.id,
                    tweet.author_username,
                    tweet.text_hash,
                    now,
                    now,
                    tweet.url,
                ),
            )

    def was_alerted(self, match: TweetMatch) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM alerts WHERE alert_key = ?",
            (match.alert_key,),
        ).fetchone()
        return row is not None

    def mark_alerted(self, match: TweetMatch, delivery: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._db:
            self._db.execute(
                """
                INSERT OR IGNORE INTO alerts (
                    alert_key, tweet_id, author_username, text_hash, matched_patterns,
                    excerpt, delivered_at, delivery_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match.alert_key,
                    match.tweet.id,
                    match.tweet.author_username,
                    match.tweet.text_hash,
                    json.dumps(list(match.matched_patterns), sort_keys=True),
                    match.excerpt,
                    now,
                    json.dumps(delivery, sort_keys=True),
                ),
            )

    def _init_schema(self) -> None:
        with self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_tweets (
                    tweet_id TEXT PRIMARY KEY,
                    author_username TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    url TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_key TEXT PRIMARY KEY,
                    tweet_id TEXT NOT NULL,
                    author_username TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    matched_patterns TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    delivery_json TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
