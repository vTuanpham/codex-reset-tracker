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
