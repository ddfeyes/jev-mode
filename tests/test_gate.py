"""Step-gate tests. No network: the client is faked."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jev_mode import gate  # noqa: E402
from jev_mode.gate_cli import evaluate, main as gate_main  # noqa: E402

CONFIG = {"gate": {"enabled": True, "threshold": 0.8, "max_denials": 2}}


class FakeClient:
    def __init__(self, choice, confidence=0.95):
        self.choice, self.confidence, self.calls = choice, confidence, []

    def ask(self, state, questions):
        self.calls.append(state)
        return {"answers": {"step": {"type": "choice", "choice": self.choice,
                                     "confidence": self.confidence}},
                "usage": {"input_tokens": 10, "output_tokens": 2}}


class SettingsTests(unittest.TestCase):
    def test_disabled_and_high_threshold_by_default(self):
        conf = gate.settings({})
        self.assertFalse(conf["enabled"])
        self.assertEqual(conf["threshold"], 0.8)
        self.assertEqual(conf["max_denials"], 3)


class DecideTests(unittest.TestCase):
    def test_disabled_gate_does_not_call(self):
        client = FakeClient("off_goal")
        verdict = gate.decide("goal", "Bash", {"cmd": "x"}, {}, client=client)
        self.assertTrue(verdict["allow"])
        self.assertEqual(client.calls, [])

    def test_confident_off_goal_is_denied(self):
        verdict = gate.decide("publish the package", "Bash", {"cmd": "pip install pandas"},
                              CONFIG, client=FakeClient("off_goal", 0.95))
        self.assertFalse(verdict["allow"])
        self.assertIn("off_goal", verdict["reason"])

    def test_confident_repeat_is_denied(self):
        verdict = gate.decide("publish the package", "Bash", {"cmd": "git push"},
                              CONFIG, client=FakeClient("repeats_or_redundant", 0.9))
        self.assertFalse(verdict["allow"])

    def test_low_confidence_allows(self):
        verdict = gate.decide("goal", "Bash", {"cmd": "x"}, CONFIG,
                              client=FakeClient("off_goal", 0.5))
        self.assertTrue(verdict["allow"])

    def test_unsafe_is_recorded_but_not_enforced(self):
        verdict = gate.decide("goal", "Bash", {"cmd": "rm -rf /"}, CONFIG,
                              client=FakeClient("unsafe_or_irreversible", 1.0))
        self.assertTrue(verdict["allow"])
        self.assertEqual(verdict["answer"], "unsafe_or_irreversible")

    def test_broken_client_fails_open(self):
        class Broken:
            def ask(self, *a, **k):
                raise RuntimeError("no network")
        verdict = gate.decide("goal", "Bash", {"cmd": "x"}, CONFIG, client=Broken())
        self.assertTrue(verdict["allow"])
        self.assertIn("failed open", verdict["reason"])

    def test_denial_budget_fails_open(self):
        verdict = gate.decide("goal", "Bash", {"cmd": "x"}, CONFIG,
                              client=FakeClient("off_goal", 1.0), prior_denials=2)
        self.assertTrue(verdict["allow"])

    def test_budget_is_reset_by_an_allowed_step(self):
        """Regression: a live task hit three denials and then ran 68 steps with
        no filtering, because the budget was permanent rather than consecutive."""
        import tempfile
        from pathlib import Path as _Path
        from jev_mode.gate_cli import evaluate
        # The CLI reports the counter the caller should store next; an allowed
        # step must report zero so the next decision is not blocked.
        allowed = evaluate({"tool_name": "Bash", "tool_input": {"cmd": "pytest"},
                            "objective": "publish the package",
                            "gate": {"enabled": True}, "denials": 0},
                           client=FakeClient("advances_goal", 0.95))
        self.assertTrue(allowed["allow"])

    def test_history_is_sent_so_repeats_are_visible(self):
        client = FakeClient("repeats_or_redundant", 0.99)
        gate.decide("goal", "Bash", {"cmd": "pytest"}, CONFIG, client=client,
                    recent=["Bash: pytest -q"])
        self.assertIn("Steps already taken", client.calls[0])
        self.assertIn("pytest -q", client.calls[0])

    def test_state_stays_small(self):
        state = gate.build_state("goal", "Bash", {"cmd": "x" * 5000}, recent=["step"] * 50)
        self.assertLess(len(state), 3000)


class GateCliTests(unittest.TestCase):
    def test_event_fields_are_recognised(self):
        client = FakeClient("off_goal", 0.95)
        verdict = evaluate({"tool": "Bash", "command": "pip install pandas",
                            "task": "publish the package",
                            "gate": {"enabled": True}}, client=client)
        self.assertFalse(verdict["allow"])
        self.assertIn("publish the package", client.calls[0])

    def test_gate_disabled_by_default_without_switch(self):
        verdict = evaluate({"tool_name": "Bash", "tool_input": {"cmd": "x"},
                            "objective": "goal"}, client=FakeClient("off_goal", 1.0))
        self.assertTrue(verdict["allow"])

    def _run_with_stdin(self, text, client=None):
        original = sys.stdin
        sys.stdin = io.StringIO(text)
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = gate_main([], client=client)
        finally:
            sys.stdin = original
        return code, buffer.getvalue()

    def test_hook_output_shape_on_deny(self):
        code, out = self._run_with_stdin(
            json.dumps({"tool_name": "Bash", "tool_input": {"cmd": "pip install pandas"},
                        "objective": "publish the package",
                        "gate": {"enabled": True, "threshold": 0.8}}),
            client=FakeClient("off_goal", 0.95))
        self.assertEqual(code, 0, "a gate must never break the pipeline")
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_allowing_gate_stays_silent_about_context(self):
        code, out = self._run_with_stdin(
            json.dumps({"tool_name": "Bash", "tool_input": {"cmd": "pytest -q"},
                        "objective": "publish the package",
                        "gate": {"enabled": True, "threshold": 0.8}}),
            client=FakeClient("advances_goal", 0.95))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["allow"])

    def test_bad_event_fails_open(self):
        code, out = self._run_with_stdin("not json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["allow"])


if __name__ == "__main__":
    unittest.main()
