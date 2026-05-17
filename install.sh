#!/usr/bin/env sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "uv is not installed and curl is unavailable."
    echo "Install uv from https://docs.astral.sh/uv/ then rerun ./install.sh."
    exit 1
  fi
  echo "uv is not installed; installing uv with Astral's official installer..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  if [ -d "$HOME/.local/bin" ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv was installed but is not on PATH yet."
  echo "Open a new shell, or add $HOME/.local/bin to PATH, then rerun ./install.sh."
  exit 1
fi

uv sync

cat <<'EOF'

Install complete.

Next:
  uv run codex-reset-tracker setup
  # Existing installs can rerun only notification setup:
  uv run codex-reset-tracker setup-notifications
  uv run codex-reset-tracker doctor
  uv run codex-reset-tracker test-notify
  uv run codex-reset-tracker check

Background service:
  uv run codex-reset-tracker service install
  uv run codex-reset-tracker service start
EOF
