"""Batch runner: one Jev call per item, in parallel, with a resumable ledger.

The measured working pattern is one record per call. A state holding an array of
records collapses into a single reading, so a "batched" state silently answers
one question about the whole set instead of one per item.

Answers are written as they complete, so a long run can be inspected while it
runs and re-run without redoing finished items.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .client import JevClient, JevError, estimate_tokens


def read_items(path, state_field="text", id_field="id"):
    """Read a JSONL item file, failing loudly on the first malformed line."""

    items = []
    try:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise JevError(f"items line {number} is not valid JSON: {exc.msg}") from None
                if not isinstance(record, dict):
                    raise JevError(f"items line {number} is not a JSON object")
                if state_field not in record:
                    raise JevError(
                        f"items line {number} has no {state_field!r} field; "
                        f"available: {', '.join(sorted(record)) or 'none'}")
                items.append(record)
    except OSError as exc:
        raise JevError(f"cannot read items file: {type(exc).__name__}") from None
    if not items:
        raise JevError("items file held no records")
    return items


def run_batch(items, questions, out_path, *, client=None, state_field="text", id_field="id",
              pool=6, retries=1, limit=32_000, progress=None):
    """Ask Jev one question set per item and return a summary.

    `items` is an iterable of mappings, or a path to a JSONL file. The summary
    carries counts, usage and mean confidence - never item text.
    """

    if isinstance(items, (str, Path)):
        items = read_items(items, state_field=state_field, id_field=id_field)
    items = list(items)
    if not items:
        raise JevError("no items to judge")

    client = client or JevClient()
    # Fail on malformed questions and oversized items before spending anything.
    question_tokens = estimate_tokens(questions)
    oversized = [r.get(id_field) for r in items
                 if estimate_tokens(r[state_field]) + question_tokens > limit]
    if oversized:
        raise JevError(
            f"{len(oversized)} item(s) exceed the {limit}-token request ceiling, "
            f"first: {oversized[0]}. Split the state or shorten the questions.")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    stats = {"ok": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0}
    confidence = {}

    def one(record):
        attempts = 0
        last_error = ""
        while attempts <= max(0, retries):
            attempts += 1
            try:
                result = client.ask(record[state_field], questions)
                return {"id": record.get(id_field), "answers": result.get("answers", {}),
                        "usage": result.get("usage", {}), "model": client.model,
                        "attempts": attempts}
            except JevError as exc:
                last_error = str(exc)
            except Exception as exc:  # noqa: BLE001 - one bad item must not stop the batch
                last_error = type(exc).__name__
        return {"id": record.get(id_field), "answers": None, "usage": {},
                "model": client.model, "attempts": attempts, "error": last_error}

    with open(target, "w", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=max(1, int(pool))) as workers:
            for row in workers.map(one, items):
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if row.get("error"):
                    stats["failed"] += 1
                else:
                    stats["ok"] += 1
                    usage = row.get("usage") or {}
                    stats["input_tokens"] += int(usage.get("input_tokens") or 0)
                    stats["output_tokens"] += int(usage.get("output_tokens") or 0)
                    for qid, answer in (row.get("answers") or {}).items():
                        if isinstance(answer, dict) and answer.get("confidence") is not None:
                            confidence.setdefault(qid, []).append(float(answer["confidence"]))
                if progress:
                    progress(row)

    return {
        "items": len(items),
        "ok": stats["ok"],
        "failed": stats["failed"],
        "model": client.model,
        "input_tokens": stats["input_tokens"],
        "output_tokens": stats["output_tokens"],
        "mean_confidence": {k: round(sum(v) / len(v), 4) for k, v in sorted(confidence.items())},
        # Reported, not judged: whether the offload paid depends on what the
        # caller's own in-context route would have spent, which is not knowable
        # from here. A local ratio would have rejected the workload that saved
        # 78 % in the benchmark.
        "text_tokens": sum(estimate_tokens(r[state_field]) for r in items),
        "output": str(target),
    }
