import unittest

from codex_reset_tracker.config import MatchingConfig
from codex_reset_tracker.matcher import RegexMatcher
from codex_reset_tracker.models import TweetRecord


def tweet(text: str) -> TweetRecord:
    return TweetRecord(
        id="1",
        author_username="sama",
        author_name="Sam Altman",
        text=text,
        created_at=None,
        url="https://x.com/sama/status/1",
        source="test",
    )


class RegexMatcherTests(unittest.TestCase):
    def test_matches_codex_quota_reset_signal(self):
        matcher = RegexMatcher(MatchingConfig())

        result = matcher.match(tweet("Codex usage limits have been refreshed. Quota reset is rolling out."))

        self.assertIsNotNone(result)
        self.assertIn("Codex usage limits", result.excerpt)

    def test_rejects_unrelated_reset(self):
        matcher = RegexMatcher(MatchingConfig())

        result = matcher.match(tweet("Please reset your password if you lost access to your account."))

        self.assertIsNone(result)

    def test_requires_all_default_contexts(self):
        matcher = RegexMatcher(MatchingConfig())

        result = matcher.match(tweet("Codex had a great week."))

        self.assertIsNone(result)

    def test_any_mode_can_match_single_pattern(self):
        matcher = RegexMatcher(
            MatchingConfig(
                require_all_include_patterns=False,
                include_patterns=(r"\breset\s+quota\b",),
                exclude_patterns=(),
            )
        )

        result = matcher.match(tweet("Looks like reset quota messaging is live."))

        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
