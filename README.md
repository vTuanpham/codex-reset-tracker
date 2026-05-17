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

## Install

```bash
uv sync
```

Copy the example config and keep secrets in environment variables:

```bash
uv run codex-reset-tracker setup
```

Run one scan:

```bash
uv run codex-reset-tracker check --config config.json
```

Run continuously:

```bash
uv run codex-reset-tracker run --config config.json
```

Install as a user-level background service:

```bash
uv run codex-reset-tracker service install
uv run codex-reset-tracker service start
uv run codex-reset-tracker service status
uv run codex-reset-tracker service logs
```

If your environment does not support `systemd --user`, use the portable daemon
fallback:

```bash
uv run codex-reset-tracker daemon start
uv run codex-reset-tracker daemon status
uv run codex-reset-tracker daemon logs
```

Show the last successful scan summary:

```bash
uv run codex-reset-tracker status --config config.json
```

Send a test alert without scraping X/Twitter:

```bash
uv run codex-reset-tracker test-notify --config config.json
```

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
- SQLite tracks seen tweet ids and alerted tweet text hashes. The default runner
  alerts on newly discovered tweet ids only, not historical backfill or old
  tweets edited later.
- Desktop notification uses `notify-send` on Linux, `osascript` on macOS, and a
  best-effort PowerShell popup on Windows.
- Secrets are loaded automatically from `.env` next to `config.json`.
