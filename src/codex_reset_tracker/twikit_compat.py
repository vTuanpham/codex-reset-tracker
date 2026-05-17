from __future__ import annotations

import logging
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

ON_DEMAND_FILE_INDEX_REGEX = re.compile(
    r""",(\d+):["']ondemand\.s["']""",
    flags=re.VERBOSE | re.MULTILINE,
)
ON_DEMAND_HASH_PATTERN = r""",{}:["']([0-9a-f]+)["']"""
LEGACY_ON_DEMAND_FILE_REGEX = re.compile(
    r"""['"]{1}ondemand\.s['"]{1}:\s*['"]{1}([\w]*)['"]{1}""",
    flags=re.VERBOSE | re.MULTILINE,
)


def patch_twikit_client_transaction() -> bool:
    """Patch Twikit's ondemand.s lookup after X changed its asset manifest."""
    try:
        from twikit.x_client_transaction import transaction
    except Exception:
        LOGGER.debug("twikit transaction module is unavailable", exc_info=True)
        return False

    client_transaction = getattr(transaction, "ClientTransaction", None)
    if client_transaction is None:
        return False
    if getattr(client_transaction.get_indices, "_codex_reset_tracker_patch", False):
        return True

    async def get_indices(self, home_page_response, session, headers):
        key_byte_indices = []
        response = self.validate_response(home_page_response) or self.home_page_response
        response_text = str(response)
        on_demand_file_url = _resolve_on_demand_file_url(response_text)

        if on_demand_file_url:
            on_demand_file_response = await session.request(
                method="GET",
                url=on_demand_file_url,
                headers=headers,
            )
            key_byte_indices_match = transaction.INDICES_REGEX.finditer(
                str(on_demand_file_response.text)
            )
            for item in key_byte_indices_match:
                key_byte_indices.append(item.group(2))

        if not key_byte_indices:
            raise Exception("Couldn't get KEY_BYTE indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]

    get_indices._codex_reset_tracker_patch = True
    client_transaction.get_indices = get_indices
    LOGGER.debug("patched Twikit ClientTransaction.get_indices")
    return True


def patch_twikit_user_defaults() -> bool:
    """Patch Twikit's User parser to tolerate optional profile fields missing."""
    try:
        from twikit import user as user_module
    except Exception:
        LOGGER.debug("twikit user module is unavailable", exc_info=True)
        return False

    user_class = getattr(user_module, "User", None)
    if user_class is None:
        return False
    original_init = getattr(user_class, "__init__", None)
    if getattr(original_init, "_codex_reset_tracker_patch", False):
        return True

    def patched_init(self, client, data):
        data = _with_user_defaults(data)
        return original_init(self, client, data)

    patched_init._codex_reset_tracker_patch = True
    user_class.__init__ = patched_init
    LOGGER.debug("patched Twikit User.__init__ defaults")
    return True


def patch_twikit() -> None:
    patch_twikit_client_transaction()
    patch_twikit_user_defaults()


def _resolve_on_demand_file_url(home_page_text: str) -> str | None:
    modern_match = ON_DEMAND_FILE_INDEX_REGEX.search(home_page_text)
    if modern_match:
        hash_regex = re.compile(
            ON_DEMAND_HASH_PATTERN.format(re.escape(modern_match.group(1))),
            flags=re.VERBOSE | re.MULTILINE,
        )
        hash_match = hash_regex.search(home_page_text)
        if hash_match:
            return _asset_url(hash_match.group(1))

    legacy_match = LEGACY_ON_DEMAND_FILE_REGEX.search(home_page_text)
    if legacy_match:
        return _asset_url(legacy_match.group(1))
    return None


def _asset_url(filename: Any) -> str:
    return f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{filename}a.js"


def _with_user_defaults(data: dict) -> dict:
    copied = dict(data)
    legacy = dict(copied.get("legacy") or {})
    entities = dict(legacy.get("entities") or {})
    description = dict(entities.get("description") or {})
    description.setdefault("urls", [])
    entities["description"] = description
    entities.setdefault("url", {"urls": []})
    legacy["entities"] = entities

    defaults = {
        "created_at": "",
        "name": "",
        "screen_name": "",
        "profile_image_url_https": "",
        "location": "",
        "description": "",
        "pinned_tweet_ids_str": [],
        "verified": False,
        "possibly_sensitive": False,
        "can_dm": False,
        "can_media_tag": False,
        "want_retweets": False,
        "default_profile": False,
        "default_profile_image": False,
        "has_custom_timelines": False,
        "followers_count": 0,
        "fast_followers_count": 0,
        "normal_followers_count": 0,
        "friends_count": 0,
        "favourites_count": 0,
        "listed_count": 0,
        "media_count": 0,
        "statuses_count": 0,
        "is_translator": False,
        "translator_type": "",
        "withheld_in_countries": [],
    }
    for key, value in defaults.items():
        legacy.setdefault(key, value)

    copied["legacy"] = legacy
    copied.setdefault("rest_id", "")
    copied.setdefault("is_blue_verified", False)
    return copied
