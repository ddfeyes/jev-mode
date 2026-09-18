"""`jev-mode gate`: read a pre-action event on stdin, answer with a decision.

Wire this into a hook system's pre-action event and every step is filtered by
Jev before it runs. The event arrives as JSON on stdin; the decision goes out as
JSON on stdout in the hook shape, or as a bare verdict with `--json`.

Recognised input keys (any of): `tool_name`, `tool`, `command`; `tool_input`,
`arguments`, `input`; `objective`, `prompt`; `recent` (list of past steps);
`denials` (count so far).

Exit status is always 0: a gate must never break the host's pipeline. Read the
decision from the JSON, not from the exit code.
"""

from __future__ import annotations

import json
import sys

from .client import JevClient, JevError
from .gate import decide, settings, step_summary


def _first(payload, *names, default=None):
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return default


def evaluate(payload, *, client=None):
    tool = _first(payload, "tool_name", "tool", "name", default="")
    tool_input = _first(payload, "tool_input", "arguments", "input", "command", default={})
    objective = _first(payload, "objective", "prompt", "goal", "task", default="")
    recent = _first(payload, "recent", "history", default=None)
    denials = int(_first(payload, "denials", "prior_denials", default=0) or 0)
    config = {"gate": _first(payload, "gate", "config", default={}) or {}}
    if not config["gate"]:
        # No inline config: take it from the environment switch.
        import os
        if str(os.environ.get("JEV_GATE", "")).strip().lower() in ("1", "on", "true", "yes"):
            config = {"gate": {"enabled": True}}
    verdict = decide(objective, tool, tool_input, config, client=client,
                     prior_denials=denials, recent=recent)
    if verdict.get("allow") and verdict.get("gated"):
        verdict["next_step"] = step_summary(tool, tool_input)
    return verdict


def main(argv=None, client=None):
    args = list(sys.argv[1:] if argv is None else argv)
    as_hook = "--json" not in args
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"gated": False, "allow": True,
                          "reason": f"gate failed open: bad event ({exc.msg})"}))
        return 0
    try:
        verdict = evaluate(payload, client=client)
    except JevError as exc:
        verdict = {"gated": False, "allow": True, "reason": f"gate failed open: {exc}"}

    if verdict.get("allow"):
        # An allowing gate stays silent in hook mode so it adds no context.
        print(json.dumps(verdict) if not as_hook else json.dumps({"allow": True,
                                                                  "next_step": verdict.get("next_step")}))
        return 0
    if as_hook:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": verdict["reason"]}}))
    else:
        print(json.dumps(verdict, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
