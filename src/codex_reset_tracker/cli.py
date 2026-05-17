from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .config import ConfigError, load_config
from .models import TweetRecord
from .notifiers import NotificationError, NotificationManager
from .ops import (
    OpsError,
    account_summary,
    add_account_config,
    daemon_start,
    daemon_status,
    daemon_stop,
    doctor_checks,
    install_default_accounts,
    install_user_service,
    read_status,
    remove_account_config,
    service_action,
    write_account_setup,
    write_notification_setup,
    write_setup,
)
from .runner import QuotaResetTracker

LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.command == "init-config":
            return init_config(args)
        if args.command == "setup":
            return setup(args)
        if args.command == "setup-notifications":
            return setup_notifications(args)
        if args.command == "setup-accounts":
            return setup_accounts(args)
        if args.command == "accounts":
            return accounts(args)
        if args.command == "doctor":
            return asyncio.run(doctor(args))
        if args.command == "check":
            return asyncio.run(check(args))
        if args.command == "debug-scan":
            return asyncio.run(debug_scan(args))
        if args.command == "run":
            return asyncio.run(run(args))
        if args.command == "status":
            return status(args)
        if args.command == "service":
            return service(args)
        if args.command == "daemon":
            return daemon(args)
        if args.command == "test-notify":
            return asyncio.run(test_notify(args))
    except (ConfigError, OpsError) as exc:
        LOGGER.error("%s", exc)
        return 2
    except NotificationError as exc:
        LOGGER.error("%s", exc)
        LOGGER.error("Run `uv run codex-reset-tracker setup-notifications` to configure or repair notification channels.")
        return 2
    except subprocess.CalledProcessError as exc:
        LOGGER.error("command failed with exit code %s: %s", exc.returncode, exc.cmd)
        return exc.returncode
    except KeyboardInterrupt:
        LOGGER.info("stopped")
        return 130
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-reset-tracker")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init-config", help="write a starter config")
    init.add_argument("--path", type=Path, default=Path("config.json"))
    init.add_argument("--force", action="store_true")

    setup_cmd = subcommands.add_parser("setup", help="write config.json and .env")
    setup_cmd.add_argument("--config", type=Path, default=Path("config.json"))
    setup_cmd.add_argument("--env", type=Path, default=Path(".env"))
    setup_cmd.add_argument("--force", action="store_true")
    setup_cmd.add_argument("--non-interactive", action="store_true")

    notification_setup = subcommands.add_parser(
        "setup-notifications",
        help="guide notification setup and update config.json/.env",
    )
    notification_setup.add_argument("--config", type=Path, default=Path("config.json"))
    notification_setup.add_argument("--env", type=Path, default=Path(".env"))
    notification_setup.add_argument("--non-interactive", action="store_true")

    account_setup = subcommands.add_parser(
        "setup-accounts",
        help="guide tracked account setup and update config.json",
    )
    account_setup.add_argument("--config", type=Path, default=Path("config.json"))
    account_setup.add_argument("--non-interactive", action="store_true")

    accounts_cmd = subcommands.add_parser("accounts", help="list or edit tracked accounts")
    accounts_cmd.add_argument("--config", type=Path, default=Path("config.json"))
    accounts_subcommands = accounts_cmd.add_subparsers(dest="accounts_command", required=True)
    accounts_subcommands.add_parser("list", help="list tracked accounts")
    accounts_subcommands.add_parser("defaults", help="install or refresh recommended accounts")
    add_account = accounts_subcommands.add_parser("add", help="add one tracked account")
    add_account.add_argument("handle")
    add_account.add_argument(
        "--timezone",
        default=None,
        help="source timezone for this account, for example America/Los_Angeles",
    )
    remove_account = accounts_subcommands.add_parser("remove", help="remove one tracked account")
    remove_account.add_argument("handle")

    doctor_cmd = subcommands.add_parser("doctor", help="check local readiness")
    doctor_cmd.add_argument("--config", type=Path, default=Path("config.json"))
    doctor_cmd.add_argument("--env", type=Path, default=Path(".env"))
    doctor_cmd.add_argument(
        "--live-auth",
        action="store_true",
        help="try connecting to X/Twitter with Twikit credentials or cookies",
    )

    check_cmd = subcommands.add_parser("check", help="run one scrape and notification pass")
    check_cmd.add_argument("--config", type=Path, default=Path("config.json"))

    debug_scan = subcommands.add_parser(
        "debug-scan",
        help="run one diagnostic scan and dump tweet/match decisions to JSONL",
    )
    debug_scan.add_argument("--config", type=Path, default=Path("config.json"))
    debug_scan.add_argument(
        "--dump-stream",
        type=Path,
        default=Path("data/runtime/debug-scan.jsonl"),
    )
    debug_scan.add_argument("--query", action="append", default=[])
    debug_scan.add_argument("--account", action="append", default=[])
    debug_scan.add_argument("--fresh-only", action="store_true")
    debug_scan.add_argument("--notify", action="store_true")
    debug_scan.add_argument(
        "--write-state",
        action="store_true",
        help="write seen/alerted rows to the configured production state DB",
    )

    run_cmd = subcommands.add_parser("run", help="run continuously")
    run_cmd.add_argument("--config", type=Path, default=Path("config.json"))

    status_cmd = subcommands.add_parser("status", help="show last tracker status")
    status_cmd.add_argument("--config", type=Path, default=Path("config.json"))

    service_cmd = subcommands.add_parser("service", help="manage user-level systemd service")
    service_subcommands = service_cmd.add_subparsers(dest="service_command", required=True)
    service_install = service_subcommands.add_parser("install")
    service_install.add_argument("--config", type=Path, default=Path("config.json"))
    service_install.add_argument("--force", action="store_true")
    for command in ("start", "stop", "restart", "status", "logs", "uninstall"):
        service_subcommands.add_parser(command)

    daemon_cmd = subcommands.add_parser("daemon", help="manage portable background daemon fallback")
    daemon_subcommands = daemon_cmd.add_subparsers(dest="daemon_command", required=True)
    for command in ("start", "stop", "status", "logs"):
        item = daemon_subcommands.add_parser(command)
        item.add_argument("--config", type=Path, default=Path("config.json"))

    notify = subcommands.add_parser("test-notify", help="send a synthetic alert through configured channels")
    notify.add_argument("--config", type=Path, default=Path("config.json"))
    notify.add_argument(
        "--message",
        default="Synthetic alert: Codex quota limits may have reset.",
    )
    return parser


def setup(args) -> int:
    config_path, env_path = write_setup(
        config_path=args.config,
        env_path=args.env,
        force=args.force,
        non_interactive=args.non_interactive,
    )
    LOGGER.info("wrote %s and %s", config_path, env_path)
    LOGGER.info("next: run `uv run codex-reset-tracker doctor`")
    return 0


def setup_notifications(args) -> int:
    config_path, env_path = write_notification_setup(
        config_path=args.config,
        env_path=args.env,
        non_interactive=args.non_interactive,
    )
    LOGGER.info("updated notification setup in %s and %s", config_path, env_path)
    LOGGER.info("next: run `uv run codex-reset-tracker test-notify`")
    return 0


def setup_accounts(args) -> int:
    config_path = write_account_setup(
        config_path=args.config,
        non_interactive=args.non_interactive,
    )
    LOGGER.info("updated tracked accounts in %s", config_path)
    LOGGER.info("next: run `uv run codex-reset-tracker doctor`")
    return 0


def accounts(args) -> int:
    command = args.accounts_command
    if command == "list":
        print(account_summary(args.config))
    elif command == "defaults":
        install_default_accounts(args.config)
        LOGGER.info("installed recommended tracked accounts in %s", args.config)
    elif command == "add":
        add_account_config(args.config, args.handle, args.timezone)
        LOGGER.info("added @%s to %s", args.handle.lstrip("@"), args.config)
    elif command == "remove":
        remove_account_config(args.config, args.handle)
        LOGGER.info("removed @%s from %s", args.handle.lstrip("@"), args.config)
    return 0


async def doctor(args) -> int:
    checks = doctor_checks(args.config, args.env)
    failed = False
    for check in checks:
        status_text = "OK" if check.ok else "FAIL"
        print(f"[{status_text}] {check.name}: {check.detail}")
        failed = failed or not check.ok

    if args.live_auth:
        try:
            config = load_config(args.config)
            tracker = QuotaResetTracker(config)
            await tracker.connect()
        except Exception as exc:
            print(f"[FAIL] live-auth: {exc}")
            failed = True
        else:
            print("[OK] live-auth: Twikit connected")

    if failed:
        print("\nFix failed checks, then rerun `uv run codex-reset-tracker doctor`.")
        return 2
    print("\nReady. Next: `uv run codex-reset-tracker test-notify` then `uv run codex-reset-tracker check`.")
    return 0


def init_config(args) -> int:
    source = Path(__file__).resolve().parents[2] / "config.example.json"
    if not source.exists():
        source = Path("config.example.json")
    if args.path.exists() and not args.force:
        LOGGER.error("%s already exists; pass --force to overwrite", args.path)
        return 2
    shutil.copyfile(source, args.path)
    LOGGER.info("wrote %s", args.path)
    return 0


async def check(args) -> int:
    config = load_config(args.config)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    tracker = QuotaResetTracker(config)
    await tracker.connect()
    summary = await tracker.scan_once()
    LOGGER.info(
        "scan complete: scanned=%s matched=%s alerted=%s duplicates=%s",
        summary.scanned,
        summary.matched,
        summary.alerted,
        summary.duplicates,
    )
    return 0


async def debug_scan(args) -> int:
    config = load_config(args.config)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if not args.write_state:
        config.runtime_dir.mkdir(parents=True, exist_ok=True)
        config = replace(config, state_path=config.runtime_dir / "debug-state.sqlite3")
    if args.query or args.account:
        config = replace(
            config,
            polling=replace(
                config.polling,
                search_queries=tuple(args.query) if args.query else config.polling.search_queries,
                accounts=tuple(args.account) if args.account else config.polling.accounts,
            ),
        )
    notifier = None if args.notify else DryRunNotifier()
    tracker = QuotaResetTracker(
        config,
        notifier=notifier,
        allow_historical=not args.fresh_only,
        dump_stream_path=args.dump_stream,
    )
    await tracker.connect()
    summary = await tracker.scan_once()
    LOGGER.info(
        "debug scan complete: scanned=%s matched=%s alerted=%s duplicates=%s dump=%s",
        summary.scanned,
        summary.matched,
        summary.alerted,
        summary.duplicates,
        args.dump_stream,
    )
    return 0


async def run(args) -> int:
    config = load_config(args.config)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    await QuotaResetTracker(config).run_forever()
    return 0


def status(args) -> int:
    payload = read_status(args.config)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def service(args) -> int:
    command = args.service_command
    if command == "install":
        path = install_user_service(args.config, force=args.force)
        LOGGER.info("installed %s", path)
    elif command == "uninstall":
        service_action("disable")
        unit_path = Path.home() / ".config/systemd/user/codex-reset-tracker.service"
        unit_path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        LOGGER.info("uninstalled user service")
    else:
        service_action(command)
    return 0


def daemon(args) -> int:
    command = args.daemon_command
    if command == "start":
        path = daemon_start(args.config)
        LOGGER.info("daemon started; pid file: %s", path)
    elif command == "stop":
        stopped = daemon_stop(args.config)
        LOGGER.info("daemon %s", "stopping" if stopped else "was not running")
    elif command == "status":
        print(daemon_status(args.config))
    elif command == "logs":
        config = load_config(args.config)
        log_path = config.runtime_dir / "tracker.log"
        if log_path.exists():
            print(log_path.read_text(encoding="utf-8")[-8000:])
        else:
            LOGGER.info("no daemon log found at %s", log_path)
    return 0


async def test_notify(args) -> int:
    config = load_config(args.config)
    tweet = TweetRecord(
        id="synthetic",
        author_username="OpenAI",
        author_name="OpenAI",
        text=args.message,
        created_at=None,
        url="https://x.com/OpenAI",
        source="synthetic",
    )
    match = config_to_match(config, tweet)
    delivery = await NotificationManager(config.notifications).send_match(match)
    LOGGER.info("test notification sent: %s", delivery)
    return 0


def config_to_match(config, tweet: TweetRecord):
    from .matcher import RegexMatcher
    from .models import TweetMatch

    match = RegexMatcher(config.matching).match(tweet)
    if match is not None:
        return match
    return TweetMatch(
        tweet=tweet,
        matched_patterns=("synthetic",),
        excerpt=tweet.text,
    )


class DryRunNotifier:
    async def send_match(self, match):
        print(
            "DRY-RUN MATCH "
            f"@{match.tweet.author_username} {match.tweet.url}: {match.excerpt}",
            flush=True,
        )
        return {"dry_run": {"ok": True}}


if __name__ == "__main__":
    sys.exit(main())
