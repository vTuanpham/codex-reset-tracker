import asyncio
import unittest

from codex_reset_tracker.config import TwitterConfig
from codex_reset_tracker.twikit_source import TwikitTweetSource


class FakeClient:
    def __init__(self):
        self.queries = []

    async def search_tweet(self, query, product, count):
        self.queries.append((query, product, count))
        return []


class TwikitSourceTests(unittest.TestCase):
    def test_account_tweets_use_from_search_query(self):
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
        self.assertEqual(client.queries, [("from:sama", "Latest", 20)])


if __name__ == "__main__":
    unittest.main()
