import unittest

from codex_reset_tracker.twikit_compat import (
    _resolve_on_demand_file_url,
    patch_twikit_client_transaction,
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

    def test_resolves_legacy_on_demand_manifest_shape(self):
        html = '"ondemand.s": "abcdef"'

        url = _resolve_on_demand_file_url(html)

        self.assertEqual(
            url,
            "https://abs.twimg.com/responsive-web/client-web/ondemand.s.abcdefa.js",
        )

    def test_patch_is_idempotent_when_twikit_is_available(self):
        first = patch_twikit_client_transaction()
        second = patch_twikit_client_transaction()

        self.assertTrue(first)
        self.assertTrue(second)

    def test_forbidden_login_message_mentions_cloudflare_cookie_fallback(self):
        message = _forbidden_login_message(Exception("403 Cloudflare blocked"))

        self.assertIn("Cloudflare", message)
        self.assertIn("data/x_cookies.json", message)


if __name__ == "__main__":
    unittest.main()
