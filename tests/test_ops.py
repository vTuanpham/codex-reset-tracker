import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_reset_tracker.ops import (
    _validate_service_prereqs,
    doctor_checks,
    OpsError,
    _unit_text,
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
            self.assertEqual(config["time"]["user_timezone"], "Asia/Saigon")
            self.assertIn("account_timezones", config["time"])
            self.assertIn("Add secrets here", env_path.read_text(encoding="utf-8"))

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
