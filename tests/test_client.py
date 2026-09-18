"""Client and validation tests. No network: every transport call is faked."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jev_mode import JevClient, JevError, validate_questions  # noqa: E402
from jev_mode.client import load_api_key  # noqa: E402

QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which team owns this?",
        "criteria": {"billing": "money", "technical": "the product fails"},
    }
}


def ok_transport(*, choice="billing", confidence=0.9, tokens=100):
    def transport(url, body, key, timeout):
        return {"answers": {"team": {"type": "choice", "choice": choice,
                                     "confidence": confidence}},
                "usage": {"input_tokens": tokens, "output_tokens": 10},
                "model": body.get("model")}
    return transport


class ValidationTests(unittest.TestCase):
    def test_accepts_a_valid_map(self):
        self.assertIs(validate_questions(QUESTIONS), QUESTIONS)

    def test_rejects_empty_and_non_dict(self):
        for bad in ({}, None, [], "x"):
            with self.assertRaises(JevError):
                validate_questions(bad)

    def test_choice_needs_two_options(self):
        with self.assertRaises(JevError):
            validate_questions({"q": {"type": "choice", "instructions": "x",
                                      "criteria": {"only": "one"}}})

    def test_noul_criteria_limited_to_true_false(self):
        with self.assertRaises(JevError):
            validate_questions({"q": {"type": "noul", "instructions": "x",
                                      "criteria": {"maybe": "n"}}})
        validate_questions({"q": {"type": "noul", "instructions": "x"}})

    def test_unknown_type_and_fields_rejected(self):
        with self.assertRaises(JevError):
            validate_questions({"q": {"type": "text", "instructions": "x"}})
        with self.assertRaises(JevError):
            validate_questions({"q": {"type": "noul", "instructions": "x", "extra": 1}})

    def test_score_needs_two_levels(self):
        with self.assertRaises(JevError):
            validate_questions({"q": {"type": "score", "instructions": "x", "criteria": ["one"]}})


class ClientTests(unittest.TestCase):
    def test_ask_returns_answers(self):
        client = JevClient(api_key="k", transport=ok_transport(choice="technical"))
        self.assertEqual(client.ask("the app crashes", QUESTIONS)["answers"]["team"]["choice"],
                         "technical")

    def test_empty_state_rejected(self):
        with self.assertRaises(JevError):
            JevClient(api_key="k", transport=ok_transport()).ask("   ", QUESTIONS)

    def test_oversized_request_fails_locally(self):
        calls = {"n": 0}

        def transport(url, body, key, timeout):
            calls["n"] += 1
            return {"answers": {}}
        with self.assertRaises(JevError):
            JevClient(api_key="k", transport=transport).ask("x" * 300_000, QUESTIONS)
        self.assertEqual(calls["n"], 0, "no request may be spent on an oversized state")

    def test_missing_answers_rejected(self):
        with self.assertRaises(JevError):
            JevClient(api_key="k", transport=lambda *a: {"nope": 1}).ask("text", QUESTIONS)

    def test_key_resolution_order(self):
        self.assertEqual(load_api_key(explicit="explicit"), "explicit")
        self.assertEqual(load_api_key(env={"TYPESAFE_API_KEY": "abc"}), "abc")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "typesafe.env"
            path.write_text("# comment\nexport TYPESAFE_API_KEY=secret-value\n")
            self.assertEqual(load_api_key(env={}, path=path), "secret-value")

    def test_missing_key_message_never_leaks_a_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(JevError) as ctx:
                load_api_key(env={}, path=Path(tmp) / "absent")
            self.assertIn("no API key", str(ctx.exception))

    def test_endpoint_and_model_are_configurable(self):
        seen = {}

        def transport(url, body, key, timeout):
            seen["url"], seen["model"] = url, body["model"]
            return {"answers": {}}
        JevClient(api_key="k", endpoint="http://localhost:9/x", model="jev-preview",
                  transport=transport).ask("text", QUESTIONS)
        self.assertEqual(seen["url"], "http://localhost:9/x")
        self.assertEqual(seen["model"], "jev-preview")


if __name__ == "__main__":
    unittest.main()
