import json
import tempfile
import unittest
from pathlib import Path

from codex_reset_tracker.ops import _unit_text, write_setup


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

    def test_user_service_unit_runs_installed_package_module(self):
        unit = _unit_text(
            project_dir=Path("/tmp/codex-reset-tracker"),
            config_path=Path("config.json"),
        )

        self.assertIn("WorkingDirectory=/tmp/codex-reset-tracker", unit)
        self.assertIn(".venv/bin/python -m codex_reset_tracker run", unit)
        self.assertIn("Restart=on-failure", unit)


if __name__ == "__main__":
    unittest.main()
