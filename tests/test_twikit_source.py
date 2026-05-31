import asyncio
import unittest

from codex_reset_tracker.config import TwitterConfig
from codex_reset_tracker.twikit_source import TwikitTweetSource
from twikit.errors import NotFound


class FakeUser:
    id = "user-123"


class FakeTweet:
    def __init__(self, tweet_id):
        self.id = tweet_id
        self.user = FakeUser()
        self.full_text = "reset"
        self.created_at = "2026-05-31T00:00:00+00:00"
        self.url = f"https://x.com/sama/status/{tweet_id}"


class FakeClient:
    def __init__(self):
        self.queries = []
        self.screen_names = []
        self.timeline_calls = []

    async def get_user_by_screen_name(self, screen_name):
        self.screen_names.append(screen_name)
        return FakeUser()

    async def get_user_tweets(self, user_id, tweet_type, count):
        self.timeline_calls.append((user_id, tweet_type, count))
        return []

    async def search_tweet(self, query, product, count):
        self.queries.append((query, product, count))
        return []


class RepliesNotFoundClient(FakeClient):
    async def get_user_tweets(self, user_id, tweet_type, count):
        self.timeline_calls.append((user_id, tweet_type, count))
        if tweet_type == "Replies":
            raise NotFound(headers={})
        return [FakeTweet("primary")]


class SearchNotFoundClient(FakeClient):
    async def search_tweet(self, query, product, count):
        self.queries.append((query, product, count))
        raise NotFound(headers={})


class TwikitSourceTests(unittest.TestCase):
    def test_account_tweets_use_user_timeline_not_search(self):
        source = TwikitTweetSource(TwitterConfig(), request_delay_seconds=0)
        client = FakeClient()
        source._client = client

        async def collect():
            return [
                tweet
                async for tweet in source.iter_account_tweets(("sama",), count=50)
            ]

        tweets = asyncio.run(collect())

        self.assertEqual(tweets, [])
        self.assertEqual(client.screen_names, ["sama"])
        self.assertEqual(
            client.timeline_calls,
            [("user-123", "Tweets", 40), ("user-123", "Replies", 40)],
        )
        self.assertEqual(client.queries, [])

    def test_account_tweets_continue_when_replies_endpoint_404s(self):
        source = TwikitTweetSource(TwitterConfig(), request_delay_seconds=0)
        client = RepliesNotFoundClient()
        source._client = client

        async def collect():
            return [
                tweet
                async for tweet in source.iter_account_tweets(("sama",), count=50)
            ]

        with self.assertLogs("codex_reset_tracker.twikit_source", level="WARNING") as logs:
            tweets = asyncio.run(collect())

        self.assertEqual([tweet.id for tweet in tweets], ["primary"])
        self.assertEqual(
            client.timeline_calls,
            [("user-123", "Tweets", 40), ("user-123", "Replies", 40)],
        )
        self.assertTrue(source._replies_unavailable)
        self.assertIn("replies timeline endpoint returned 404", logs.output[0])

    def test_search_404_disables_remaining_searches_without_failing_scan(self):
        source = TwikitTweetSource(TwitterConfig(), request_delay_seconds=0)
        client = SearchNotFoundClient()
        source._client = client

        async def collect():
            return [
                tweet
                async for tweet in source.iter_search_tweets(
                    ("from:OpenAI reset", "from:claudeai reset"),
                    count=20,
                )
            ]

        with self.assertLogs("codex_reset_tracker.twikit_source", level="WARNING") as logs:
            tweets = asyncio.run(collect())

        self.assertEqual(tweets, [])
        self.assertEqual(client.queries, [("from:OpenAI reset", "Latest", 20)])
        self.assertTrue(source._search_unavailable)
        self.assertIn("search endpoint returned 404", logs.output[0])


if __name__ == "__main__":
    unittest.main()
