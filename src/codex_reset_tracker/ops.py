from __future__ import annotations

import getpass
import html
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .notifiers import desktop_notification_backend, desktop_notifications_available


class OpsError(RuntimeError):
    pass


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


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
            f"found {config_path}" if config_path.exists() else "run codex-reset-tracker init-config",
        )
    )
    checks.append(
        DoctorCheck(
            "env",
            env_path.exists(),
            f"found {env_path}" if env_path.exists() else "create .env with the CODQ_* variables from the README",
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
            "enabled: " + ", ".join(enabled) if enabled else "enable at least stdout, Telegram, or desktop",
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


def _env_pair_check(name: str, first: str, second: str) -> DoctorCheck:
    missing = [env_name for env_name in (first, second) if not os.getenv(str(env_name))]
    return DoctorCheck(
        name,
        not missing,
        "required env vars are set"
        if not missing
        else "missing " + ", ".join(str(item) for item in missing) + "; run `uv run codex-reset-tracker telegram`",
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
        raise OpsError("systemctl is not installed; run `uv run codex-reset-tracker run` in the foreground instead")
    if not config_path.exists():
        raise OpsError(f"{config_path} does not exist; run codex-reset-tracker init-config first")
    load_config(config_path)
    python_path = _venv_python(project_dir)
    if not python_path.exists():
        raise OpsError(f"{python_path} does not exist; run ./install.sh or uv sync first")


def _venv_python(project_dir: Path) -> Path:
    if os.name == "nt":
        return project_dir / ".venv/Scripts/python.exe"
    return project_dir / ".venv/bin/python"


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
      <Command>C:\\Windows\\System32\\conhost.exe</Command>
      <Arguments>--headless C:\\Windows\\System32\\wsl.exe {html.escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)


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
