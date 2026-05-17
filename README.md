# Codex Reset Tracker

Watch trusted X/Twitter accounts for fresh `reset` posts and notify you by
Telegram, email, webhook, desktop popup, or stdout.

| Area | Default |
| --- | --- |
| Package runner | `uv` |
| Scraper | [Twikit](https://github.com/d60/twikit) |
| Match rule | `reset`, `resets`, `resetting`, `resetted` |
| Trust rule | tweet author must be in `polling.accounts` |
| User timezone | auto-detected from the current machine unless overridden |
| Source timezone | per-account, used for phrases like `this evening` |

## Fast Start

```bash
git clone https://github.com/vTuanpham/codex-reset-tracker.git
cd codex-reset-tracker
./install.sh
uv run codex-reset-tracker setup
uv run codex-reset-tracker doctor
uv run codex-reset-tracker test-notify
uv run codex-reset-tracker check
```

The first `check` is a baseline pass by default. Keep the process running to
catch newly discovered tweets:

```bash
uv run codex-reset-tracker run
```

## Setup Map

| Need | Command |
| --- | --- |
| Full first-time wizard | `uv run codex-reset-tracker setup` |
| Notification wizard only | `uv run codex-reset-tracker setup-notifications` |
| Account wizard only | `uv run codex-reset-tracker setup-accounts` |
| List tracked accounts | `uv run codex-reset-tracker accounts list` |
| Add one account | `uv run codex-reset-tracker accounts add thsottiaux --timezone America/Los_Angeles` |
| Remove one account | `uv run codex-reset-tracker accounts remove thsottiaux` |
| Refresh recommended accounts | `uv run codex-reset-tracker accounts defaults` |

`setup` writes normal settings to `config.json` and secrets to `.env`. You should
not need to hand-edit JSON for normal use.

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
3. Click Cookie-Editor while on `x.com`.
4. Export/copy JSON.
5. Save it here:

```bash
mkdir -p data
# save the Cookie-Editor JSON as:
data/x_cookies.json
```

Do not commit or share `data/x_cookies.json`; it can grant access to your X
session.

## Notifications

| Channel | Best for | CLI setup |
| --- | --- | --- |
| Telegram | fastest mobile alert | `setup-notifications` -> enable Telegram |
| Email | durable fallback | `setup-notifications` -> enable email |
| Webhook | Discord, Slack, ntfy, Pushover, Home Assistant | `setup-notifications` -> enable webhook |
| Desktop | active workstation popup | `setup-notifications` -> enable desktop |
| stdout | logs and testing | enabled by default |

Telegram quick path:

1. Message `@BotFather`.
2. Send `/newbot`.
3. Paste the token into the wizard.
4. Send any message to the new bot.
5. Let the wizard auto-detect the chat id, or paste it manually.

When running inside WSL, desktop notifications are forwarded to Windows through
`powershell.exe`.

## Accounts

The tracker has two safeguards:

1. Searches are generated as `from:<handle> reset`.
2. Every tweet is checked again locally; only authors in `polling.accounts` can
   alert.

That means broad or noisy search results from unrelated accounts are recorded as
`untrusted_author` and do not notify.

### Seeded Watchlist

The default watchlist includes official accounts and active developer-facing
people from OpenAI and Anthropic/Claude.

| Group | Handles |
| --- | --- |
| OpenAI official | `@OpenAI`, `@OpenAIDevs`, `@ChatGPTapp`, `@OpenAIStatus` |
| OpenAI people | `@sama`, `@gdb`, `@markchen90`, `@nickaturley`, `@kevinweil` |
| OpenAI Codex | `@thsottiaux`, `@embirico`, `@hansonwng`, `@katyhshi` |
| Anthropic official | `@AnthropicAI`, `@claudeai`, `@ClaudeDevs` |
| Anthropic people | `@DarioAmodei`, `@DanielaAmodei`, `@jackclarkSF`, `@mikeyk`, `@ch402` |
| Claude Code | `@bcherny` |

Sources used to seed this list include official X pages and public index pages
for [OpenAI Developers](https://x.com/OpenAIDevs),
[Tibo / Codex](https://x.com/thsottiaux/with_replies?lang=en),
[Anthropic](https://x.com/AnthropicAI/status/2025997928242811253?lang=en),
[Claude](https://x.com/claudeai/status/1972706815885373936), the reported
[@ClaudeDevs launch](https://awesomeagents.ai/news/anthropic-claudedevs-x-account-launch/),
and public profiles for [Boris Cherny](https://x.com/bcherny/status/2015524460481388760)
and the [Anthropic radar](https://llmgram.app/anthropic-radar/).

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

## Background Run

| Mode | Commands |
| --- | --- |
| systemd user service | `uv run codex-reset-tracker service install` then `uv run codex-reset-tracker service start` |
| portable daemon | `uv run codex-reset-tracker daemon start` |
| foreground | `uv run codex-reset-tracker run` |

Useful status/log commands:

```bash
uv run codex-reset-tracker service status
uv run codex-reset-tracker service logs
uv run codex-reset-tracker daemon status
uv run codex-reset-tracker daemon logs
uv run codex-reset-tracker status
```

### Optional WSL Auto-Start

If you use WSL and want tracker auto-start after Windows logon/unlock/wake,
install the Windows scheduled-task bridge:

```bash
uv run codex-reset-tracker service install
uv run codex-reset-tracker service start
uv run codex-reset-tracker windows-startup install --force
uv run codex-reset-tracker windows-startup status
```

If distro detection fails, set it explicitly:

```bash
uv run codex-reset-tracker windows-startup install --distro Ubuntu --force
```

Microsoft documents that [`wsl.exe` can run a specific distro from Windows](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)
and that [WSL supports `systemd`](https://learn.microsoft.com/en-us/windows/wsl/systemd);
this project uses both pieces for the Windows Scheduled Task bridge.

## Diagnostics

Historical diagnostic scan, without touching production state or sending real
notifications:

```bash
uv run codex-reset-tracker debug-scan \
  --config config.json \
  --query 'from:thsottiaux reset' \
  --dump-stream data/runtime/debug-scan.jsonl
```

Use a specific account override:

```bash
uv run codex-reset-tracker debug-scan --account thsottiaux
```

Run tests:

```bash
uv run ruff check .
uv run python -m unittest discover -s tests
```

## Runtime Notes

| Topic | Detail |
| --- | --- |
| Fresh tweets | first scan baselines visible tweets unless `alert_on_first_scan=true` |
| Rate limits | default polling is 5 minutes plus jitter |
| Twikit patches | compatibility monkeypatch registry lives in `src/codex_reset_tracker/twikit_compat.py` |
| State | SQLite tracks seen tweet ids and alerted text hashes |
| Secrets | `.env`, `data/`, cookies, and local DBs are git-ignored |
