"""`classify` tests: plain lists in, counts out. No network."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jev_mode import JevClient, JevError  # noqa: E402
from jev_mode.classify import classify, load_text_items, parse_options  # noqa: E402


def transport_for(mapping, confidence=0.9):
    def transport(url, body, key, timeout):
        state = body["state"]
        choice = next((v for k, v in mapping.items() if k in state), "no")
        return {"answers": {"answer": {"type": "choice", "choice": choice,
                                       "confidence": confidence}},
                "usage": {"input_tokens": 10, "output_tokens": 2}}
    return transport


class OptionTests(unittest.TestCase):
    def test_parses_pairs(self):
        self.assertEqual(parse_options("yes=keep it,no=drop it"),
                         {"yes": "keep it", "no": "drop it"})

    def test_bare_name_becomes_its_own_description(self):
        self.assertEqual(parse_options("yes,no"), {"yes": "yes", "no": "no"})

    def test_rejects_one_option_and_malformed_pairs(self):
        with self.assertRaises(JevError):
            parse_options("yes=only")
        # A bare name is shorthand, not an error; but an empty name is.
        self.assertEqual(parse_options("yes=ok,broken"),
                         {"yes": "ok", "broken": "broken"})
        with self.assertRaises(JevError):
            parse_options("=no name,other=fine")


class ItemLoadingTests(unittest.TestCase):
    def test_plain_text_lines_become_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.txt"
            path.write_text("first thing\n\nsecond thing\n")
            items = load_text_items(str(path))
            self.assertEqual([i["text"] for i in items], ["first thing", "second thing"])
            self.assertEqual([i["id"] for i in items], ["row1", "row2"])

    def test_jsonl_keeps_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            path.write_text('{"id":"A","text":"alpha"}\n{"id":"B","text":"beta"}\n')
            items = load_text_items(str(path))
            self.assertEqual([i["id"] for i in items], ["A", "B"])

    def test_glob_treats_each_file_as_one_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.log", "b.log"):
                (Path(tmp) / name).write_text(f"contents of {name}\n")
            items = load_text_items(str(Path(tmp) / "*.log"))
            self.assertEqual(sorted(i["id"] for i in items), ["a.log", "b.log"])
            self.assertIn("contents of a.log", items[0]["text"])

    def test_missing_input_is_reported(self):
        with self.assertRaises(JevError):
            load_text_items("/nonexistent/path.txt")

    def test_jsonl_without_text_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            path.write_text('{"id":"A"}\n')
            with self.assertRaises(JevError):
                load_text_items(str(path))


class ClassifyTests(unittest.TestCase):
    def test_counts_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.txt"
            path.write_text("keep this file\n"
                            "drop that file\n"
                            "keep this too\n"
                            "another keep\n"
                            "and one more keep\n")
            out = Path(tmp) / "answers.jsonl"
            client = JevClient(api_key="k", transport=transport_for({"keep": "keep", "drop": "drop"}))
            summary = classify(str(path), "Keep or drop?", "keep=still needed,drop=stale",
                               out, client=client, pool=3)
            self.assertEqual(summary["items"], 5)
            self.assertEqual(summary["ok"], 5)
            self.assertEqual(summary["counts"], {"keep": 4, "drop": 1})
            self.assertEqual(summary["low_confidence"], 0)

    def test_low_confidence_is_counted_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.txt"
            path.write_text("keep a\nkeep b\nkeep c\nkeep d\nkeep e\n")
            out = Path(tmp) / "answers.jsonl"
            client = JevClient(api_key="k",
                               transport=transport_for({"keep": "keep"}, confidence=0.4))
            summary = classify(str(path), "Keep or drop?", "keep=needed,drop=stale",
                               out, client=client, pool=2)
            self.assertEqual(summary["low_confidence"], 5)

    def test_bad_options_fail_before_any_call(self):
        calls = {"n": 0}

        def transport(url, body, key, timeout):
            calls["n"] += 1
            return {"answers": {}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.txt"
            path.write_text("a\nb\nc\nd\ne\n")
            with self.assertRaises(JevError):
                classify(str(path), "q", "single=only", Path(tmp) / "o.jsonl",
                         client=JevClient(api_key="k", transport=transport))
            self.assertEqual(calls["n"], 0)


if __name__ == "__main__":
    unittest.main()
