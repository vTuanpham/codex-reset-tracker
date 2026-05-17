import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_reset_tracker.ops import (
    account_summary,
    add_account_config,
    _extract_telegram_chat_id,
    install_default_accounts,
    remove_account_config,
    _validate_service_prereqs,
    doctor_checks,
    OpsError,
    _windows_startup_task_xml,
    _unit_text,
    write_account_setup,
    write_notification_setup,
    write_setup,
)


class OpsTests(unittest.TestCase):
    def test_non_interactive_setup_writes_config_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            env_path = Path(tmp) / ".env"

            write_setup(
                config_path=config_path,
                env_path=env_path,
                force=False,
                non_interactive=True,
            )

            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["time"]["user_timezone"], "auto")
            self.assertIn("AnthropicAI", config["polling"]["accounts"])
            self.assertIn("from:AnthropicAI reset", config["polling"]["search_queries"])
            self.assertIn("account_timezones", config["time"])
            self.assertIn("Add secrets here", env_path.read_text(encoding="utf-8"))

    def test_account_setup_adds_defaults_and_syncs_search_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            write_account_setup(config_path=config_path, non_interactive=True)

            config = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertIn("OpenAIDevs", config["polling"]["accounts"])
            self.assertIn("ClaudeDevs", config["polling"]["accounts"])
            self.assertIn("bcherny", config["polling"]["accounts"])
            self.assertEqual(
                config["polling"]["search_queries"],
                [f"from:{handle} reset" for handle in config["polling"]["accounts"]],
            )

    def test_account_add_remove_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            install_default_accounts(config_path)

            add_account_config(config_path, "@example_dev", "Europe/London")
            summary = account_summary(config_path)
            self.assertIn("@example_dev", summary)
            self.assertIn("Europe/London", summary)

            remove_account_config(config_path, "example_dev")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("example_dev", config["polling"]["accounts"])
            self.assertNotIn("from:example_dev reset", config["polling"]["search_queries"])

    def test_account_add_rejects_invalid_source_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"

            with self.assertRaisesRegex(OpsError, "Invalid source timezone"):
                add_account_config(config_path, "example_dev", "Not/AZone")

    def test_notification_setup_guides_telegram_without_editing_json_by_hand(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            env_path = Path(tmp) / ".env"
            write_setup(
                config_path=config_path,
                env_path=env_path,
                force=False,
                non_interactive=True,
            )

            answers = iter(["n", "", "y", "n", "123456", "n", "n", "y"])
            with patch("builtins.input", side_effect=lambda _prompt: next(answers)):
                with patch("getpass.getpass", return_value="telegram-token"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        write_notification_setup(
                            config_path=config_path,
                            env_path=env_path,
                            non_interactive=False,
                        )

            config = json.loads(config_path.read_text(encoding="utf-8"))
            env = env_path.read_text(encoding="utf-8")
            self.assertTrue(config["notifications"]["channels"]["telegram"]["enabled"])
            self.assertIn('CODQ_TELEGRAM_BOT_TOKEN="telegram-token"', env)
            self.assertIn('CODQ_TELEGRAM_CHAT_ID="123456"', env)

    def test_notification_setup_preserves_existing_auth_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            env_path = Path(tmp) / ".env"
            write_setup(
                config_path=config_path,
                env_path=env_path,
                force=False,
                non_interactive=True,
            )
            env_path.write_text(
                'CODQ_X_USERNAME="existing-user"\nCODQ_X_PASSWORD="existing-pass"\n',
                encoding="utf-8",
            )

            write_notification_setup(
                config_path=config_path,
                env_path=env_path,
                non_interactive=True,
            )

            env = env_path.read_text(encoding="utf-8")
            self.assertIn('CODQ_X_USERNAME="existing-user"', env)
            self.assertIn('CODQ_X_PASSWORD="existing-pass"', env)

    def test_extract_telegram_chat_id_from_get_updates_payload(self):
        chat_id = _extract_telegram_chat_id(
            {
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 111}}},
                    {"channel_post": {"chat": {"id": -222}}},
                ],
            }
        )

        self.assertEqual(chat_id, "-222")

    def test_doctor_reports_missing_auth_after_basic_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            config_path = Path(tmp) / "config.json"
            env_path = Path(tmp) / ".env"
            try:
                write_setup(
                    config_path=config_path,
                    env_path=env_path,
                    force=False,
                    non_interactive=True,
                )

                with patch.dict("os.environ", {}, clear=True):
                    checks = doctor_checks(config_path, env_path)
                by_name = {check.name: check for check in checks}

                self.assertTrue(by_name["config"].ok)
                self.assertTrue(by_name["env"].ok)
                self.assertFalse(by_name["x-auth"].ok)
                self.assertIn("data/x_cookies.json", by_name["x-auth"].detail)
            finally:
                os.chdir(previous_cwd)

    def test_doctor_loads_custom_env_path_for_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            config_path = Path(tmp) / "config.json"
            env_path = Path(tmp) / "custom.env"
            try:
                write_setup(
                    config_path=config_path,
                    env_path=env_path,
                    force=False,
                    non_interactive=True,
                )
                env_path.write_text(
                    'CODQ_X_USERNAME="user"\nCODQ_X_PASSWORD="pass"\n',
                    encoding="utf-8",
                )

                with patch.dict("os.environ", {}, clear=True):
                    checks = doctor_checks(config_path, env_path)
                by_name = {check.name: check for check in checks}

                self.assertTrue(by_name["x-auth"].ok)
            finally:
                os.chdir(previous_cwd)

    def test_user_service_unit_runs_installed_package_module(self):
        unit = _unit_text(
            project_dir=Path("/tmp/codex-reset-tracker"),
            config_path=Path("config.json"),
        )

        self.assertIn("WorkingDirectory=/tmp/codex-reset-tracker", unit)
        self.assertIn(".venv/bin/python -m codex_reset_tracker run", unit)
        self.assertIn("Restart=on-failure", unit)

    def test_windows_startup_task_wakes_wsl_and_starts_service(self):
        xml = _windows_startup_task_xml(
            distro="Ubuntu",
            linux_user="tuanpham",
            project_dir=Path("/home/tuanpham/selfstudy-linux/codex-reset-tracker"),
            config_path=Path("config.json"),
        )

        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("<SessionStateChangeTrigger>", xml)
        self.assertIn("Microsoft-Windows-Power-Troubleshooter", xml)
        self.assertIn("C:\\Windows\\System32\\wsl.exe", xml)
        self.assertIn("-d Ubuntu", xml)
        self.assertIn("-u tuanpham", xml)
        self.assertIn("codex_reset_tracker service start", xml)

    def test_service_prereqs_require_uv_synced_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            config_path = project_dir / "config.json"
            env_path = project_dir / ".env"
            write_setup(
                config_path=config_path,
                env_path=env_path,
                force=False,
                non_interactive=True,
            )

            with patch("shutil.which", return_value="/usr/bin/systemctl"):
                with self.assertRaisesRegex(OpsError, "uv sync"):
                    _validate_service_prereqs(
                        project_dir=project_dir,
                        config_path=config_path,
                    )


if __name__ == "__main__":
    unittest.main()
