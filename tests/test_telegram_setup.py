import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_reset_tracker import telegram_setup
from codex_reset_tracker.ops import OpsError


def message(chat_type, chat_id):
    return {"message": {"chat": {"type": chat_type, "id": chat_id}}}


class TelegramSetupTests(unittest.TestCase):
    def test_chat_id_is_the_newest_private_chat_and_groups_are_ignored(self):
        updates = [message("private", 1), message("private", 2), message("group", -9)]

        self.assertEqual(telegram_setup.find_chat_id(updates), "2")
        self.assertIsNone(telegram_setup.find_chat_id([message("group", -9), {"edited_message": {}}]))
        self.assertIsNone(telegram_setup.find_chat_id([]))

    def test_upsert_env_replaces_its_keys_keeps_other_lines_and_locks_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("CODQ_X_USERNAME=me\nCODQ_TELEGRAM_CHAT_ID=old\n", encoding="utf-8")

            telegram_setup.upsert_env(env, {"CODQ_TELEGRAM_BOT_TOKEN": "tok", "CODQ_TELEGRAM_CHAT_ID": "5"})

            lines = env.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "CODQ_X_USERNAME=me")
            self.assertEqual(sorted(lines[1:]), ["CODQ_TELEGRAM_BOT_TOKEN=tok", "CODQ_TELEGRAM_CHAT_ID=5"])
            self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)

    def run_setup(self, tmp, answers):
        said, asked = [], []
        config = Path(tmp) / "config.json"
        config.write_text(json.dumps({"notifications": {"channels": {"telegram": {"enabled": False}}}}))
        calls = iter(answers)
        # a real token in the environment must not leak into the test
        with patch.dict("os.environ", {}, clear=True), patch.object(
            telegram_setup, "call", lambda token, method, **p: next(calls)(method, p)
        ):
            telegram_setup.setup(
                config,
                Path(tmp) / ".env",
                ask=lambda prompt: asked.append(prompt) or " 123:SECRET ",
                say=said.append,
            )
        return config, said, asked

    def test_setup_reads_the_chat_id_from_telegram_and_never_prints_the_token(self):
        sent = []
        answers = [
            lambda method, p: {"username": "my_bot"},  # getMe
            lambda method, p: [],  # getUpdates: nothing yet
            lambda method, p: [message("private", 77)],  # getUpdates: the user wrote to the bot
            lambda method, p: sent.append(p) or {},  # sendMessage
        ]
        with tempfile.TemporaryDirectory() as tmp:
            config, said, asked = self.run_setup(tmp, answers)

            env = (Path(tmp) / ".env").read_text(encoding="utf-8")
            self.assertIn("CODQ_TELEGRAM_BOT_TOKEN=123:SECRET", env)
            self.assertIn("CODQ_TELEGRAM_CHAT_ID=77", env)
            self.assertTrue(json.loads(config.read_text())["notifications"]["channels"]["telegram"]["enabled"])

        self.assertEqual(len(asked), 1)  # one hidden prompt, for the token
        self.assertEqual(sent[0]["chat_id"], "77")
        self.assertNotIn("SECRET", "\n".join(said))
        self.assertIn("@my_bot", "\n".join(said))

    def test_setup_tells_the_user_what_to_do_when_no_message_arrives(self):
        answers = [lambda method, p: {"username": "my_bot"}]
        with tempfile.TemporaryDirectory() as tmp, patch.object(telegram_setup, "WAIT_S", 0):
            with self.assertRaisesRegex(OpsError, "send it one and run this command again"):
                self.run_setup(tmp, answers)
            self.assertFalse((Path(tmp) / ".env").exists())  # nothing half-written


if __name__ == "__main__":
    unittest.main()
