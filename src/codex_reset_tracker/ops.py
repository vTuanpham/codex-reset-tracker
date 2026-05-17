from __future__ import annotations

import getpass
import json
import os
import signal
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import load_config


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

    source = Path(__file__).resolve().parents[2] / "config.example.json"
    raw_config = json.loads(source.read_text(encoding="utf-8"))

    timezone_name = _prompt("Your timezone", "Asia/Saigon", non_interactive)
    raw_config["local_timezone"] = timezone_name
    raw_config.setdefault("time", {})["user_timezone"] = timezone_name
    raw_config.setdefault("notifications", {}).setdefault("channels", {}).setdefault(
        "stdout", {}
    )["enabled"] = True

    if not non_interactive:
        print(
            "X/Twitter auth: browser cookies at data/x_cookies.json are recommended. "
            "Use Cookie-Editor for Chrome/Chromium to export x.com cookies as JSON. "
            "Username/password login is only a fallback and may be blocked by Cloudflare."
        )
    env_values: dict[str, str] = {}
    _maybe_env(env_values, "CODQ_X_USERNAME", "X/Twitter username (fallback if no cookies)", non_interactive)
    _maybe_env(env_values, "CODQ_X_EMAIL", "X/Twitter email (optional fallback login aid)", non_interactive)
    _maybe_env(env_values, "CODQ_X_PASSWORD", "X/Twitter password (fallback if no cookies)", non_interactive, secret=True)
    _maybe_env(
        env_values,
        "CODQ_X_TOTP_SECRET",
        "X/Twitter TOTP secret (optional; only if authenticator 2FA is enabled)",
        non_interactive,
        secret=True,
    )

    telegram = _prompt("Enable Telegram notifications? [y/N]", "n", non_interactive)
    if telegram.lower().startswith("y"):
        channels = raw_config["notifications"]["channels"]
        channels["telegram"]["enabled"] = True
        _maybe_env(env_values, "CODQ_TELEGRAM_BOT_TOKEN", "Telegram bot token", non_interactive, secret=True)
        _maybe_env(env_values, "CODQ_TELEGRAM_CHAT_ID", "Telegram chat id", non_interactive)

    webhook = _prompt("Enable generic webhook notifications? [y/N]", "n", non_interactive)
    if webhook.lower().startswith("y"):
        raw_config["notifications"]["channels"]["webhook"]["enabled"] = True
        _maybe_env(env_values, "CODQ_WEBHOOK_URL", "Webhook URL", non_interactive, secret=True)

    config_path.write_text(
        json.dumps(raw_config, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    env_path.write_text(_format_env(env_values), encoding="utf-8")
    Path(raw_config["data_dir"]).mkdir(parents=True, exist_ok=True)
    return config_path, env_path


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
            "enabled: " + ", ".join(enabled) if enabled else "enable at least stdout, Telegram, email, webhook, or desktop",
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


def _prompt(label: str, default: str, non_interactive: bool, *, secret: bool = False) -> str:
    if non_interactive:
        return default
    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    return value or default


def _maybe_env(
    env_values: dict[str, str],
    env_name: str,
    label: str,
    non_interactive: bool,
    *,
    secret: bool = False,
) -> None:
    value = _prompt(label, "", non_interactive, secret=secret)
    if value:
        env_values[env_name] = value


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
