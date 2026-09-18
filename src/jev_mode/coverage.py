"""Measure how much of an agent session's work actually went through Jev.

A claim like "every step is filtered through Jev" is only worth anything if it
can be counted. This module counts it from the two records an agent host already
leaves behind:

* **Rollouts** - the host writes one JSONL file per session containing every
  tool call and every `codex-typesafe` invocation. This is the only per-agent
  record of direct Jev use, and it is what makes subagent coverage measurable.
* **Hook trace** - an append-only file with one line per hook invocation and the
  session it belonged to. A hook event table that keeps only the newest rows per
  task cannot answer coverage questions for a long session, because the early
  rows are already deleted.

Root sessions and subagents are reported separately. Measured on Codex
2026-09-18: session hooks do not run inside subagent sessions at all, so a
spawned child is never gated by the host, and the parent cannot see its steps.
One blended number would hide that, so it is not produced.

Read-only: nothing here writes to the session store.

    jev-mode coverage --task <session-id>
    jev-mode coverage --day 20260918
"""

from __future__ import annotations

import glob
import json
import sqlite3
from collections import Counter
from pathlib import Path

TOOL_CALL_TYPES = ("function_call", "custom_tool_call", "local_shell_call", "shell_call")
JEV_NEEDLE = "codex-typesafe"


def default_sessions_root(home=None):
    return Path(home or Path.home()) / ".codex" / "sessions"


def default_trace(home=None):
    return Path(home or Path.home()) / ".local" / "state" / "codex-harness" / "hook-trace.jsonl"


def default_events_db(home=None):
    return Path(home or Path.home()) / ".local" / "state" / "codex-harness" / "tasks.sqlite3"


def rollout_files(root, day=None):
    """Every rollout under a sessions root, or just one day (YYYYMMDD)."""

    pattern = "**/*.jsonl" if not day else f"{day[:4]}/{day[4:6]}/{day[6:]}/*.jsonl"
    return sorted(glob.glob(str(Path(root) / pattern), recursive=True))


def session_meta(path):
    """Return (session_id, parent_id) from a rollout's opening records."""

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index > 4:
                    break
                if '"session_meta"' not in line:
                    continue
                try:
                    payload = json.loads(line).get("payload") or {}
                except ValueError:
                    continue
                return payload.get("id"), payload.get("parent_thread_id")
    except OSError:
        pass
    return None, None


def count_session(path):
    """Count tool calls by name plus direct Jev invocations in one rollout."""

    calls = Counter()
    jev = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"response_item"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("type") != "response_item":
                    continue
                payload = record.get("payload") or {}
                item = payload.get("item") or payload
                if item.get("type") not in TOOL_CALL_TYPES:
                    continue
                name = str(item.get("name") or item.get("tool_name") or item.get("type") or "")
                arguments = str(item.get("arguments") or item.get("input") or item.get("action") or "")
                calls[name] += 1
                if JEV_NEEDLE in arguments or JEV_NEEDLE in name:
                    jev += 1
    except OSError:
        pass
    return calls, jev


def trace_counts(trace_path):
    """Hook invocations per session id from the append-only trace."""

    counts = Counter()
    try:
        with open(trace_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("event") in ("PreToolUse", "PostToolUse"):
                    counts[record.get("session_id") or ""] += 1
    except OSError:
        pass
    return counts


def gate_counts(db_path):
    """Retained gate rows per session. A floor, never a total: rows are capped."""

    counts = {}
    try:
        with sqlite3.connect(str(db_path)) as db:
            for task, kept, denied in db.execute(
                    "SELECT task, COUNT(*), SUM(detail LIKE '%allow=False%') "
                    "FROM events WHERE kind='JevGate' GROUP BY task"):
                counts[task] = {"kept": kept, "denied": denied or 0}
    except sqlite3.Error:
        pass
    return counts


def survey(task=None, *, day=None, sessions_root=None, trace=None, events_db=None):
    """Coverage rows for one task and its children, or for a whole day."""

    root = Path(sessions_root) if sessions_root else default_sessions_root()
    files = rollout_files(root, day)
    metas = {path: session_meta(path) for path in files}
    if task:
        files = [path for path, meta in metas.items() if meta[0] == task or meta[1] == task]
        metas = {path: session_meta(path) for path in files}

    hooks = trace_counts(trace or default_trace())
    gates = gate_counts(events_db or default_events_db())

    rows = []
    for path in sorted(files):
        session_id, parent = metas.get(path, (None, None))
        calls, jev = count_session(path)
        steps = sum(calls.values())
        entry = gates.get(session_id) or {}
        rows.append({
            "session": session_id,
            "parent": parent,
            "role": "child" if parent else "root",
            "steps": steps,
            "jev_calls": jev,
            "jev_ratio": (jev / steps) if steps else 0.0,
            "hook_events": hooks.get(session_id, 0),
            "gate_rows_kept": entry.get("kept", 0),
            "gate_denials": entry.get("denied", 0),
            "tools": calls.most_common(4),
        })
    return rows


def summarise(rows):
    """Aggregate roots, children and everything, never merged into one claim."""

    totals = {}
    for role in ("root", "child", "all"):
        subset = rows if role == "all" else [row for row in rows if row["role"] == role]
        steps = sum(row["steps"] for row in subset)
        jev = sum(row["jev_calls"] for row in subset)
        totals[role] = {
            "sessions": len(subset),
            "steps": steps,
            "jev_calls": jev,
            "jev_ratio": (jev / steps) if steps else 0.0,
            "hook_events": sum(row["hook_events"] for row in subset),
            "gate_denials": sum(row["gate_denials"] for row in subset),
        }
    return totals


def render(rows, limit=25):
    totals = summarise(rows)
    lines = []
    for role in ("root", "child", "all"):
        t = totals[role]
        lines.append(f"{role:>5}: sessions={t['sessions']:4d} steps={t['steps']:7d} "
                     f"jev={t['jev_calls']:6d} ({t['jev_ratio']:6.2%}) "
                     f"hook_events={t['hook_events']:7d} denials={t['gate_denials']}")
    lines.append("")
    lines.append(f"{'session':38s} {'role':6s} {'steps':>7s} {'jev':>6s} {'ratio':>7s} "
                 f"{'hooks':>6s} {'gate':>5s} {'deny':>4s}")
    for row in sorted(rows, key=lambda item: -item["steps"])[:limit]:
        lines.append(f"{str(row['session']):38s} {row['role']:6s} {row['steps']:7d} "
                     f"{row['jev_calls']:6d} {row['jev_ratio']:7.2%} {row['hook_events']:6d} "
                     f"{row['gate_rows_kept']:5d} {row['gate_denials']:4d}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Jev coverage per session and subagent")
    parser.add_argument("--task", help="limit to one session id and its children")
    parser.add_argument("--day", help="limit to one day, YYYYMMDD")
    parser.add_argument("--sessions-root", help="override the rollout directory")
    parser.add_argument("--trace", help="override hook-trace.jsonl")
    parser.add_argument("--events-db", help="override tasks.sqlite3")
    parser.add_argument("--json", action="store_true", help="emit raw rows")
    args = parser.parse_args(argv)
    rows = survey(args.task, day=args.day, sessions_root=args.sessions_root,
                  trace=args.trace, events_db=args.events_db)
    print(json.dumps(rows, indent=1) if args.json else render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
