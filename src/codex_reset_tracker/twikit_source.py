from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .config import ConfigError, TwitterConfig
from .models import TweetRecord
from .twikit_compat import patch_twikit_client_transaction

LOGGER = logging.getLogger(__name__)


class TwikitTweetSource:
    def __init__(self, config: TwitterConfig, request_delay_seconds: float = 2.0):
        self.config = config
        self.request_delay_seconds = request_delay_seconds
        self._client = None
        self._user_ids: dict[str, str] = {}

    async def connect(self) -> None:
        patch_twikit_client_transaction()
        from twikit import Client
        from twikit.errors import Forbidden

        self.config.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {}
        if self.config.proxy:
            kwargs["proxy"] = self.config.proxy
        if self.config.user_agent:
            kwargs["user_agent"] = self.config.user_agent

        self._client = Client(self.config.language, **kwargs)
        if self.config.cookies_file.exists():
            self._client.load_cookies(str(self.config.cookies_file))
            LOGGER.info("loaded X/Twitter cookies from %s", self.config.cookies_file)
            return

        username = self.config.username
        password = self.config.password
        if username and password:
            try:
                await self._client.login(
                    auth_info_1=username,
                    auth_info_2=self.config.email,
                    password=password,
                    totp_secret=self.config.totp_secret,
                    cookies_file=str(self.config.cookies_file),
                )
            except Forbidden as exc:
                raise ConfigError(_forbidden_login_message(exc)) from exc
            LOGGER.info("authenticated X/Twitter client as configured user")
        elif not self.config.cookies_file.exists():
            raise ConfigError(
                "Missing X/Twitter credentials and no cookies file exists. "
                f"Set {self.config.username_env}/{self.config.password_env} or provide "
                f"{self.config.cookies_file}."
            )

    async def iter_account_tweets(
        self,
        accounts: list[str] | tuple[str, ...],
        count: int,
    ) -> AsyncIterator[TweetRecord]:
        client = self._require_client()
        for account in accounts:
            screen_name = normalize_handle(account)
            try:
                user_id = await self._resolve_user_id(screen_name)
                tweets = await client.get_user_tweets(user_id, "Tweets", count=count)
            except Exception:
                LOGGER.exception("failed to fetch tweets for @%s", screen_name)
                await asyncio.sleep(self.request_delay_seconds)
                continue

            for tweet in tweets:
                yield tweet_to_record(tweet, source=f"account:@{screen_name}")
            await asyncio.sleep(self.request_delay_seconds)

    async def iter_search_tweets(
        self,
        queries: list[str] | tuple[str, ...],
        count: int,
    ) -> AsyncIterator[TweetRecord]:
        client = self._require_client()
        bounded_count = min(max(1, count), 20)
        for query in queries:
            try:
                tweets = await client.search_tweet(query, "Latest", count=bounded_count)
            except Exception:
                LOGGER.exception("failed to search tweets for query: %s", query)
                await asyncio.sleep(self.request_delay_seconds)
                continue

            for tweet in tweets:
                yield tweet_to_record(tweet, source=f"search:{query}")
            await asyncio.sleep(self.request_delay_seconds)

    async def _resolve_user_id(self, screen_name: str) -> str:
        if screen_name in self._user_ids:
            return self._user_ids[screen_name]
        client = self._require_client()
        user = await client.get_user_by_screen_name(screen_name)
        user_id = str(user.id)
        self._user_ids[screen_name] = user_id
        return user_id

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("Twikit client is not connected")
        return self._client


def normalize_handle(value: str) -> str:
    return value.strip().lstrip("@")


def tweet_to_record(tweet, source: str) -> TweetRecord:
    user = getattr(tweet, "user", None)
    screen_name = normalize_handle(
        str(
            getattr(user, "screen_name", "")
            or getattr(user, "username", "")
            or "unknown"
        )
    )
    author_name = str(getattr(user, "name", "") or screen_name)
    tweet_id = str(getattr(tweet, "id", ""))
    text = str(
        getattr(tweet, "full_text", "")
        or getattr(tweet, "text", "")
        or ""
    )
    created_at = (
        getattr(tweet, "created_at_datetime", None)
        or getattr(tweet, "created_at", None)
    )
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()
    url = str(getattr(tweet, "url", "") or _tweet_url(screen_name, tweet_id))
    return TweetRecord(
        id=tweet_id,
        author_username=screen_name,
        author_name=author_name,
        text=text,
        created_at=str(created_at) if created_at is not None else None,
        url=url,
        source=source,
        raw=_safe_tweet_snapshot(tweet),
    )


def _tweet_url(screen_name: str, tweet_id: str) -> str:
    if not tweet_id:
        return "https://x.com"
    if not screen_name or screen_name == "unknown":
        return f"https://x.com/i/web/status/{tweet_id}"
    return f"https://x.com/{screen_name}/status/{tweet_id}"


def _forbidden_login_message(exc: Exception) -> str:
    message = str(exc)
    if "Cloudflare" in message or "been blocked" in message or "403" in message:
        return (
            "X/Twitter blocked the login request with Cloudflare. The Twikit parser "
            "patch is working, but direct username/password login was blocked. "
            "Recommended fix: log into X in your browser, export x.com cookies as "
            "JSON, save them at data/x_cookies.json, then rerun the command. "
            "When that file exists, the tracker skips fresh login."
        )
    return f"X/Twitter login was forbidden: {exc}"


def _safe_tweet_snapshot(tweet) -> dict[str, object]:
    fields = (
        "id",
        "text",
        "full_text",
        "created_at",
        "created_at_datetime",
        "lang",
        "quote_count",
        "reply_count",
        "retweet_count",
        "favorite_count",
        "view_count",
        "url",
    )
    payload: dict[str, object] = {}
    for field in fields:
        value = getattr(tweet, field, None)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            payload[field] = value
        elif hasattr(value, "isoformat"):
            payload[field] = value.isoformat()
        else:
            payload[field] = str(value)
    user = getattr(tweet, "user", None)
    if user is not None:
        payload["user"] = {
            "id": str(getattr(user, "id", "")),
            "screen_name": str(
                getattr(user, "screen_name", "")
                or getattr(user, "username", "")
            ),
            "name": str(getattr(user, "name", "")),
        }
    return payload
