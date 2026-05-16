from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import AppConfig
from .matcher import RegexMatcher
from .models import TweetRecord
from .notifiers import NotificationManager
from .state import StateStore
from .time_window import attach_reset_window, parse_created_at
from .twikit_source import TwikitTweetSource

LOGGER = logging.getLogger(__name__)


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

            if self.state.has_seen(tweet):
                duplicates += 1
                continue

            if bootstrap_seen_only:
                self.state.mark_seen(tweet)
                continue

            if self._is_preexisting_tweet(tweet):
                self.state.mark_seen(tweet)
                continue

            match = self.matcher.match(tweet)
            if match is None:
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
                duplicates += 1
                self.state.mark_seen(tweet)
                continue

            if alerted >= self.config.polling.max_alerts_per_scan:
                LOGGER.warning("max_alerts_per_scan reached; suppressing remaining matches")
                break

            delivery = await self.notifier.send_match(match)
            self.state.mark_alerted(match, delivery)
            self.state.mark_seen(tweet)
            alerted += 1

        return ScanSummary(scanned=scanned, matched=matched, alerted=alerted, duplicates=duplicates)

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
        cutoff = self.started_at - timedelta(
            seconds=self.config.polling.new_tweet_grace_seconds
        )
        return created_at < cutoff


def _has_tweet_id(tweet: TweetRecord) -> bool:
    if not tweet.id:
        LOGGER.warning("skipping tweet with missing id from %s", tweet.source)
        return False
    return True
