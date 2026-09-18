"""Command line interface: ask, batch, hook, check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .batch import run_batch
from .client import JevClient, JevError, load_api_key, validate_questions

__version__ = "1.0.0"


def _read(path_or_inline, is_file):
    if not is_file:
        return path_or_inline
    try:
        return Path(path_or_inline).read_text()
    except OSError as exc:
        raise JevError(f"cannot read file: {type(exc).__name__}") from None


def _load_json(text, label):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JevError(f"{label} is not valid JSON: {exc.msg}") from None


def _check(args):
    """Report configuration health; the only place the CLI touches the network."""

    report = {"version": __version__, "api_key_present": False, "reachable": False,
              "model": None, "error": None}
    try:
        report["api_key_present"] = bool(load_api_key())
    except JevError as exc:
        report["error"] = str(exc)
        print(json.dumps(report, indent=2))
        print(f"jev-mode: {exc}", file=sys.stderr)
        return 2
    if args.offline:
        print(json.dumps(report, indent=2))
        return 0
    client = JevClient(model=args.model)
    try:
        result = client.ask("The parcel arrived two days late and the box was crushed.", {
            "topic": {"type": "choice", "instructions": "Which topic is this?",
                      "criteria": {"shipping": "a delivery", "billing": "money"}}})
        report["reachable"] = True
        report["model"] = result.get("model") or client.model
        report["sample_answer"] = (result.get("answers", {}).get("topic", {}) or {}).get("choice")
    except JevError as exc:
        report["error"] = str(exc)
    print(json.dumps(report, indent=2))
    if report["error"]:
        print(f"jev-mode: {report['error']}", file=sys.stderr)
    return 0 if report["reachable"] else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="jev-mode", description=__doc__)
    parser.add_argument("--version", action="version", version=f"jev-mode {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="judge a single item")
    ask.add_argument("--state")
    ask.add_argument("--state-file")
    ask.add_argument("--questions", help="inline JSON question map")
    ask.add_argument("--questions-file")
    ask.add_argument("--model", default=None)
    ask.add_argument("--timeout", type=float, default=120)

    batch = sub.add_parser("batch", help="judge many items, one call each")
    batch.add_argument("--items", required=True, help="JSONL, one record per line")
    batch.add_argument("--questions", required=True, help="JSON question map applied to every item")
    batch.add_argument("--out", required=True, help="JSONL answers, one line per item")
    batch.add_argument("--state-field", default="text")
    batch.add_argument("--id-field", default="id")
    batch.add_argument("--pool", type=int, default=6)
    batch.add_argument("--retries", type=int, default=1)
    batch.add_argument("--model", default=None)
    batch.add_argument("--timeout", type=float, default=120)

    hook = sub.add_parser("hook", help="print the Jev-mode directive for a hook system")
    hook.add_argument("--event", default="UserPromptSubmit")

    gate = sub.add_parser("gate", help="filter one step through Jev (reads a pre-action event on stdin)")
    gate.add_argument("--json", action="store_true", help="print the bare verdict instead of a hook payload")

    classify = sub.add_parser(
        "classify",
        help="classify a plain list (one line per item, or a glob of files) with one question")
    classify.add_argument("--input", required=True,
                          help="text file, JSONL file, or glob such as 'logs/*.txt'")
    classify.add_argument("--question", required=True, help="the single choice question")
    classify.add_argument("--options", required=True,
                          help="comma-separated option=description pairs, e.g. 'yes=...,no=...'")
    classify.add_argument("--out", required=True, help="JSONL answers, one line per item")
    classify.add_argument("--pool", type=int, default=6)
    classify.add_argument("--retries", type=int, default=1)
    classify.add_argument("--model", default=None)
    classify.add_argument("--timeout", type=float, default=120)

    check = sub.add_parser("check", help="verify configuration and reach the API")
    check.add_argument("--offline", action="store_true", help="skip the network call")
    check.add_argument("--model", default=None)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "ask":
            state = _read(args.state if args.state is not None else args.state_file,
                          args.state_file is not None)
            raw = _read(args.questions if args.questions is not None else args.questions_file,
                        args.questions_file is not None)
            if state is None or raw is None:
                raise JevError("ask needs a state and a question map")
            client = JevClient(model=args.model, timeout=args.timeout)
            result = client.ask(state, _load_json(raw, "questions"))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "batch":
            try:
                raw = Path(args.questions).read_text()
            except OSError as exc:
                raise JevError(f"cannot read questions file: {type(exc).__name__}") from None
            questions = _load_json(raw, "questions")
            validate_questions(questions)
            client = JevClient(model=args.model, timeout=args.timeout)
            summary = run_batch(args.items, questions, args.out, client=client,
                                state_field=args.state_field, id_field=args.id_field,
                                pool=args.pool, retries=args.retries)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.command == "hook":
            from .hook import directive
            text = directive()
            if not text:
                return 0
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": args.event, "additionalContext": text}}))
            return 0
        if args.command == "classify":
            from .classify import classify
            summary = classify(args.input, args.question, args.options, args.out,
                               client=JevClient(model=args.model, timeout=args.timeout),
                               pool=args.pool, retries=args.retries)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.command == "gate":
            from .gate_cli import main as gate_main
            return gate_main(["--json"] if args.json else [])
        if args.command == "check":
            return _check(args)
    except JevError as exc:
        print(f"jev-mode: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
