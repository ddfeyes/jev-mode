---
name: jev-mode
description: Cut token use on bulk bounded semantic judgments by routing them to TypeSafe Jev instead of deciding them in the model context. Use when a task needs many small verdicts over many items - classifying, routing, triaging, ranking, relevance filtering, or mapping text onto a fixed option set - or when the user asks to save tokens on such a task.
---

# Jev Mode

For five or more bounded semantic judgments, deciding them in your own context
is the expensive way. Send them to Jev instead: one state, a map of typed
questions, answers constrained to the options you supplied, each with a
calibrated confidence.

Install the CLI first: `pip install jev-mode` (or run it from a checkout with
`PYTHONPATH=src python3 -m jev_mode.cli`).

## When it applies

All three must hold:

1. **Many items** - about five or more. Below that the call overhead is not
   worth it.
2. **Bounded answers** - each judgment is a small choice, a rubric level, or a
   yes/no. An open-ended answer space cannot be expressed.
3. **Semantic and one-step** - "does this report a bug?", "which team owns
   this?", "is this relevant?". Not a chain of reasoning.

Do not use it for prose, code, explanation, arithmetic, counting, magnitudes or
date comparison.

## Running it

Many items - one call per item, driven from code:

```sh
jev-mode batch --items items.jsonl --questions questions.json --out answers.jsonl --pool 6
```

One item, many questions - put them in one call:

```sh
jev-mode ask --state-file one.txt --questions-file questions.json
```

The batch helper writes each answer as it completes, isolates a failed item,
retries it a configurable number of times, refuses an oversized item before
spending a call, and reports counts, usage and mean confidence.

## Rules that measurably matter

- **One `choice` question carrying a short decisive rule** beats several atomic
  `noul` questions recombined by argmax (96.1 % vs 84.5 % in testing), and beats
  long, heavily qualified criteria (82.3 % vs 86.0 %).
- **One record per call.** A state holding an array of records collapses into a
  single reading, so a "batched" state answers one question about the whole set.
- **Keep item text out of your own context.** Read it in code, build the request
  files in code, print counts only. This is where most of the saving comes from.
- **Combine answers in code.** Weights and thresholds are your job, not Jev's.
- **Check confidence.** Low-confidence answers are candidates for a second pass
  with sharper criteria, not for silent acceptance.

## Optional: make it the default

```sh
jev-mode hook                        # prints nothing unless configured
echo '{"enabled": true}' > ~/.config/jev-mode/config.json
```

Wire `jev-mode hook` into your agent's session-start and prompt hooks to inject
a short directive automatically. See `hooks/` in the repository.

## Measure the coverage, do not assume it

`jev-mode coverage` counts what actually happened: tool calls versus
`codex-typesafe` invocations from the session rollouts, plus hook invocations
from the append-only hook trace, reported for root sessions and subagents
separately.

```sh
jev-mode coverage --task <session-id>
jev-mode coverage --day 20260918
```

```
 root: sessions=   1 steps=    389 jev=    52 (13.37%) hook_events=    501 denials=0
child: sessions=  42 steps=   4872 jev=   250 ( 5.13%) hook_events=      0 denials=0
```

Measured on Codex 2026-09-18: the host runs **no** session hooks inside subagent
sessions. 339 subagent sessions produced 29,146 tool calls and zero hook
invocations, and two live probes that each ran a single command also produced
none while the parent was being gated in the same seconds. A gate wired into the
host hooks therefore protects the session you are in and not the children it
spawns. Give a spawned child narrow work, require a Jev call per step, and
verify the split afterwards with `coverage` instead of assuming it.
