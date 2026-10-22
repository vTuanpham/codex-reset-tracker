import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_reset_tracker.ops import (
    OpsError,
    _unit_text,
    _validate_service_prereqs,
    _windows_startup_task_xml,
    doctor_checks,
)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.json"


def write_starter_files(config_path: Path, env_path: Path) -> None:
    shutil.copyfile(EXAMPLE_CONFIG, config_path)
    env_path.write_text("", encoding="utf-8")


class OpsTests(unittest.TestCase):
    def test_doctor_reports_missing_auth_with_the_starter_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            config_path = Path(tmp) / "config.json"
            env_path = Path(tmp) / ".env"
            try:
                write_starter_files(config_path, env_path)

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
                write_starter_files(config_path, env_path)
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
        self.assertIn("<Command>C:\\Windows\\System32\\conhost.exe</Command>", xml)  # no console window
        self.assertIn("--headless C:\\Windows\\System32\\wsl.exe -d Ubuntu", xml)
        self.assertIn("-u tuanpham", xml)
        self.assertIn("codex_reset_tracker service start", xml)

    def test_service_prereqs_require_uv_synced_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            config_path = project_dir / "config.json"
            write_starter_files(config_path, project_dir / ".env")

            with patch("shutil.which", return_value="/usr/bin/systemctl"):
                with self.assertRaisesRegex(OpsError, "uv sync"):
                    _validate_service_prereqs(
                        project_dir=project_dir,
                        config_path=config_path,
                    )


if __name__ == "__main__":
    unittest.main()
