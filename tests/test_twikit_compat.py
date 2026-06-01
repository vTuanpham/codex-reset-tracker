import unittest

from codex_reset_tracker.twikit_compat import (
    apply_twikit_compatibility_patches,
    _resolve_on_demand_file_url,
    _with_user_defaults,
)
from codex_reset_tracker.twikit_source import _forbidden_login_message


class TwikitCompatTests(unittest.TestCase):
    def test_resolves_modern_on_demand_manifest_shape(self):
        html = 'window.__SCRIPTS__={x:1,123:"ondemand.s",y:2,123:"abcdef123456"}'

        url = _resolve_on_demand_file_url(html)

        self.assertEqual(
            url,
            "https://abs.twimg.com/responsive-web/client-web/ondemand.s.abcdef123456a.js",
        )

    def test_patch_is_idempotent_when_twikit_is_available(self):
        first = apply_twikit_compatibility_patches()
        second = apply_twikit_compatibility_patches()

        self.assertTrue(all(result.ok for result in first))
        self.assertTrue(all(result.ok for result in second))

    def test_patch_registry_reports_named_results(self):
        results = apply_twikit_compatibility_patches()
        by_name = {result.name: result for result in results}

        self.assertIn("client-transaction-manifest", by_name)
        self.assertIn("user-optional-fields", by_name)
        self.assertTrue(all(result.ok for result in results))

    def test_forbidden_login_message_mentions_cloudflare_cookie_fallback(self):
        message = _forbidden_login_message(Exception("403 Cloudflare blocked"))

        self.assertIn("Cloudflare", message)
        self.assertIn("data/x_cookies.json", message)
        self.assertIn("skips fresh login", message)

    def test_user_defaults_fill_optional_profile_fields(self):
        patched = _with_user_defaults(
            {
                "rest_id": "1",
                "legacy": {
                    "entities": {"description": {}},
                    "screen_name": "thsottiaux",
                    "name": "Thomas",
                },
            }
        )

        legacy = patched["legacy"]
        self.assertEqual(legacy["entities"]["description"]["urls"], [])
        self.assertEqual(legacy["pinned_tweet_ids_str"], [])
        self.assertEqual(legacy["withheld_in_countries"], [])
        self.assertFalse(patched["is_blue_verified"])


if __name__ == "__main__":
    unittest.main()
