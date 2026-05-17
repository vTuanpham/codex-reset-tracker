from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackedAccount:
    handle: str
    timezone: str
    group: str
    note: str


DEFAULT_TRACKED_ACCOUNTS: tuple[TrackedAccount, ...] = (
    TrackedAccount("OpenAI", "America/Los_Angeles", "openai-official", "official OpenAI"),
    TrackedAccount("OpenAIDevs", "America/Los_Angeles", "openai-official", "official OpenAI developer updates"),
    TrackedAccount("ChatGPTapp", "America/Los_Angeles", "openai-official", "official ChatGPT product updates"),
    TrackedAccount("OpenAIStatus", "America/Los_Angeles", "openai-official", "official OpenAI status"),
    TrackedAccount("sama", "America/Los_Angeles", "openai-people", "Sam Altman"),
    TrackedAccount("gdb", "America/Los_Angeles", "openai-people", "Greg Brockman"),
    TrackedAccount("markchen90", "America/Los_Angeles", "openai-people", "Mark Chen"),
    TrackedAccount("nickaturley", "America/Los_Angeles", "openai-people", "Nick Turley"),
    TrackedAccount("kevinweil", "America/Los_Angeles", "openai-people", "Kevin Weil"),
    TrackedAccount("thsottiaux", "America/Los_Angeles", "openai-codex", "Thibault Sottiaux / Codex"),
    TrackedAccount("embirico", "America/Los_Angeles", "openai-codex", "Alexander Embiricos / Codex"),
    TrackedAccount("hansonwng", "America/Los_Angeles", "openai-codex", "Hanson Wang / Codex"),
    TrackedAccount("katyhshi", "America/Los_Angeles", "openai-codex", "Katy Shi / Codex"),
    TrackedAccount("AnthropicAI", "America/Los_Angeles", "anthropic-official", "official Anthropic"),
    TrackedAccount("claudeai", "America/Los_Angeles", "anthropic-official", "official Claude product updates"),
    TrackedAccount("ClaudeDevs", "America/Los_Angeles", "anthropic-official", "official Claude developer updates"),
    TrackedAccount("DarioAmodei", "America/Los_Angeles", "anthropic-people", "Dario Amodei"),
    TrackedAccount("DanielaAmodei", "America/Los_Angeles", "anthropic-people", "Daniela Amodei"),
    TrackedAccount("jackclarkSF", "America/Los_Angeles", "anthropic-people", "Jack Clark"),
    TrackedAccount("mikeyk", "America/Los_Angeles", "anthropic-people", "Mike Krieger"),
    TrackedAccount("ch402", "America/Los_Angeles", "anthropic-people", "Chris Olah"),
    TrackedAccount("bcherny", "America/Los_Angeles", "anthropic-code", "Boris Cherny / Claude Code"),
)


def default_account_handles() -> tuple[str, ...]:
    return tuple(account.handle for account in DEFAULT_TRACKED_ACCOUNTS)


def default_account_timezones() -> dict[str, str]:
    return {account.handle: account.timezone for account in DEFAULT_TRACKED_ACCOUNTS}


def reset_search_queries(accounts: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"from:{normalize_handle(account)} reset" for account in accounts if normalize_handle(account))


def normalize_handle(value: str) -> str:
    return value.strip().lstrip("@")


def unique_handles(handles: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for handle in handles:
        normalized = normalize_handle(handle)
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result
