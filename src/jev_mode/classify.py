"""One-command classification of a plain list.

Authoring `items.jsonl` plus `questions.json` is enough friction that an agent
skips the typed path altogether. `classify` takes a plain list of items - one
per line, or a file whose lines are items - plus one question and a set of
options, and does the whole round trip.
"""

from __future__ import annotations

import glob as globlib
import json
from pathlib import Path

from .batch import run_batch
from .client import JevClient, JevError


def parse_options(text):
    """Turn `a=desc,b=desc` into a criteria map."""

    criteria = {}
    for pair in (text or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            # A bare name is a legitimate shorthand: the option speaks for
            # itself when its name is already the description.
            criteria[pair] = pair
            continue
        name, description = pair.split("=", 1)
        name = name.strip()
        if not name:
            raise JevError(f"option {pair!r} has an empty name")
        criteria[name] = description.strip() or name
    if len(criteria) < 2:
        raise JevError("a choice needs at least two options, for example yes=...,no=...")
    return criteria


def load_text_items(pattern, id_prefix="row"):
    """Read items from a JSONL file, a text file, or a glob of files.

    - a .jsonl/.ndjson file: each line is a record; `text` holds the state
    - any other file: each non-empty line is one item
    - a glob: each matched file's contents become one item, so a directory of
      logs or notes can be classified in a single command
    """

    matches = (sorted(globlib.glob(pattern))
               if any(ch in pattern for ch in "*?[") else [pattern])
    if not matches:
        raise JevError(f"no input matched {pattern!r}")

    items = []
    seen = 0
    for path in matches:
        target = Path(path)
        if not target.exists():
            raise JevError(f"cannot read {path}: not found")
        if target.suffix.lower() in (".jsonl", ".ndjson"):
            for number, line in enumerate(target.read_text().splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise JevError(f"{path} line {number} is not valid JSON: {exc.msg}") from None
                if "text" not in record:
                    raise JevError(f"{path} line {number} has no 'text' field")
                seen += 1
                items.append({"id": record.get("id", f"{id_prefix}{seen}"),
                              "text": record["text"]})
            continue
        body = target.read_text(errors="replace")
        if len(matches) > 1:
            items.append({"id": target.name, "text": f"# {target.name}\n{body}"})
            seen += 1
        else:
            for number, line in enumerate(body.splitlines(), start=1):
                line = line.strip()
                if line:
                    seen += 1
                    # Number by item, not by source line, so blank lines do not
                    # create gaps in the ids.
                    items.append({"id": f"{id_prefix}{seen}", "text": line})
    if not items:
        raise JevError(f"{pattern!r} produced no items")
    return items


def classify(pattern, question, options, out_path, *, client=None, pool=6, retries=1):
    """Classify every item; return the batch summary plus per-option counts."""

    criteria = parse_options(options)
    items = load_text_items(pattern)
    questions = {"answer": {"type": "choice", "instructions": question, "criteria": criteria}}
    client = client or JevClient()
    summary = run_batch(items, questions, out_path, client=client, pool=pool,
                        retries=retries, state_field="text", id_field="id")
    counts = {name: 0 for name in criteria}
    low = 0
    for line in Path(out_path).read_text().splitlines():
        row = json.loads(line)
        answer = (row.get("answers") or {}).get("answer") or {}
        if answer.get("choice") in counts:
            counts[answer["choice"]] += 1
        if (answer.get("confidence") or 1.0) < 0.6:
            low += 1
    summary["counts"] = counts
    summary["low_confidence"] = low
    return summary
