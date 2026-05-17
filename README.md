# Codex Reset Tracker

Small Python service for watching selected X/Twitter accounts and searches for
possible Codex quota or usage-limit reset announcements, then sending one alert
per matching tweet.

The clean notification path is:

1. Telegram for fast mobile delivery.
2. Email as a durable fallback.
3. Generic webhook for Discord, Slack, ntfy, Pushover, Home Assistant, or any
   other messaging app with an incoming webhook.
4. Desktop notification only when the tracker runs on your active workstation.

The scraper adapter uses [Twikit](https://github.com/d60/twikit). Twikit's docs
show an async client with cookie-backed login, tweet search, user lookup, and
user timeline retrieval through `search_tweet`, `get_user_by_screen_name`, and
`get_user_tweets`.

## Quickstart

```bash
git clone https://github.com/vTuanpham/codex-reset-tracker.git
cd codex-reset-tracker
./install.sh
```

Run the guided setup wizard:

```bash
uv run codex-reset-tracker setup
```

The wizard walks you through:

- your local timezone for reset-window translation
- X/Twitter auth
- notification channels
- final confirmation before writing files

It writes non-secret settings to `config.json` and secrets to `.env`. You should
not need to hand-edit `config.json` for normal setup.

## CLI Setup Flow

### 1. Install

```bash
git clone https://github.com/vTuanpham/codex-reset-tracker.git
cd codex-reset-tracker
./install.sh
```

### 2. Run The Wizard

```bash
uv run codex-reset-tracker setup
```

The wizard asks for values in the terminal and prints the exact next command at
the end. Press Enter to accept defaults.

If you already have `config.json` and only want to configure notifications, run:

```bash
uv run codex-reset-tracker setup-notifications
```

That command preserves existing X/Twitter auth values in `.env`.

### 3. Add X/Twitter Auth

Recommended path: browser cookies. X/Twitter often blocks fresh
username/password automation with Cloudflare.

Cookie setup step by step:

1. Install Cookie-Editor:
   https://chromewebstore.google.com/detail/cookie-editor/ookdjilphngeeeghgngjabigmpepanpl?hl=en-US&utm_source=ext_sidebar
2. Open `https://x.com` in Chrome, Chromium, Brave, Edge, or another compatible
   browser.
3. Log into the X/Twitter account the tracker should use.
4. Click the Cookie-Editor extension icon while you are on `x.com`.
5. Use Cookie-Editor's export action and copy/download the JSON export.
6. In this repo, create the data directory if needed:

   ```bash
   mkdir -p data
   ```

7. Save the Cookie-Editor JSON at `data/x_cookies.json`.

When `data/x_cookies.json` exists, the tracker loads cookies and skips a fresh
username/password login attempt. Do not commit or share `data/x_cookies.json`;
it can grant access to your X session.

Fallback path: username/password. The setup wizard can write these env vars:

- `CODQ_X_USERNAME`
- `CODQ_X_PASSWORD`
- optional `CODQ_X_EMAIL`
- optional `CODQ_X_TOTP_SECRET`

`CODQ_X_TOTP_SECRET` is only for X/Twitter accounts that have authenticator-app
2FA enabled. Leave it blank if your account does not use TOTP-based 2FA.

### 4. Configure Notifications

The setup wizard asks about each notification channel. You can enable multiple
channels.

Telegram setup in the CLI:

1. Run:

   ```bash
   uv run codex-reset-tracker setup-notifications
   ```

2. Answer `y` to `Enable Telegram mobile alerts?`.
3. In Telegram, message `@BotFather`.
4. Send `/newbot`, follow the prompts, and copy the bot token.
5. Paste the bot token into the CLI prompt.
6. Open your new bot chat and send it any message.
7. Let the wizard try to auto-detect your chat id.
8. If auto-detect does not find it yet, rerun `setup-notifications` after the
   bot receives a message, or paste the chat id manually.

Email setup in the CLI:

1. Run `uv run codex-reset-tracker setup-notifications`.
2. Answer `y` to `Enable email alerts?`.
3. Enter your SMTP host, usually one of:
   - `smtp.gmail.com`
   - `smtp.office365.com`
   - `smtp.mail.yahoo.com`
4. Use port `587` with STARTTLS unless your provider says otherwise.
5. Enter SMTP username and password/app password.
6. Enter the from address and recipient address.

Webhook setup in the CLI:

1. Run `uv run codex-reset-tracker setup-notifications`.
2. Answer `y` to webhook alerts.
3. Choose `generic`, `discord`, or `slack`.
4. Paste the incoming webhook URL.

Desktop setup in the CLI:

1. Run `uv run codex-reset-tracker setup-notifications`.
2. Answer `y` to desktop notifications.
3. On WSL, the tracker detects WSL and sends the popup to Windows through
   `powershell.exe`.
4. Native Linux needs `notify-send`; macOS uses `osascript`; Windows uses
   PowerShell.
5. Verify it immediately with:

   ```bash
   uv run codex-reset-tracker test-notify
   ```

### 5. Verify

Check local readiness:

```bash
uv run codex-reset-tracker doctor
```

Optionally verify Twikit can authenticate:

```bash
uv run codex-reset-tracker doctor --live-auth
```

Test notifications without scraping X/Twitter:

```bash
uv run codex-reset-tracker test-notify
```

Run one scan:

```bash
uv run codex-reset-tracker check
```

The first scan is a baseline pass by default. It records currently visible tweets
without alerting. Leave it running after that to catch newly discovered tweets.

## Background Run

On Linux with user-level systemd:

```bash
uv run codex-reset-tracker service install
uv run codex-reset-tracker service start
uv run codex-reset-tracker service status
uv run codex-reset-tracker service logs
```

If `systemd --user` is unavailable, use the portable daemon fallback:

```bash
uv run codex-reset-tracker daemon start
uv run codex-reset-tracker daemon status
uv run codex-reset-tracker daemon logs
```

Run in the foreground instead:

```bash
uv run codex-reset-tracker run
```

Show the last successful scan summary:

```bash
uv run codex-reset-tracker status
```

## Diagnostics

Run a historical diagnostic scan and dump the normalized tweet stream plus match
decisions without sending real notifications or touching the production state DB:

```bash
uv run codex-reset-tracker debug-scan \
  --config config.json \
  --query '(codex OR "ChatGPT Codex") (quota OR limit OR cap) (reset OR refreshed OR "later today")' \
  --dump-stream data/runtime/debug-scan.jsonl
```

Run local tests:

```bash
uv run python -m unittest discover -s tests
```

## Matching Strategy

The default matcher requires all three contexts:

- Product: `codex`, `chatgpt codex`, or `openai codex`
- Quota object: `quota`, `usage limit`, `rate limit`, `message cap`, or similar
- Reset/increase action: `reset`, `refresh`, `renew`, `increase`, `bump`, or similar

It also excludes unrelated account/password/factory reset language. Tune
`matching.include_patterns` and `matching.exclude_patterns` in `config.json`
without changing code.

## Accounts And Searches

The example config starts with:

- `sama`
- `thsottiaux`
- `OpenAI`
- `OpenAIDevs`
- `ChatGPTapp`
- `OpenAIStatus`

Add more employee or official handles as you decide they are trustworthy. The
service intentionally treats the watchlist as configuration, because a hardcoded
"all OpenAI employees" list would be stale and noisy.

## Fresh Tweet Semantics

By default, the first scan is a baseline pass. It records the currently visible
tweets and does not alert on them. After that, alerts only fire for tweet ids the
local SQLite state has not seen before. If a tweet exposes a parseable creation
time, the scanner also suppresses items older than the process start time minus
`polling.new_tweet_grace_seconds`.

Set `polling.alert_on_first_scan` to `true` only when you intentionally want
backfill alerts from historical tweets.

## Reset Window Estimates

When a matching tweet contains timing language, alerts include an approximate
window. Ambiguous phrases such as `this evening` are interpreted in the
poster/source timezone first, then translated to `time.user_timezone` for you.
Configure source timezones with `time.account_timezones` and
`time.default_source_timezone`.

Supported cues include phrases such as `later today`, `today`, `tonight`, `this
evening`, `this afternoon`, `tomorrow`, `soon`, `in 2 hours`, `within 30
minutes`, and simple `at 5pm` style times.

## Runtime Notes

- Use a low-frequency polling interval. The default is 5 minutes with jitter.
- Twikit uses authenticated scraping; use your own account and keep the polling
  behavior conservative.
- Twikit compatibility fixes live in `src/codex_reset_tracker/twikit_compat.py`
  as an explicit monkeypatch registry. Each patch is named, idempotent, and
  scoped to the current upstream parser breakage.
- Desktop notifications from WSL are routed to Windows through `powershell.exe`.
  If `doctor` says `wsl-windows-unavailable`, enable WSL Windows interop or use
  Telegram/email/webhook instead.
- SQLite tracks seen tweet ids and alerted tweet text hashes. The default runner
  alerts on newly discovered tweet ids only, not historical backfill or old
  tweets edited later.
- Desktop notification uses `notify-send` on Linux, `osascript` on macOS, and a
  best-effort PowerShell popup on Windows.
- Secrets are loaded automatically from `.env` next to `config.json`.
