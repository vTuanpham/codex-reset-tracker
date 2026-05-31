from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from .config import AppConfig
from .matcher import RegexMatcher
from .models import TweetRecord
from .notifiers import NotificationManager
from .state import StateStore
from .time_window import attach_reset_window, parse_created_at
from .twikit_source import TwikitTweetSource

LOGGER = logging.getLogger(__name__)
LAST_SCAN_AT_KEY = "last_scan_at"
LAST_EFFECTIVE_SCAN_AT_KEY = "last_effective_scan_at"
MATCHING_FINGERPRINT_KEY = "matching_fingerprint"
MAX_STARTUP_CATCHUP = timedelta(days=1)


@dataclass(frozen=True)
class ScanSummary:
    scanned: int
    matched: int
    alerted: int
    duplicates: int


class QuotaResetTracker:
    def __init__(
        self,
        config: AppConfig,
        *,
        source: TwikitTweetSource | None = None,
        state: StateStore | None = None,
        notifier: NotificationManager | None = None,
        allow_historical: bool = False,
        dump_stream_path: Path | None = None,
    ):
        self.config = config
        self.matcher = RegexMatcher(config.matching)
        self.source = source or TwikitTweetSource(
            config.twitter,
            request_delay_seconds=config.polling.request_delay_seconds,
        )
        self.state = state or StateStore(config.state_path)
        self.notifier = notifier or NotificationManager(config.notifications)
        self.started_at = datetime.now(timezone.utc)
        self.matching_fingerprint = _matching_fingerprint(config)
        self.catchup_cutoff = self._startup_catchup_cutoff(self.started_at)
        self.reprocess_seen_cutoff = self._matcher_reprocess_cutoff(self.started_at)
        self.allow_historical = allow_historical
        self.dump_stream_path = dump_stream_path
        if self.dump_stream_path is not None:
            self.dump_stream_path.parent.mkdir(parents=True, exist_ok=True)
            self.dump_stream_path.write_text("", encoding="utf-8")

    async def connect(self) -> None:
        await self.source.connect()

    async def run_forever(self) -> None:
        await self.connect()
        while True:
            summary = await self.scan_once()
            LOGGER.info(
                "scan complete: scanned=%s matched=%s alerted=%s duplicates=%s",
                summary.scanned,
                summary.matched,
                summary.alerted,
                summary.duplicates,
            )
            await asyncio.sleep(self._sleep_seconds())

    async def scan_once(self) -> ScanSummary:
        seen_ids: set[str] = set()
        trusted_authors = _trusted_authors(self.config.polling.accounts)
        scanned = 0
        matched = 0
        alerted = 0
        duplicates = 0
        bootstrap_seen_only = (
            not self.config.polling.alert_on_first_scan
            and self.state.seen_count() == 0
        )

        async for tweet in self._iter_tweets():
            if tweet.id in seen_ids:
                continue
            seen_ids.add(tweet.id)
            scanned += 1

            if not _is_trusted_author(tweet, trusted_authors):
                LOGGER.info(
                    "skipping tweet from untrusted author @%s source=%s url=%s",
                    tweet.author_username,
                    tweet.source,
                    tweet.url,
                )
                self._dump_tweet(tweet, decision="untrusted_author")
                self.state.mark_seen(tweet)
                continue

            seen_before = self.state.has_seen(tweet)
            reprocess_seen = seen_before and self._should_reprocess_seen(tweet)
            if seen_before and not reprocess_seen:
                self._dump_tweet(tweet, decision="seen_duplicate")
                duplicates += 1
                continue

            if (
                not reprocess_seen
                and bootstrap_seen_only
                and not self.allow_historical
                and self.catchup_cutoff is None
            ):
                self._dump_tweet(tweet, decision="baseline_seen")
                self.state.mark_seen(tweet)
                continue

            if (
                not reprocess_seen
                and not self.allow_historical
                and self._is_preexisting_tweet(tweet)
            ):
                self._dump_tweet(tweet, decision="preexisting_suppressed")
                self.state.mark_seen(tweet)
                continue

            match = self.matcher.match(tweet)
            if match is None:
                decision = "reprocessed_no_match" if reprocess_seen else "no_match"
                self._dump_tweet(tweet, decision=decision)
                self.state.mark_seen(tweet)
                continue
            matched += 1
            match = attach_reset_window(
                match,
                source_timezone_name=self.config.time.source_timezone_for(
                    tweet.author_username
                ),
                user_timezone_name=self.config.time.user_timezone,
            )

            if self.state.was_alerted(match):
                decision = (
                    "reprocessed_alert_duplicate"
                    if reprocess_seen
                    else "alert_duplicate"
                )
                self._dump_tweet(tweet, decision=decision, match=match)
                duplicates += 1
                self.state.mark_seen(tweet)
                continue

            if alerted >= self.config.polling.max_alerts_per_scan:
                LOGGER.warning("max_alerts_per_scan reached; suppressing remaining matches")
                break

            delivery = await self.notifier.send_match(match)
            self.state.mark_alerted(match, delivery)
            self.state.mark_seen(tweet)
            decision = "reprocessed_alerted" if reprocess_seen else "alerted"
            self._dump_tweet(tweet, decision=decision, match=match, delivery=delivery)
            alerted += 1

        summary = ScanSummary(
            scanned=scanned,
            matched=matched,
            alerted=alerted,
            duplicates=duplicates,
        )
        self._write_status(summary)
        return summary

    async def _iter_tweets(self):
        polling = self.config.polling
        async for tweet in self.source.iter_account_tweets(
            polling.accounts,
            count=polling.tweet_count_per_account,
        ):
            if _has_tweet_id(tweet):
                yield tweet
        async for tweet in self.source.iter_search_tweets(
            polling.search_queries,
            count=polling.search_count_per_query,
        ):
            if _has_tweet_id(tweet):
                yield tweet

    def _sleep_seconds(self) -> float:
        polling = self.config.polling
        if polling.jitter_seconds <= 0:
            return float(polling.interval_seconds)
        return float(polling.interval_seconds + random.uniform(0, polling.jitter_seconds))

    def _is_preexisting_tweet(self, tweet: TweetRecord) -> bool:
        created_at = parse_created_at(tweet.created_at)
        if created_at is None:
            return False
        cutoff = self.catchup_cutoff
        if cutoff is None:
            cutoff = self.started_at - timedelta(
                seconds=self.config.polling.new_tweet_grace_seconds
            )
        return created_at < cutoff

    def _write_status(self, summary: ScanSummary) -> None:
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        scan_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "last_scan_at": scan_at,
            "summary": {
                "scanned": summary.scanned,
                "matched": summary.matched,
                "alerted": summary.alerted,
                "duplicates": summary.duplicates,
            },
        }
        (self.config.runtime_dir / "status.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.state.set_metadata(LAST_SCAN_AT_KEY, scan_at)
        if summary.scanned > 0:
            self.state.set_metadata(LAST_EFFECTIVE_SCAN_AT_KEY, scan_at)
            self.state.set_metadata(MATCHING_FINGERPRINT_KEY, self.matching_fingerprint)

    def _startup_catchup_cutoff(self, now: datetime) -> datetime | None:
        last_scan = parse_created_at(
            self.state.get_metadata(LAST_EFFECTIVE_SCAN_AT_KEY)
            or self.state.get_metadata(LAST_SCAN_AT_KEY)
        )
        if last_scan is None:
            return None
        cutoff = max(last_scan, now - MAX_STARTUP_CATCHUP)
        LOGGER.info(
            "startup catch-up enabled from %s to %s",
            cutoff.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
        )
        return cutoff

    def _matcher_reprocess_cutoff(self, now: datetime) -> datetime | None:
        previous = self.state.get_metadata(MATCHING_FINGERPRINT_KEY)
        if previous == self.matching_fingerprint or self.state.seen_count() == 0:
            return None
        cutoff = now - MAX_STARTUP_CATCHUP
        LOGGER.info(
            "matching profile changed or was not recorded; rechecking seen tweets since %s",
            cutoff.isoformat(timespec="seconds"),
        )
        return cutoff

    def _should_reprocess_seen(self, tweet: TweetRecord) -> bool:
        if self.reprocess_seen_cutoff is None:
            return False
        created_at = parse_created_at(tweet.created_at)
        if created_at is None:
            return False
        return created_at >= self.reprocess_seen_cutoff

    def _dump_tweet(
        self,
        tweet: TweetRecord,
        *,
        decision: str,
        match=None,
        delivery: dict | None = None,
    ) -> None:
        if self.dump_stream_path is None:
            return
        payload = {
            "decision": decision,
            "tweet": {
                "id": tweet.id,
                "author_username": tweet.author_username,
                "author_name": tweet.author_name,
                "text": tweet.text,
                "created_at": tweet.created_at,
                "url": tweet.url,
                "source": tweet.source,
                "raw": tweet.raw,
            },
        }
        if match is not None:
            payload["match"] = {
                "matched_patterns": list(match.matched_patterns),
                "excerpt": match.excerpt,
                "reset_window": None
                if match.reset_window is None
                else {
                    "label": match.reset_window.label,
                    "source_start_at": match.reset_window.source_start_at,
                    "source_end_at": match.reset_window.source_end_at,
                    "source_timezone": match.reset_window.source_timezone,
                    "user_start_at": match.reset_window.user_start_at,
                    "user_end_at": match.reset_window.user_end_at,
                    "user_timezone": match.reset_window.user_timezone,
                    "confidence": match.reset_window.confidence,
                    "evidence": list(match.reset_window.evidence),
                },
            }
        if delivery is not None:
            payload["delivery"] = delivery
        with self.dump_stream_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True) + "\n")


def _has_tweet_id(tweet: TweetRecord) -> bool:
    if not tweet.id:
        LOGGER.warning("skipping tweet with missing id from %s", tweet.source)
        return False
    return True


def _trusted_authors(accounts: list[str] | tuple[str, ...]) -> set[str]:
    return {_normalize_handle(account) for account in accounts if _normalize_handle(account)}


def _is_trusted_author(tweet: TweetRecord, trusted_authors: set[str]) -> bool:
    return _normalize_handle(tweet.author_username) in trusted_authors


def _normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()


def _matching_fingerprint(config: AppConfig) -> str:
    payload = {
        "version": 1,
        "case_sensitive": config.matching.case_sensitive,
        "require_all_include_patterns": config.matching.require_all_include_patterns,
        "include_patterns": list(config.matching.include_patterns),
        "exclude_patterns": list(config.matching.exclude_patterns),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
