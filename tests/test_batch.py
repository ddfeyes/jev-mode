"""Batch runner tests. No network: every transport call is faked."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jev_mode import JevClient, JevError, run_batch  # noqa: E402
from jev_mode.batch import read_items  # noqa: E402

QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which team owns this?",
        "criteria": {"billing": "money", "technical": "the product fails"},
    }
}


class BatchTests(unittest.TestCase):
    def _items(self, directory, rows):
        path = Path(directory) / "items.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return path

    def test_one_call_per_item_and_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"id": f"T{i}", "text": f"message {i}"} for i in range(7)]
            items = self._items(tmp, rows)
            seen = []

            def transport(url, body, key, timeout):
                seen.append(body["state"])
                return {"answers": {"team": {"type": "choice", "choice": "billing",
                                             "confidence": 0.8}},
                        "usage": {"input_tokens": 100, "output_tokens": 5}}
            out = Path(tmp) / "answers.jsonl"
            summary = run_batch(items, QUESTIONS, out,
                                client=JevClient(api_key="k", transport=transport), pool=4)

            self.assertEqual(summary["items"], 7)
            self.assertEqual(summary["ok"], 7)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["input_tokens"], 700)
            self.assertEqual(summary["mean_confidence"], {"team": 0.8})
            # One record per call: never the whole batch as one state.
            self.assertEqual(sorted(seen), sorted(r["text"] for r in rows))
            self.assertEqual(len(out.read_text().strip().splitlines()), 7)

    def test_accepts_an_iterable_as_well_as_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "answers.jsonl"
            summary = run_batch([{"id": "A", "text": "one"}], QUESTIONS, out,
                                client=JevClient(api_key="k", transport=lambda *a: {
                                    "answers": {}, "usage": {"input_tokens": 3}}))
            self.assertEqual(summary["ok"], 1)

    def test_custom_field_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = self._items(tmp, [{"key": "A", "body": "text a"},
                                      {"key": "B", "body": "text b"}])
            out = Path(tmp) / "answers.jsonl"
            summary = run_batch(items, QUESTIONS, out,
                                client=JevClient(api_key="k", transport=lambda *a: {
                                    "answers": {}, "usage": {}}),
                                state_field="body", id_field="key")
            self.assertEqual(summary["ok"], 2)
            ids = {json.loads(line)["id"] for line in out.read_text().splitlines()}
            self.assertEqual(ids, {"A", "B"})

    def test_failure_is_isolated_to_one_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = self._items(tmp, [{"id": "A", "text": "a"}, {"id": "B", "text": "b"}])
            out = Path(tmp) / "answers.jsonl"

            def transport(url, body, key, timeout):
                if body["state"] == "a":
                    raise JevError("boom")
                return {"answers": {}, "usage": {"input_tokens": 5, "output_tokens": 0}}
            summary = run_batch(items, QUESTIONS, out,
                                client=JevClient(api_key="k", transport=transport),
                                pool=2, retries=0)
            self.assertEqual((summary["ok"], summary["failed"]), (1, 1))
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertIn("error", next(r for r in rows if r["id"] == "A"))

    def test_retries_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = self._items(tmp, [{"id": "A", "text": "a"}])
            attempts = {"n": 0}

            def transport(url, body, key, timeout):
                attempts["n"] += 1
                raise JevError("flaky")
            summary = run_batch(items, QUESTIONS, Path(tmp) / "o.jsonl",
                                client=JevClient(api_key="k", transport=transport), retries=2)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(attempts["n"], 3)

    def test_oversized_item_refused_before_calling(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = self._items(tmp, [{"id": "big", "text": "x" * 300_000}])
            calls = {"n": 0}

            def transport(url, body, key, timeout):
                calls["n"] += 1
                return {"answers": {}}
            with self.assertRaises(JevError):
                run_batch(items, QUESTIONS, Path(tmp) / "o.jsonl",
                          client=JevClient(api_key="k", transport=transport))
            self.assertEqual(calls["n"], 0)

    def test_read_items_reports_problems_precisely(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(JevError):
                read_items(self._items(tmp, [{"id": "A"}]))
            empty = Path(tmp) / "empty.jsonl"
            empty.write_text("")
            with self.assertRaises(JevError):
                read_items(empty)
            bad = Path(tmp) / "bad.jsonl"
            bad.write_text('{"id":"A","text":"ok"}\nnot json\n')
            with self.assertRaises(JevError) as ctx:
                read_items(bad)
            self.assertIn("line 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
