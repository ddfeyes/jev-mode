"""Hook switch tests and CLI tests run as a subprocess, the way a user runs it."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jev_mode import hook  # noqa: E402


class HookTests(unittest.TestCase):
    def test_off_by_default_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"JEV_MODE_CONFIG": str(Path(tmp) / "absent.json")}
            self.assertFalse(hook.enabled(env))
            self.assertEqual(hook.directive(env), "")

    def test_explicit_environment_switch_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"enabled": False}))
            env = {"JEV_MODE_CONFIG": str(config), "JEV_MODE": "1"}
            self.assertTrue(hook.enabled(env))
            self.assertIn("JEV MODE", hook.directive(env))
            env["JEV_MODE"] = "0"
            self.assertFalse(hook.enabled(env))

    def test_config_file_enables_and_can_override_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            env = {"JEV_MODE_CONFIG": str(config)}
            config.write_text(json.dumps({"enabled": True, "directive": "CUSTOM"}))
            self.assertEqual(hook.directive(env), "CUSTOM")
            config.write_text(json.dumps({"enabled": True}))
            self.assertIn("JEV MODE", hook.directive(env))

    def test_broken_config_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text("{not json")
            self.assertFalse(hook.enabled({"JEV_MODE_CONFIG": str(config)}))

    def test_directive_stays_short_and_names_non_triggers(self):
        # Raised from 1400 when the directive changed from advice to an
        # imperative with a one-command fast path. The bound still exists so the
        # block cannot quietly grow into a long document.
        self.assertLess(len(hook.DIRECTIVE), 2200)
        self.assertIn("Do NOT use Jev for prose", hook.DIRECTIVE)
        self.assertIn("MANDATORY", hook.DIRECTIVE)


class CliTests(unittest.TestCase):
    def _run(self, args, env=None):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        environment.pop("TYPESAFE_API_KEY", None)
        environment.update(env or {})
        return subprocess.run([sys.executable, "-m", "jev_mode.cli"] + args,
                              capture_output=True, text=True, env=environment, timeout=120)

    def test_check_offline_reports_missing_key_without_crashing(self):
        result = self._run(["check", "--offline"], env={"TYPESAFE_ENV_FILE": "/nonexistent"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("no API key", result.stderr)

    def test_check_offline_with_key_present_never_echoes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            keyfile = Path(tmp) / "typesafe.env"
            keyfile.write_text("TYPESAFE_API_KEY=dummy-not-real\n")
            result = self._run(["check", "--offline"], env={"TYPESAFE_ENV_FILE": str(keyfile)})
            self.assertEqual(result.returncode, 0)
            self.assertTrue(json.loads(result.stdout)["api_key_present"])
            self.assertNotIn("dummy-not-real", result.stdout)

    def test_hook_prints_nothing_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(["hook"], env={"JEV_MODE_CONFIG": str(Path(tmp) / "absent.json")})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")

    def test_hook_emits_a_hook_payload_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"enabled": True}))
            result = self._run(["hook"], env={"JEV_MODE_CONFIG": str(config)})
            payload = json.loads(result.stdout)
            self.assertIn("JEV MODE", payload["hookSpecificOutput"]["additionalContext"])

    def test_batch_rejects_bad_questions_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = Path(tmp) / "items.jsonl"
            items.write_text('{"id":"A","text":"hello"}\n')
            questions = Path(tmp) / "q.json"
            questions.write_text(json.dumps({"q": {"type": "choice", "instructions": "x"}}))
            result = self._run(["batch", "--items", str(items), "--questions", str(questions),
                                "--out", str(Path(tmp) / "o.jsonl")],
                               env={"TYPESAFE_ENV_FILE": "/nonexistent"})
            self.assertEqual(result.returncode, 2)
            self.assertIn("criteria", result.stderr)

    def test_usage_error_exits_nonzero(self):
        self.assertNotEqual(self._run(["batch", "--items", "x"]).returncode, 0)

    def test_version_flag(self):
        self.assertIn("jev-mode", self._run(["--version"]).stdout)


if __name__ == "__main__":
    unittest.main()
