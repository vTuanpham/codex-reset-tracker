# Codex Reset Tracker

Watch trusted X/Twitter accounts for fresh `reset` posts (Codex and Claude usage
limits) and notify you by Telegram or a desktop popup.

| Area | Default |
| --- | --- |
| Package runner | `uv` |
| Scraper | [Twikit](https://github.com/d60/twikit) |
| Match rule | `reset`, `resets`, `resetting`, `resetted` |
| Trust rule | tweet author must be in `polling.accounts` |
| User timezone | auto-detected from the current machine unless overridden |
| Source timezone | per-account, used for phrases like `this evening` |

## Quick Start

```bash
git clone https://github.com/vTuanpham/codex-reset-tracker.git
cd codex-reset-tracker
./install.sh
uv run codex-reset-tracker init-config   # writes config.json from config.example.json
```

Then:

1. Edit `config.json`: set `enabled` for the channels you want and review `polling.accounts`.
2. Put secrets in `.env` (see below).
3. Save your X cookies as `data/x_cookies.json` (see X/Twitter Auth).

```bash
uv run codex-reset-tracker doctor
uv run codex-reset-tracker test-notify
uv run codex-reset-tracker check
uv run codex-reset-tracker run
```

The first `check` is a baseline pass by default. Keep `run` going to catch newly
discovered tweets, or install the background service below.

## X/Twitter Auth

Cookies are strongly preferred. X/Twitter often blocks automated
username/password login with Cloudflare.

| Path | When to use | Setup |
| --- | --- | --- |
| Browser cookies | Recommended | export x.com cookies to `data/x_cookies.json` |
| Username/password | Fallback only | set `CODQ_X_USERNAME` and `CODQ_X_PASSWORD` in `.env` |
| TOTP secret | Only if authenticator-app 2FA is enabled | set `CODQ_X_TOTP_SECRET` |

Cookie steps:

1. Install [Cookie-Editor (Chrome Web Store)](https://chromewebstore.google.com/detail/cookie-editor/ookdjilphngeeeghgngjabigmpepanpl?hl=en-US&utm_source=ext_sidebar).
2. Open `https://x.com` and log in.
3. Click Cookie-Editor while on `x.com`, export the JSON, and save it as `data/x_cookies.json`.

Do not commit or share `data/x_cookies.json`; it can grant access to your X
session.

## Notifications

| Channel | Best for | Setup |
| --- | --- | --- |
| Desktop | active workstation popup | `channels.desktop.enabled: true` |
| Telegram | fastest mobile alert | `channels.telegram.enabled: true`, plus the two env vars below |
| stdout | logs and testing | `channels.stdout.enabled: true` |

Telegram: message `@BotFather`, send `/newbot`, then put the token and your chat
id in `.env`:

```
CODQ_TELEGRAM_BOT_TOKEN=...
CODQ_TELEGRAM_CHAT_ID=...
```

Desktop: on Windows and in WSL this is a toast under its own "Codex Reset
Tracker" name, showing the tweet's words, the author and the reset window, with an
Open tweet button (WSL forwards it to Windows through `powershell.exe`). On Linux it
uses `notify-send`, on macOS `osascript`.

| Telegram |
| --- |
| <img src="assets/telegram_noti_example.JPG" alt="Telegram notification example" width="420"> |

## Accounts

Only authors listed in `polling.accounts` can alert. Every tweet is checked
locally; results from other accounts are recorded as `untrusted_author` and do
not notify. Edit the list in `config.json`. The defaults cover official accounts
and developer-facing people from OpenAI and Anthropic:

| Group | Handles |
| --- | --- |
| OpenAI official | `@OpenAI`, `@OpenAIDevs`, `@ChatGPTapp`, `@OpenAIStatus` |
| OpenAI people | `@sama`, `@gdb`, `@markchen90`, `@nickaturley`, `@kevinweil` |
| OpenAI Codex | `@thsottiaux`, `@embirico`, `@hansonwng`, `@katyhshi` |
| Anthropic official | `@AnthropicAI`, `@claudeai`, `@ClaudeDevs` |
| Anthropic people | `@DarioAmodei`, `@DanielaAmodei`, `@jackclarkSF`, `@mikeyk`, `@ch402` |
| Claude Code | `@bcherny` |

An account X reports as suspended or nonexistent is skipped for a day after one
warning.

## Timezones

| Timezone | Purpose | Default |
| --- | --- | --- |
| User timezone | where alert windows are shown | `auto` |
| Source timezone | how tweet phrases are interpreted | per account |

Example: if `@thsottiaux` says `this evening`, the phrase is interpreted in
`America/Los_Angeles`, then translated to your detected local timezone.

Override your timezone only when needed:

```json
{
  "local_timezone": "Asia/Saigon",
  "time": {
    "user_timezone": "Asia/Saigon"
  }
}
```

## Background Service

```bash
uv run codex-reset-tracker service install
uv run codex-reset-tracker service start
uv run codex-reset-tracker service status
uv run codex-reset-tracker service logs
```

It runs through `systemd --user`, gets restart handling, and writes logs to the
user journal. For one-off testing run `uv run codex-reset-tracker run` in the
foreground and `uv run codex-reset-tracker status` for the last scan.

### Optional WSL Auto-Start

To start the tracker after Windows logon, unlock or wake, install the Windows
scheduled-task bridge:

```bash
uv run codex-reset-tracker windows-startup install --force
uv run codex-reset-tracker windows-startup status
```

The task wakes WSL and starts the `service`. It runs through
`conhost.exe --headless`, so no console window appears; re-run the install
with `--force` to update an older task that opens one. If distro detection
fails, pass `--distro Ubuntu`.

## Diagnostics

A historical scan, without touching production state or sending real
notifications:

```bash
uv run codex-reset-tracker debug-scan --account thsottiaux --dump-stream data/runtime/debug-scan.jsonl
```

Run tests:

```bash
uv run ruff check .
uv run python -m unittest discover -s tests
```

## Runtime Notes

| Topic | Detail |
| --- | --- |
| Fresh tweets | first-ever scan baselines visible tweets; restarts catch up since the last scan, capped at 24 hours |
| Rate limits | default polling is 20 minutes plus jitter |
| Twikit patches | compatibility monkeypatch registry lives in `src/codex_reset_tracker/twikit_compat.py` |
| State | SQLite tracks seen tweet ids and alerted text hashes |
| Secrets | `.env`, `data/`, cookies, and local DBs are git-ignored |
