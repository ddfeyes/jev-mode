"""Coverage counting: root vs subagent attribution must never be merged."""

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jev_mode import coverage


def write_rollout(path, session_id, parent=None, tool_calls=0, jev_calls=0):
    """Write a minimal rollout with the records the counter reads."""

    payload = {"id": session_id}
    if parent:
        payload["parent_thread_id"] = parent
    lines = [json.dumps({"type": "session_meta", "payload": payload})]
    for index in range(tool_calls):
        arguments = "codex-typesafe ask --state-file s" if index < jev_calls else "ls -1"
        lines.append(json.dumps({"type": "response_item", "payload": {"item": {
            "type": "function_call", "name": "exec_command", "arguments": arguments}}}))
    Path(path).write_text("\n".join(lines) + "\n")


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions" / "2026" / "09" / "18"
        self.root.mkdir(parents=True)
        self.trace = Path(self.tmp.name) / "hook-trace.jsonl"
        self.db = Path(self.tmp.name) / "tasks.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def write_trace(self, entries):
        self.trace.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    def write_db(self, rows):
        db = sqlite3.connect(str(self.db))
        db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, task TEXT, at TEXT, kind TEXT, detail TEXT)")
        for task, detail in rows:
            db.execute("INSERT INTO events(task,at,kind,detail) VALUES (?,?,?,?)",
                       (task, "2026-09-18T00:00:00+00:00", "JevGate", detail))
        db.commit()
        db.close()

    def survey(self, task=None):
        return coverage.survey(task, sessions_root=self.root.parent.parent.parent,
                               trace=self.trace, events_db=self.db)

    def test_children_are_counted_separately_from_the_root(self):
        write_rollout(self.root / "root.jsonl", "root-1", tool_calls=10, jev_calls=4)
        write_rollout(self.root / "child.jsonl", "child-1", parent="root-1",
                      tool_calls=8, jev_calls=0)
        rows = self.survey("root-1")
        roles = {row["session"]: row["role"] for row in rows}
        self.assertEqual(roles, {"root-1": "root", "child-1": "child"})
        totals = coverage.summarise(rows)
        self.assertEqual(totals["root"]["steps"], 10)
        self.assertEqual(totals["root"]["jev_calls"], 4)
        self.assertEqual(totals["child"]["steps"], 8)
        self.assertEqual(totals["child"]["jev_calls"], 0)
        self.assertEqual(totals["all"]["steps"], 18)

    def test_task_filter_keeps_only_that_root_and_its_children(self):
        write_rollout(self.root / "a.jsonl", "root-a", tool_calls=3)
        write_rollout(self.root / "b.jsonl", "root-b", tool_calls=5)
        write_rollout(self.root / "c.jsonl", "child-a", parent="root-a", tool_calls=2)
        rows = self.survey("root-a")
        self.assertEqual(sorted(row["session"] for row in rows), ["child-a", "root-a"])

    def test_hook_events_come_from_the_trace_not_the_capped_table(self):
        write_rollout(self.root / "root.jsonl", "root-1", tool_calls=6, jev_calls=1)
        self.write_trace([
            {"event": "PreToolUse", "session_id": "root-1", "tool": "Bash"},
            {"event": "PostToolUse", "session_id": "root-1", "tool": "Bash"},
            {"event": "UserPromptSubmit", "session_id": "root-1", "tool": ""},
        ])
        rows = self.survey("root-1")
        self.assertEqual(rows[0]["hook_events"], 2)

    def test_gate_denials_are_reported_from_the_events_table(self):
        write_rollout(self.root / "root.jsonl", "root-1", tool_calls=4)
        self.write_db([("root-1", "off_goal conf=0.91 allow=False"),
                       ("root-1", "advances_goal conf=0.8 allow=True")])
        rows = self.survey("root-1")
        self.assertEqual(rows[0]["gate_rows_kept"], 2)
        self.assertEqual(rows[0]["gate_denials"], 1)

    def test_missing_trace_and_database_do_not_raise(self):
        write_rollout(self.root / "root.jsonl", "root-1", tool_calls=2, jev_calls=1)
        rows = self.survey("root-1")
        self.assertEqual(rows[0]["hook_events"], 0)
        self.assertEqual(rows[0]["gate_rows_kept"], 0)
        self.assertEqual(rows[0]["jev_ratio"], 0.5)

    def test_render_keeps_root_child_and_all_lines(self):
        write_rollout(self.root / "root.jsonl", "root-1", tool_calls=4, jev_calls=2)
        write_rollout(self.root / "child.jsonl", "child-1", parent="root-1", tool_calls=4)
        text = coverage.render(self.survey("root-1"))
        self.assertIn("root:", text)
        self.assertIn("child:", text)
        self.assertIn("all:", text)

    def test_day_filter_selects_the_requested_day(self):
        write_rollout(self.root / "root.jsonl", "root-1", tool_calls=3)
        rows = coverage.survey(day="20260918", sessions_root=self.root.parent.parent.parent,
                               trace=self.trace, events_db=self.db)
        self.assertEqual([row["session"] for row in rows], ["root-1"])
        rows = coverage.survey(day="20260919", sessions_root=self.root.parent.parent.parent,
                               trace=self.trace, events_db=self.db)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
