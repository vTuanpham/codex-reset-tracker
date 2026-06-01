from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
import logging
import re
from typing import Any

LOGGER = logging.getLogger(__name__)
PATCH_MARKER = "_codex_reset_tracker_twikit_patch"
ORIGINAL_ATTR = "_codex_reset_tracker_original"

ON_DEMAND_FILE_INDEX_REGEX = re.compile(
    r""",(\d+):["']ondemand\.s["']""",
    flags=re.VERBOSE | re.MULTILINE,
)
ON_DEMAND_HASH_PATTERN = r""",{}:["']([0-9a-f]+)["']"""


@dataclass(frozen=True)
class TwikitPatch:
    name: str
    reason: str
    apply: Callable[[], "TwikitPatchResult"]
    upstream_reference: str | None = None


@dataclass(frozen=True)
class TwikitPatchResult:
    name: str
    ok: bool
    applied: bool
    detail: str


def apply_twikit_compatibility_patches() -> tuple[TwikitPatchResult, ...]:
    """Apply the narrow Twikit monkeypatches required by current X payloads.

    Twikit does not expose extension hooks for the two broken parser paths this
    project needs to patch, so these are runtime monkeypatches by necessity.
    Keeping them in this registry makes the behavior explicit, idempotent, and
    easy to remove when upstream Twikit catches up.
    """
    results = tuple(patch.apply() for patch in TWIKIT_PATCHES)
    version = _twikit_version()
    for patch, result in zip(TWIKIT_PATCHES, results):
        reference = f" ({patch.upstream_reference})" if patch.upstream_reference else ""
        if result.ok:
            LOGGER.debug(
                "Twikit compat patch %s %s for twikit=%s: %s%s",
                patch.name,
                "applied" if result.applied else "skipped",
                version,
                result.detail,
                reference,
            )
        else:
            LOGGER.warning(
                "Twikit compat patch %s failed for twikit=%s: %s%s",
                patch.name,
                version,
                result.detail,
                reference,
            )
    return results


def patch_twikit() -> None:
    apply_twikit_compatibility_patches()


def _apply_client_transaction_manifest_patch() -> TwikitPatchResult:
    try:
        from twikit.x_client_transaction import transaction
    except Exception:
        LOGGER.debug("twikit transaction module is unavailable", exc_info=True)
        return TwikitPatchResult(
            name="client-transaction-manifest",
            ok=False,
            applied=False,
            detail="twikit transaction module is unavailable",
        )

    client_transaction = getattr(transaction, "ClientTransaction", None)
    if client_transaction is None:
        return TwikitPatchResult(
            name="client-transaction-manifest",
            ok=False,
            applied=False,
            detail="ClientTransaction class is missing",
        )
    if getattr(client_transaction.get_indices, PATCH_MARKER, False):
        return TwikitPatchResult(
            name="client-transaction-manifest",
            ok=True,
            applied=False,
            detail="already active",
        )

    original = client_transaction.get_indices
    setattr(_client_transaction_get_indices_compat, PATCH_MARKER, True)
    setattr(_client_transaction_get_indices_compat, ORIGINAL_ATTR, original)
    client_transaction.get_indices = _client_transaction_get_indices_compat
    return TwikitPatchResult(
        name="client-transaction-manifest",
        ok=True,
        applied=True,
        detail="ClientTransaction.get_indices now supports the current ondemand.s manifest",
    )


async def _client_transaction_get_indices_compat(self, home_page_response, session, headers):
    from twikit.x_client_transaction import transaction

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


def _apply_user_defaults_patch() -> TwikitPatchResult:
    try:
        from twikit import user as user_module
    except Exception:
        LOGGER.debug("twikit user module is unavailable", exc_info=True)
        return TwikitPatchResult(
            name="user-optional-fields",
            ok=False,
            applied=False,
            detail="twikit user module is unavailable",
        )

    user_class = getattr(user_module, "User", None)
    if user_class is None:
        return TwikitPatchResult(
            name="user-optional-fields",
            ok=False,
            applied=False,
            detail="User class is missing",
        )
    original_init = getattr(user_class, "__init__", None)
    if getattr(original_init, PATCH_MARKER, False):
        return TwikitPatchResult(
            name="user-optional-fields",
            ok=True,
            applied=False,
            detail="already active",
        )

    def patched_init(self, client, data):
        data = _with_user_defaults(data)
        return original_init(self, client, data)

    setattr(patched_init, PATCH_MARKER, True)
    setattr(patched_init, ORIGINAL_ATTR, original_init)
    user_class.__init__ = patched_init
    return TwikitPatchResult(
        name="user-optional-fields",
        ok=True,
        applied=True,
        detail="User.__init__ now fills optional profile fields before Twikit parses them",
    )


def _twikit_version() -> str:
    try:
        return metadata.version("twikit")
    except metadata.PackageNotFoundError:
        return "not-installed"


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


TWIKIT_PATCHES = (
    TwikitPatch(
        name="client-transaction-manifest",
        reason="X changed the ondemand.s asset manifest shape used by Twikit's x-client-transaction parser.",
        upstream_reference="https://github.com/d60/twikit/issues/408",
        apply=_apply_client_transaction_manifest_patch,
    ),
    TwikitPatch(
        name="user-optional-fields",
        reason="Current X GraphQL payloads sometimes omit optional profile fields that Twikit indexes directly.",
        apply=_apply_user_defaults_patch,
    ),
)
