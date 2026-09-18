"""Filter every step through Jev before it runs.

The point of this mode is not only to offload bulk work; it is to have a typed
judgment in front of each step the agent is about to take. Wire `decide` into
whatever hook system delivers a pre-action event, and a step that repeats work
already done or leaves the objective gets refused before it costs anything.

Design rules, all deliberate:

- One `choice` question per step. The measurement showed a single choice
  carrying a short decisive rule beats several atomic yes/no questions, and long
  criteria cost accuracy.
- The recent steps are part of the state. Without them the model cannot tell a
  fresh action from a repeat - verified: an identical command was judged
  `advances_goal` on a stateless check and `repeats_or_redundant` at 0.99 once
  the history was included.
- Fail open. Any transport error, timeout or malformed answer allows the step.
  A gate that breaks work when the model is unreachable is worse than no gate.
- Deny only on repeat/off-goal answers above a high confidence threshold. The
  unsafe answer is recorded but never used to block: a typed model is not a
  policy engine.
- Bounded refusals (`max_denials`), but only CONSECUTIVE ones: an allowed step
  clears the counter. The gate stops a wrong step without trapping the agent,
  and a later step is still judged. A lifetime budget was the first
  implementation and it was wrong - a live task collected three denials and then
  ran 68 steps with no filtering at all.
"""

from __future__ import annotations

import json

DEFAULT_TOOLS = ("Bash", "exec_command", "shell", "local_shell", "apply_patch",
                 "write", "edit")
DEFAULT_DENY = ("repeats_or_redundant", "off_goal")

QUESTION = {
    "step": {
        "type": "choice",
        "instructions": (
            "An agent is about to take its next step. Judge the step against the objective: "
            "does it advance the objective, repeat work already done, or leave the objective?"
        ),
        "criteria": {
            "advances_goal": "the step moves the objective forward: it produces needed information, a required change, or a necessary verification",
            "repeats_or_redundant": "the same command, inspection or edit was already performed and this step adds nothing new",
            "off_goal": "the step does not serve the stated objective, or it jumps to unrelated work",
            "unsafe_or_irreversible": "the step destroys data, publishes, spends money or changes external state without the objective requiring it",
        },
    }
}


def settings(config):
    """Resolve gate settings with safe defaults (disabled, high threshold)."""

    block = (config or {}).get("gate") or {}
    if not isinstance(block, dict):
        block = {}
    return {
        "enabled": bool(block.get("enabled")),
        "threshold": float(block.get("threshold", 0.8)),
        "deny": tuple(block.get("deny") or DEFAULT_DENY),
        "max_denials": int(block.get("max_denials", 3)),
        "timeout": float(block.get("timeout", 3)),
        "tools": tuple(block.get("tools") or DEFAULT_TOOLS),
    }


def summarise(value, limit=1200):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[:limit]


def step_summary(tool, tool_input, limit=200):
    """One line describing a step, for the history the next decision sees."""

    return f"{tool}: {summarise(tool_input, limit)}"[:limit]


def build_state(objective, tool, tool_input, recent=None, limit=1200):
    """Compose the gate state: objective, recent steps, and the proposed step."""

    lines = [f"Objective: {objective or '(none recorded)'}"]
    history = [str(item)[:200] for item in (recent or [])][-8:]
    if history:
        lines.append("Steps already taken, most recent last:")
        lines.extend(f"  {n}. {item}" for n, item in enumerate(history, start=1))
    else:
        lines.append("Steps already taken: none")
    lines.append(f"Next step: {tool}")
    lines.append(f"Arguments: {summarise(tool_input, limit)}")
    return "\n".join(lines)


def decide(objective, tool, tool_input, config, *, client=None, prior_denials=0, recent=None):
    """Return the verdict for one step. Never raises."""

    conf = settings(config)
    verdict = {"gated": False, "allow": True, "answer": None, "confidence": None,
               "reason": "", "prior_denials": prior_denials}
    if not conf["enabled"]:
        verdict["reason"] = "gate disabled"
        return verdict
    if not objective:
        verdict["reason"] = "no objective to judge against"
        return verdict
    if tool not in conf["tools"]:
        verdict["reason"] = f"{tool} is not a gated tool"
        return verdict
    if prior_denials >= conf["max_denials"]:
        verdict["reason"] = "denial budget exhausted; failing open"
        return verdict

    verdict["gated"] = True
    try:
        if client is None:
            from .client import JevClient
            client = JevClient()
        result = client.ask(build_state(objective, tool, tool_input, recent), QUESTION)
        answer = ((result.get("answers") or {}).get("step") or {})
        choice, confidence = answer.get("choice"), answer.get("confidence")
        verdict["answer"], verdict["confidence"] = choice, confidence
        if choice in conf["deny"] and confidence is not None and confidence >= conf["threshold"]:
            verdict["allow"] = False
            verdict["reason"] = (
                f"Jev judged this step '{choice}' at confidence {confidence:.2f}; "
                f"objective: {objective[:200]}. Choose a step that advances the objective, "
                f"or say why this one is needed.")
        else:
            verdict["reason"] = f"Jev allowed '{choice}'"
    except Exception as exc:  # noqa: BLE001 - the gate must never break work
        verdict["reason"] = f"gate failed open: {type(exc).__name__}"
    return verdict
