from __future__ import annotations

import getpass
import html
import json
import os
import signal
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .accounts import (
    DEFAULT_TRACKED_ACCOUNTS,
    normalize_handle,
    unique_handles,
)
from .config import detect_local_timezone, is_valid_timezone, load_config
from .notifiers import desktop_notification_backend, desktop_notifications_available


class OpsError(RuntimeError):
    pass


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def write_setup(
    *,
    config_path: Path,
    env_path: Path,
    force: bool,
    non_interactive: bool,
) -> tuple[Path, Path]:
    if config_path.exists() and not force:
        raise OpsError(f"{config_path} already exists; pass --force to overwrite")
    if env_path.exists() and not force:
        raise OpsError(f"{env_path} already exists; pass --force to overwrite")

    raw_config = _starter_config()
    env_values = _read_env_values(env_path)

    if not non_interactive:
        _print_setup_header("First-time setup")
    _configure_timezone(raw_config, non_interactive)
    _configure_auth(env_values, non_interactive)
    _configure_accounts(
        raw_config,
        non_interactive,
        step_label="Step 3/5 - Tracked accounts",
    )
    _configure_notifications(
        raw_config,
        env_values,
        non_interactive,
        step_label="Step 4/5 - Notifications",
    )

    if not non_interactive:
        _confirm_write_or_raise(
            config_path=config_path,
            env_path=env_path,
            raw_config=raw_config,
            non_interactive=non_interactive,
            step_label="Step 5/5 - Confirm",
        )

    config_path.write_text(
        json.dumps(raw_config, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    env_path.write_text(_format_env(env_values), encoding="utf-8")
    Path(raw_config["data_dir"]).mkdir(parents=True, exist_ok=True)
    if not non_interactive:
        _print_next_steps(first_time=True)
    return config_path, env_path


def write_notification_setup(
    *,
    config_path: Path,
    env_path: Path,
    non_interactive: bool,
) -> tuple[Path, Path]:
    raw_config = _read_config_or_starter(config_path)
    env_values = _read_env_values(env_path)

    if not non_interactive:
        _print_setup_header("Notification setup")
        print(
            "This updates only the notification part of config.json and the related "
            "secrets in .env. Existing X/Twitter auth values are kept."
        )
    _configure_notifications(
        raw_config,
        env_values,
        non_interactive,
        step_label="Step 1/2 - Notifications",
    )
    if not non_interactive:
        _confirm_write_or_raise(
            config_path=config_path,
            env_path=env_path,
            raw_config=raw_config,
            non_interactive=non_interactive,
            step_label="Step 2/2 - Confirm",
        )

    config_path.write_text(
        json.dumps(raw_config, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    env_path.write_text(_format_env(env_values), encoding="utf-8")
    Path(raw_config.get("data_dir", "data")).mkdir(parents=True, exist_ok=True)
    if not non_interactive:
        _print_next_steps(first_time=False)
    return config_path, env_path


def write_account_setup(
    *,
    config_path: Path,
    non_interactive: bool,
) -> Path:
    raw_config = _read_config_or_starter(config_path)
    if not non_interactive:
        _print_setup_header("Account setup")
        print("This updates polling.accounts and per-account source timezones.")
        print("Alerts still require the tweet author to be in polling.accounts.")
        _print_accounts(raw_config)

    _configure_accounts(
        raw_config,
        non_interactive,
        step_label="Step 1/2 - Tracked accounts",
    )
    if not non_interactive:
        _print_accounts(raw_config)
        if not _yes_no("Write account changes now?", True, non_interactive):
            raise OpsError("account setup cancelled; no files were changed")

    _write_config(config_path, raw_config)
    if not non_interactive:
        print("\nNext CLI steps:")
        print("  1. Run: uv run codex-reset-tracker doctor")
        print("  2. Run: uv run codex-reset-tracker debug-scan --account <handle>")
        print("  3. Restart whichever run mode is already active.")
    return config_path


def account_summary(config_path: Path) -> str:
    raw_config = _read_config_or_starter(config_path)
    polling = raw_config.setdefault("polling", {})
    time_config = raw_config.setdefault("time", {})
    timezones = time_config.setdefault("account_timezones", {})
    accounts = unique_handles(polling.get("accounts", []))
    if not accounts:
        return "No tracked accounts."
    lines = ["Tracked accounts:"]
    for handle in accounts:
        lines.append(f"  @{handle}  source_timezone={timezones.get(handle, _account_timezone_default(handle))}")
    return "\n".join(lines)


def add_account_config(config_path: Path, handle: str, timezone_name: str | None = None) -> Path:
    raw_config = _read_config_or_starter(config_path)
    add_account_to_config(raw_config, handle, timezone_name or _account_timezone_default(handle))
    _sync_reset_search_queries(raw_config)
    _write_config(config_path, raw_config)
    return config_path


def remove_account_config(config_path: Path, handle: str) -> Path:
    raw_config = _read_config_or_starter(config_path)
    remove_account_from_config(raw_config, handle)
    _sync_reset_search_queries(raw_config)
    _write_config(config_path, raw_config)
    return config_path


def install_default_accounts(config_path: Path) -> Path:
    raw_config = _read_config_or_starter(config_path)
    _merge_default_accounts(raw_config)
    _sync_reset_search_queries(raw_config)
    _write_config(config_path, raw_config)
    return config_path


def doctor_checks(config_path: Path, env_path: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(
        DoctorCheck(
            "uv",
            shutil.which("uv") is not None,
            "uv is on PATH" if shutil.which("uv") else "install uv or run ./install.sh",
        )
    )
    checks.append(
        DoctorCheck(
            "virtualenv",
            _venv_python(Path.cwd()).exists(),
            ".venv Python exists" if _venv_python(Path.cwd()).exists() else "run uv sync",
        )
    )
    checks.append(
        DoctorCheck(
            "config",
            config_path.exists(),
            f"found {config_path}" if config_path.exists() else "run codex-reset-tracker setup",
        )
    )
    checks.append(
        DoctorCheck(
            "env",
            env_path.exists(),
            f"found {env_path}" if env_path.exists() else "run codex-reset-tracker setup",
        )
    )

    if not config_path.exists():
        return checks

    if env_path.exists():
        try:
            from dotenv import load_dotenv
        except ImportError:
            pass
        else:
            load_dotenv(env_path, override=False)
    try:
        config = load_config(config_path)
    except Exception as exc:
        checks.append(DoctorCheck("config-load", False, str(exc)))
        return checks

    checks.append(DoctorCheck("config-load", True, "config loaded"))
    checks.append(
        DoctorCheck(
            "timezone",
            True,
            f"user timezone resolves to {config.time.user_timezone}",
        )
    )
    has_cookies = config.twitter.cookies_file.exists()
    has_login = bool(config.twitter.username and config.twitter.password)
    checks.append(
        DoctorCheck(
            "x-auth",
            has_cookies or has_login,
            f"cookies file exists at {config.twitter.cookies_file}"
            if has_cookies
            else (
                "X/Twitter username and password env vars are set"
                if has_login
                else (
                    "recommended: use Cookie-Editor to export x.com browser cookies "
                    "to data/x_cookies.json; fallback: set CODQ_X_USERNAME/"
                    "CODQ_X_PASSWORD in .env"
                )
            ),
        )
    )
    checks.extend(_notification_checks(config))
    return checks


def install_user_service(config_path: Path, *, force: bool) -> Path:
    unit_path = _unit_path()
    if unit_path.exists() and not force:
        raise OpsError(f"{unit_path} already exists; pass --force to overwrite")

    project_dir = Path.cwd()
    _validate_service_prereqs(project_dir=project_dir, config_path=config_path)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        _unit_text(project_dir=project_dir, config_path=config_path),
        encoding="utf-8",
    )
    _systemctl("daemon-reload")
    _systemctl("enable", unit_path.name)
    return unit_path


def service_action(action: str) -> None:
    if action == "logs":
        subprocess.run(
            ["journalctl", "--user", "-u", _unit_name(), "-n", "80", "--no-pager"],
            check=True,
        )
        return
    _systemctl(action, _unit_name())


def install_windows_startup_task(
    *,
    config_path: Path,
    task_name: str,
    distro: str | None = None,
    linux_user: str | None = None,
    force: bool = False,
) -> str:
    if not _is_wsl_environment():
        raise OpsError("windows-startup is only available from inside WSL")
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise OpsError("powershell.exe is unavailable; enable WSL Windows interop")

    project_dir = Path.cwd()
    _validate_service_prereqs(project_dir=project_dir, config_path=config_path)
    if not _unit_path().exists():
        raise OpsError("codex-reset-tracker.service is not installed; run `uv run codex-reset-tracker service install` first")
    distro_name = distro or os.getenv("WSL_DISTRO_NAME")
    if not distro_name:
        raise OpsError("Could not detect WSL distro name; pass --distro")
    user_name = linux_user or getpass.getuser()
    xml = _windows_startup_task_xml(
        distro=distro_name,
        linux_user=user_name,
        project_dir=project_dir,
        config_path=config_path,
    )
    command = (
        "$xml = @'\n"
        f"{xml}\n"
        "'@\n"
        f"Register-ScheduledTask -TaskName { _powershell_quote(task_name) } -Xml $xml"
        + (" -Force" if force else "")
    )
    subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
    )
    return task_name


def uninstall_windows_startup_task(task_name: str) -> str:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise OpsError("powershell.exe is unavailable; enable WSL Windows interop")
    command = f"Unregister-ScheduledTask -TaskName { _powershell_quote(task_name) } -Confirm:$false"
    subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
    )
    return task_name


def windows_startup_task_status(task_name: str) -> str:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise OpsError("powershell.exe is unavailable; enable WSL Windows interop")
    command = (
        f"$task = Get-ScheduledTask -TaskName { _powershell_quote(task_name) } -ErrorAction SilentlyContinue; "
        "if ($null -eq $task) { 'not installed' } "
        "else { \"installed state=$($task.State)\" }"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def daemon_start(config_path: Path) -> Path:
    config = load_config(config_path)
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    pid_path = config.runtime_dir / "tracker.pid"
    log_path = config.runtime_dir / "tracker.log"
    if _pid_alive(pid_path):
        raise OpsError(f"daemon already running with pid {pid_path.read_text().strip()}")

    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "codex_reset_tracker",
                "run",
                "--config",
                str(config_path),
            ],
            cwd=Path.cwd(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    return pid_path


def daemon_stop(config_path: Path) -> bool:
    config = load_config(config_path)
    pid_path = config.runtime_dir / "tracker.pid"
    if not pid_path.exists():
        return False
    pid = int(pid_path.read_text(encoding="utf-8").strip())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return False
    return True


def daemon_status(config_path: Path) -> str:
    config = load_config(config_path)
    pid_path = config.runtime_dir / "tracker.pid"
    if _pid_alive(pid_path):
        return f"running pid={pid_path.read_text().strip()}"
    return "stopped"


def read_status(config_path: Path) -> dict:
    config = load_config(config_path)
    path = config.runtime_dir / "status.json"
    if not path.exists():
        raise OpsError(f"No status file found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _notification_checks(config) -> list[DoctorCheck]:
    channels = config.notifications.channels
    checks: list[DoctorCheck] = []
    enabled = [name for name, values in channels.items() if values.get("enabled")]
    checks.append(
        DoctorCheck(
            "notifications",
            bool(enabled),
            _notification_detail(enabled) if enabled else "enable at least stdout, Telegram, email, webhook, or desktop",
        )
    )
    if channels.get("telegram", {}).get("enabled"):
        checks.append(
            _env_pair_check(
                "telegram-env",
                channels["telegram"].get("bot_token_env", "CODQ_TELEGRAM_BOT_TOKEN"),
                channels["telegram"].get("chat_id_env", "CODQ_TELEGRAM_CHAT_ID"),
            )
        )
    if channels.get("webhook", {}).get("enabled"):
        checks.append(
            _env_check(
                "webhook-env",
                channels["webhook"].get("url_env", "CODQ_WEBHOOK_URL"),
            )
        )
    if channels.get("email", {}).get("enabled"):
        required = (
            channels["email"].get("smtp_host_env", "CODQ_SMTP_HOST"),
            channels["email"].get("from_env", "CODQ_EMAIL_FROM"),
            channels["email"].get("to_env", "CODQ_EMAIL_TO"),
        )
        missing = [name for name in required if not os.getenv(str(name))]
        checks.append(
            DoctorCheck(
                "email-env",
                not missing,
                "required email env vars are set"
                if not missing
                else "missing " + ", ".join(str(name) for name in missing),
            )
        )
    if channels.get("desktop", {}).get("enabled"):
        backend = desktop_notification_backend()
        checks.append(
            DoctorCheck(
                "desktop-notifications",
                desktop_notifications_available(),
                _desktop_check_detail(backend),
            )
        )
    return checks


def _env_check(name: str, env_name: str) -> DoctorCheck:
    return DoctorCheck(
        name,
        bool(os.getenv(str(env_name))),
        f"{env_name} is set" if os.getenv(str(env_name)) else f"missing {env_name}",
    )


def _env_pair_check(name: str, first: str, second: str) -> DoctorCheck:
    missing = [env_name for env_name in (first, second) if not os.getenv(str(env_name))]
    return DoctorCheck(
        name,
        not missing,
        "required env vars are set" if not missing else "missing " + ", ".join(str(item) for item in missing),
    )


def _desktop_check_detail(backend: str) -> str:
    if backend == "wsl-windows":
        return "WSL detected; Windows notifications will be sent through powershell.exe"
    if backend == "wsl-windows-unavailable":
        return "WSL detected, but powershell.exe is unavailable; enable WSL Windows interop"
    if backend == "linux-notify-send":
        return "native Linux notify-send is available"
    if backend == "linux-notify-send-unavailable":
        return "notify-send is unavailable"
    if backend == "macos-osascript":
        return "macOS osascript is available"
    if backend == "macos-osascript-unavailable":
        return "osascript is unavailable"
    if backend == "windows-powershell":
        return "Windows PowerShell notifications are available"
    if backend == "windows-powershell-unavailable":
        return "powershell.exe is unavailable"
    return f"unsupported desktop notification backend: {backend}"


def _validate_service_prereqs(*, project_dir: Path, config_path: Path) -> None:
    if not shutil.which("systemctl"):
        raise OpsError("systemctl is not installed; use codex-reset-tracker daemon start instead")
    if not config_path.exists():
        raise OpsError(f"{config_path} does not exist; run codex-reset-tracker setup first")
    load_config(config_path)
    python_path = _venv_python(project_dir)
    if not python_path.exists():
        raise OpsError(f"{python_path} does not exist; run ./install.sh or uv sync first")


def _venv_python(project_dir: Path) -> Path:
    if os.name == "nt":
        return project_dir / ".venv/Scripts/python.exe"
    return project_dir / ".venv/bin/python"


def _starter_config() -> dict[str, Any]:
    source = Path(__file__).resolve().parents[2] / "config.example.json"
    return json.loads(source.read_text(encoding="utf-8"))


def _write_config(config_path: Path, raw_config: dict[str, Any]) -> None:
    config_path.write_text(
        json.dumps(raw_config, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _read_config_or_starter(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raw_config = _starter_config()
        _ensure_notification_defaults(raw_config)
        return raw_config
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OpsError(f"Invalid JSON config {config_path}: {exc}") from exc
    if not isinstance(raw_config, dict):
        raise OpsError(f"{config_path} must contain a JSON object")
    _ensure_notification_defaults(raw_config)
    return raw_config


def _ensure_notification_defaults(raw_config: dict[str, Any]) -> None:
    default_channels = _starter_config()["notifications"]["channels"]
    notifications = raw_config.setdefault("notifications", {})
    if not isinstance(notifications, dict):
        raise OpsError("notifications must be a JSON object")
    channels = notifications.setdefault("channels", {})
    if not isinstance(channels, dict):
        raise OpsError("notifications.channels must be a JSON object")
    for name, defaults in default_channels.items():
        channel = channels.setdefault(name, {})
        if not isinstance(channel, dict):
            raise OpsError(f"notifications.channels.{name} must be a JSON object")
        for key, value in defaults.items():
            channel.setdefault(key, value)


def _merge_default_accounts(raw_config: dict[str, Any]) -> None:
    for account in DEFAULT_TRACKED_ACCOUNTS:
        add_account_to_config(raw_config, account.handle, account.timezone)


def add_account_to_config(
    raw_config: dict[str, Any],
    handle: str,
    timezone_name: str,
) -> None:
    normalized = normalize_handle(handle)
    if not normalized:
        raise OpsError("Account handle cannot be blank")
    if not is_valid_timezone(timezone_name):
        raise OpsError(f"Invalid source timezone for @{normalized}: {timezone_name}")
    polling = raw_config.setdefault("polling", {})
    accounts = unique_handles([*polling.get("accounts", []), normalized])
    polling["accounts"] = accounts
    time_config = raw_config.setdefault("time", {})
    account_timezones = time_config.setdefault("account_timezones", {})
    account_timezones[normalized] = timezone_name


def remove_account_from_config(raw_config: dict[str, Any], handle: str) -> None:
    normalized = normalize_handle(handle)
    key = normalized.lower()
    polling = raw_config.setdefault("polling", {})
    polling["accounts"] = [
        item for item in unique_handles(polling.get("accounts", []))
        if item.lower() != key
    ]
    time_config = raw_config.setdefault("time", {})
    account_timezones = time_config.setdefault("account_timezones", {})
    for stored in list(account_timezones):
        if stored.lower() == key:
            del account_timezones[stored]


def _sync_reset_search_queries(raw_config: dict[str, Any]) -> None:
    polling = raw_config.setdefault("polling", {})
    accounts = unique_handles(polling.get("accounts", []))
    polling["accounts"] = accounts
    polling.setdefault("search_queries", [])


def _account_timezone_default(handle: str) -> str:
    normalized = normalize_handle(handle).lower()
    for account in DEFAULT_TRACKED_ACCOUNTS:
        if account.handle.lower() == normalized:
            return account.timezone
    return "America/Los_Angeles"


def _print_accounts(raw_config: dict[str, Any]) -> None:
    polling = raw_config.setdefault("polling", {})
    time_config = raw_config.setdefault("time", {})
    timezones = time_config.setdefault("account_timezones", {})
    accounts = unique_handles(polling.get("accounts", []))
    print("\nTracked accounts:")
    if not accounts:
        print("  none")
        return
    for handle in accounts:
        print(f"  @{handle}  source_timezone={timezones.get(handle, _account_timezone_default(handle))}")


def _print_setup_header(title: str) -> None:
    print()
    print(f"== {title} ==")
    print("The wizard will ask only for values that cannot be guessed safely.")
    print("Secrets are written to .env. Non-secret behavior is written to config.json.")


def _configure_timezone(raw_config: dict[str, Any], non_interactive: bool) -> None:
    print("\nStep 1/4 - Local timezone") if not non_interactive else None
    detected = detect_local_timezone()
    if not non_interactive:
        print(f"Detected local timezone: {detected}")
        print("Use `auto` to keep detecting from the machine at runtime.")
    timezone_name = _prompt("Your timezone override, or auto", "auto", non_interactive)
    raw_config["local_timezone"] = timezone_name
    raw_config.setdefault("time", {})["user_timezone"] = timezone_name


def _configure_auth(env_values: dict[str, str], non_interactive: bool) -> None:
    if not non_interactive:
        print("\nStep 2/4 - X/Twitter login")
        print("Recommended auth path:")
        print("  1. Install Cookie-Editor:")
        print("     https://chromewebstore.google.com/detail/cookie-editor/ookdjilphngeeeghgngjabigmpepanpl?hl=en-US&utm_source=ext_sidebar")
        print("  2. Open https://x.com in that browser and log in.")
        print("  3. Click Cookie-Editor while on x.com and export JSON.")
        print("  4. Save that JSON as data/x_cookies.json in this repo.")
        print("Username/password login is only a fallback because X often blocks it with Cloudflare.")
    _set_or_keep_env(
        env_values,
        "CODQ_X_USERNAME",
        "X/Twitter username fallback, blank if using cookies",
        non_interactive,
    )
    _set_or_keep_env(
        env_values,
        "CODQ_X_EMAIL",
        "X/Twitter email fallback, optional",
        non_interactive,
    )
    _set_or_keep_env(
        env_values,
        "CODQ_X_PASSWORD",
        "X/Twitter password fallback, blank if using cookies",
        non_interactive,
        secret=True,
    )
    _set_or_keep_env(
        env_values,
        "CODQ_X_TOTP_SECRET",
        "X/Twitter TOTP secret, only if authenticator-app 2FA is enabled",
        non_interactive,
        secret=True,
    )


def _configure_accounts(
    raw_config: dict[str, Any],
    non_interactive: bool,
    *,
    step_label: str,
) -> None:
    if not non_interactive:
        print(f"\n{step_label}")
        print("Default watchlist includes official OpenAI/Anthropic/Claude accounts plus active developer-facing people.")
        print("Only tweets from these trusted handles can alert.")
    if _yes_no("Install or refresh the recommended OpenAI + Anthropic watchlist?", True, non_interactive):
        _merge_default_accounts(raw_config)

    while not non_interactive and _yes_no("Add another account manually?", False, non_interactive):
        handle = normalize_handle(_prompt("X/Twitter handle", "", non_interactive))
        if not handle:
            continue
        timezone_name = _prompt(
            "Source timezone for this account",
            _account_timezone_default(handle),
            non_interactive,
        )
        add_account_to_config(raw_config, handle, timezone_name)

    while not non_interactive and _yes_no("Remove an account?", False, non_interactive):
        handle = normalize_handle(_prompt("Handle to remove", "", non_interactive))
        if handle:
            remove_account_from_config(raw_config, handle)

    _sync_reset_search_queries(raw_config)
    if not non_interactive:
        _print_accounts(raw_config)


def _configure_notifications(
    raw_config: dict[str, Any],
    env_values: dict[str, str],
    non_interactive: bool,
    *,
    step_label: str,
) -> None:
    _ensure_notification_defaults(raw_config)
    channels = raw_config["notifications"]["channels"]
    if not non_interactive:
        print(f"\n{step_label}")
        print("Recommended path: desktop first for local confirmation, Telegram for phone alerts.")
        print("You can enable multiple channels now and rerun setup-notifications later.")

    _configure_desktop(channels, non_interactive)
    channels["stdout"]["enabled"] = _yes_no(
        "Keep console/stdout alerts enabled?",
        bool(channels.get("stdout", {}).get("enabled", True)),
        non_interactive,
    )
    _configure_telegram(channels, env_values, non_interactive)
    _configure_email(channels, env_values, non_interactive)
    _configure_webhook(channels, env_values, non_interactive)


def _configure_telegram(
    channels: dict[str, dict[str, Any]],
    env_values: dict[str, str],
    non_interactive: bool,
) -> None:
    enabled = _yes_no(
        "Enable Telegram mobile alerts?",
        bool(channels.get("telegram", {}).get("enabled", False)),
        non_interactive,
    )
    channels["telegram"]["enabled"] = enabled
    if not enabled:
        return
    if not non_interactive:
        print("\nTelegram setup steps:")
        print("  1. Open Telegram and message @BotFather.")
        print("  2. Send /newbot, follow the prompts, then copy the bot token.")
        print("  3. Open your new bot chat and send it any message.")
        print("  4. The wizard can try to read your chat id from Telegram getUpdates.")
    token = _set_or_keep_env(
        env_values,
        channels["telegram"].get("bot_token_env", "CODQ_TELEGRAM_BOT_TOKEN"),
        "Telegram bot token from @BotFather",
        non_interactive,
        secret=True,
    )
    if not token:
        channels["telegram"]["enabled"] = False
        print("Telegram disabled because no bot token was provided.") if not non_interactive else None
        return
    if token and _yes_no("Try to auto-detect Telegram chat id now?", True, non_interactive):
        chat_id = _fetch_telegram_chat_id(token)
        if chat_id:
            print(f"Detected Telegram chat id: {chat_id}") if not non_interactive else None
            env_values[channels["telegram"].get("chat_id_env", "CODQ_TELEGRAM_CHAT_ID")] = chat_id
            return
        if not non_interactive:
            print("Could not auto-detect a chat id yet.")
            print("Make sure you sent a message to your bot, then rerun setup-notifications.")
            print('Manual CLI check: curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates"')
            print("Copy result.message.chat.id or channel_post.chat.id from the JSON.")
    _set_or_keep_env(
        env_values,
        channels["telegram"].get("chat_id_env", "CODQ_TELEGRAM_CHAT_ID"),
        "Telegram chat id",
        non_interactive,
    )
    if not env_values.get(channels["telegram"].get("chat_id_env", "CODQ_TELEGRAM_CHAT_ID")):
        channels["telegram"]["enabled"] = False
        print("Telegram disabled because no chat id was provided.") if not non_interactive else None


def _configure_email(
    channels: dict[str, dict[str, Any]],
    env_values: dict[str, str],
    non_interactive: bool,
) -> None:
    enabled = _yes_no(
        "Enable email alerts?",
        bool(channels.get("email", {}).get("enabled", False)),
        non_interactive,
    )
    channels["email"]["enabled"] = enabled
    if not enabled:
        return
    if not non_interactive:
        print("\nEmail setup steps:")
        print("  1. Use an SMTP provider. Gmail and Outlook usually require an app password.")
        print("  2. Common hosts: smtp.gmail.com, smtp.office365.com, smtp.mail.yahoo.com.")
        print("  3. Port 587 with STARTTLS is the normal default.")
    email_config = channels["email"]
    host_env = email_config.get("smtp_host_env", "CODQ_SMTP_HOST")
    username_env = email_config.get("smtp_username_env", "CODQ_SMTP_USERNAME")
    password_env = email_config.get("smtp_password_env", "CODQ_SMTP_PASSWORD")
    from_env = email_config.get("from_env", "CODQ_EMAIL_FROM")
    to_env = email_config.get("to_env", "CODQ_EMAIL_TO")
    env_values[host_env] = _prompt("SMTP host", env_values.get(host_env, "smtp.gmail.com"), non_interactive)
    email_config["smtp_port"] = _prompt_int(
        "SMTP port",
        int(email_config.get("smtp_port", 587)),
        non_interactive,
    )
    username = _set_or_keep_env(
        env_values,
        username_env,
        "SMTP username, usually your email address",
        non_interactive,
    )
    _set_or_keep_env(
        env_values,
        password_env,
        "SMTP password or app password",
        non_interactive,
        secret=True,
    )
    sender_default = env_values.get(from_env) or username
    env_values[from_env] = _prompt("From email address", sender_default, non_interactive)
    env_values[to_env] = _prompt(
        "To email address or comma-separated list",
        env_values.get(to_env, env_values[from_env]),
        non_interactive,
    )
    if not env_values.get(host_env) or not env_values.get(from_env) or not env_values.get(to_env):
        channels["email"]["enabled"] = False
        print("Email disabled because SMTP host, from address, and to address are required.") if not non_interactive else None
        return
    email_config["starttls"] = _yes_no(
        "Use STARTTLS?",
        bool(email_config.get("starttls", True)),
        non_interactive,
    )


def _configure_webhook(
    channels: dict[str, dict[str, Any]],
    env_values: dict[str, str],
    non_interactive: bool,
) -> None:
    enabled = _yes_no(
        "Enable webhook alerts for Discord, Slack, ntfy, Pushover, etc.?",
        bool(channels.get("webhook", {}).get("enabled", False)),
        non_interactive,
    )
    channels["webhook"]["enabled"] = enabled
    if not enabled:
        return
    if not non_interactive:
        print("\nWebhook setup steps:")
        print("  Discord: Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL.")
        print("  Slack: create an incoming webhook app and copy the webhook URL.")
        print("  ntfy/Pushover/Home Assistant: paste the incoming webhook URL they provide.")
    channels["webhook"]["format"] = _choice(
        "Webhook format",
        ("generic", "discord", "slack"),
        str(channels["webhook"].get("format", "generic")),
        non_interactive,
    )
    url = _set_or_keep_env(
        env_values,
        channels["webhook"].get("url_env", "CODQ_WEBHOOK_URL"),
        "Webhook URL",
        non_interactive,
        secret=True,
    )
    if not url:
        channels["webhook"]["enabled"] = False
        print("Webhook disabled because no URL was provided.") if not non_interactive else None


def _configure_desktop(channels: dict[str, dict[str, Any]], non_interactive: bool) -> None:
    enabled = _yes_no(
        "Enable desktop notifications on this machine?",
        bool(channels.get("desktop", {}).get("enabled", False)),
        non_interactive,
    )
    channels["desktop"]["enabled"] = enabled
    if enabled and not non_interactive:
        print("Desktop notes:")
        print("  WSL sends notifications to Windows through powershell.exe automatically.")
        print("  Native Linux needs notify-send.")
        print("  macOS uses osascript.")
        print("  Windows uses PowerShell.")


def _confirm_write_or_raise(
    *,
    config_path: Path,
    env_path: Path,
    raw_config: dict[str, Any],
    non_interactive: bool,
    step_label: str,
) -> None:
    if non_interactive:
        return
    print(f"\n{step_label}")
    print(f"Config file: {config_path}")
    print(f"Secrets file: {env_path}")
    print("Enabled notification channels: " + ", ".join(_enabled_channels(raw_config)))
    if not _yes_no("Write these files now?", True, non_interactive):
        raise OpsError("setup cancelled; no files were changed")


def _print_next_steps(*, first_time: bool) -> None:
    print("\nNext CLI steps:")
    if first_time:
        print("  1. If using cookies, save Cookie-Editor JSON to data/x_cookies.json.")
        print("  2. Run: uv run codex-reset-tracker doctor")
        print("  3. Run: uv run codex-reset-tracker test-notify")
        print("  4. Run: uv run codex-reset-tracker check")
        print("  5. Keep it running: uv run codex-reset-tracker run")
    else:
        print("  1. Run: uv run codex-reset-tracker doctor")
        print("  2. Run: uv run codex-reset-tracker test-notify")
        print("  3. Restart whichever run mode is already active.")
    print("Background service:")
    print("  uv run codex-reset-tracker service install")
    print("  uv run codex-reset-tracker service start")
    print("Fallback only if service install fails:")
    print("  uv run codex-reset-tracker daemon start")


def _enabled_channels(raw_config: dict[str, Any]) -> list[str]:
    channels = raw_config.get("notifications", {}).get("channels", {})
    enabled = [name for name, values in channels.items() if values.get("enabled")]
    return enabled or ["none"]


def _notification_detail(enabled: list[str]) -> str:
    if enabled == ["stdout"]:
        return "enabled: stdout; console only. Run setup-notifications for Telegram, email, webhook, or desktop."
    return "enabled: " + ", ".join(enabled)


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _unquote_env_value(raw_value.strip())
    return values


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _prompt(label: str, default: str, non_interactive: bool, *, secret: bool = False) -> str:
    if non_interactive:
        return default
    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    return value or default


def _prompt_int(label: str, default: int, non_interactive: bool) -> int:
    if non_interactive:
        return default
    while True:
        value = _prompt(label, str(default), non_interactive)
        try:
            parsed = int(value)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if parsed <= 0:
            print("Please enter a number greater than zero.")
            continue
        return parsed


def _yes_no(label: str, default: bool, non_interactive: bool) -> bool:
    if non_interactive:
        return default
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _choice(
    label: str,
    options: tuple[str, ...],
    default: str,
    non_interactive: bool,
) -> str:
    default = default if default in options else options[0]
    if non_interactive:
        return default
    choices = "/".join(options)
    while True:
        value = input(f"{label} ({choices}) [{default}]: ").strip().lower()
        if not value:
            return default
        if value in options:
            return value
        print("Please choose one of: " + ", ".join(options))


def _set_or_keep_env(
    env_values: dict[str, str],
    env_name: str,
    label: str,
    non_interactive: bool,
    *,
    secret: bool = False,
) -> str:
    current = env_values.get(env_name, "")
    if non_interactive:
        return current
    prompt_label = label
    if current:
        prompt_label += " (leave blank to keep current value)"
    value = _prompt(prompt_label, "", non_interactive, secret=secret)
    if value:
        env_values[env_name] = value
        return value
    return current


def _fetch_telegram_chat_id(token: str) -> str | None:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return _extract_telegram_chat_id(payload)


def _extract_telegram_chat_id(payload: dict[str, Any]) -> str | None:
    for update in reversed(payload.get("result", [])):
        if not isinstance(update, dict):
            continue
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            item = update.get(key)
            if not isinstance(item, dict):
                continue
            chat = item.get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None:
                return str(chat["id"])
            if key == "my_chat_member":
                chat = item.get("chat")
                if isinstance(chat, dict) and chat.get("id") is not None:
                    return str(chat["id"])
    return None


def _format_env(values: dict[str, str]) -> str:
    if not values:
        return "# Add secrets here. setup left them blank.\n"
    lines = []
    for key, value in values.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + "\n"


def _unit_name() -> str:
    return "codex-reset-tracker.service"


def _unit_path() -> Path:
    return Path.home() / ".config/systemd/user" / _unit_name()


def _unit_text(*, project_dir: Path, config_path: Path) -> str:
    return f"""[Unit]
Description=Codex quota reset tracker
After=network-online.target

[Service]
Type=simple
WorkingDirectory={project_dir}
EnvironmentFile=-{project_dir / ".env"}
ExecStart={project_dir / ".venv/bin/python"} -m codex_reset_tracker run --config {config_path}
Restart=on-failure
RestartSec=20

[Install]
WantedBy=default.target
"""


def _windows_startup_task_xml(
    *,
    distro: str,
    linux_user: str,
    project_dir: Path,
    config_path: Path,
) -> str:
    linux_command = (
        f"cd {sh_quote(str(project_dir))} "
        '&& export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" '
        f"&& {sh_quote(str(project_dir / '.venv/bin/python'))} "
        "-m codex_reset_tracker service start"
    )
    arguments = subprocess.list2cmdline(
        [
            "-d",
            distro,
            "-u",
            linux_user,
            "--cd",
            str(project_dir),
            "--exec",
            "/bin/sh",
            "-lc",
            linux_command,
        ]
    )
    wake_subscription = (
        "<QueryList>"
        '<Query Id="0" Path="System">'
        '<Select Path="System">'
        "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]"
        "</Select>"
        "</Query>"
        "</QueryList>"
    )
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Start the Codex Reset Tracker WSL service after Windows logon, unlock, or wake.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <SessionStateChangeTrigger>
      <Enabled>true</Enabled>
      <StateChange>SessionUnlock</StateChange>
    </SessionStateChangeTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>{html.escape(wake_subscription)}</Subscription>
    </EventTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Windows\\System32\\wsl.exe</Command>
      <Arguments>{html.escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)


def _pid_alive(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError):
        return False
    return True


def _is_wsl_environment() -> bool:
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return False
    return "microsoft" in release.lower() or "wsl" in release.lower()


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
