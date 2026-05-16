from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .models import TweetRecord
from .notifiers import NotificationManager
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
        if args.command == "check":
            return asyncio.run(check(args))
        if args.command == "run":
            return asyncio.run(run(args))
        if args.command == "test-notify":
            return asyncio.run(test_notify(args))
    except ConfigError as exc:
        LOGGER.error("%s", exc)
        return 2
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

    check_cmd = subcommands.add_parser("check", help="run one scrape and notification pass")
    check_cmd.add_argument("--config", type=Path, default=Path("config.json"))

    run_cmd = subcommands.add_parser("run", help="run continuously")
    run_cmd.add_argument("--config", type=Path, default=Path("config.json"))

    notify = subcommands.add_parser("test-notify", help="send a synthetic alert through configured channels")
    notify.add_argument("--config", type=Path, default=Path("config.json"))
    notify.add_argument(
        "--message",
        default="Synthetic alert: Codex quota limits may have reset.",
    )
    return parser


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


async def run(args) -> int:
    config = load_config(args.config)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    await QuotaResetTracker(config).run_forever()
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


if __name__ == "__main__":
    sys.exit(main())
