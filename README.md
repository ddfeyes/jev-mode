# jev-mode

**Cut your coding agent's token use on bulk semantic judgments.**

Your agent burns tokens deciding the same kind of small question over and over:
which team owns this ticket, is this file stale, does this message report a bug,
which category is this row. `jev-mode` moves those judgments out of the model
context and into [TypeSafe Jev](https://typesafe.ai), a typed-judgment model:
you send one item and a map of typed questions, and you get answers constrained
to the options you supplied, each with a calibrated confidence.

Measured on 1000 triage judgments against a Jev-free control on the same task:

| metric | in-context control | through jev-mode |
| --- | ---: | ---: |
| total tokens | 7,987,983 | 1,770,917 (**-77.8 %**) |
| work-attributable input | 3,539,466 | 220,879 (**16x less**) |
| model round trips | 40 | 14 (**-65 %**) |
| accuracy (department) | 93.70 % | **96.10 %** |
| accuracy (boolean field) | 100 % | 100 % |

Jev's own cost for that run was about four cents. The design rules below come
from that measurement, not from opinion.

## Why I built this

I run coding agents all day, and I kept watching them spend whole context
windows on decisions that aren't hard. Triage 400 support messages. Tag 600
files as stale or current. Decide which of six teams owns a ticket. None of
those judgments need a frontier model, and none of them need to sit in the
context window being re-read on every later turn - but that is exactly what
happens, and you keep paying for it on every request afterwards.

I had a typed-judgment model available (Jev) that answers from a fixed option
set with a confidence, and it costs almost nothing per call. So the obvious
question was whether I could move the judgments there without the answers
getting worse. I didn't trust my intuition on that, so I measured it instead:
two agents, same task, same model, same rubric, one doing the work itself and
one routing every verdict through Jev.

The result surprised me in both directions. The token saving was bigger than I
expected - 78 % fewer tokens, 16x less work-attributable input - and the
accuracy was not worse, it came out slightly better (96.1 % against 93.7 %).
The part I got wrong first was the question design: my initial version lost 5.3
points of accuracy, and fixing it took several rounds of measuring different
question shapes. Those findings are the section further down, and they matter
more than the wrapper code does.

Two things I want to be straight about, because they are easy to oversell. The
control arm was not a weak baseline - across repeated runs it scored anywhere
from 93.4 % to 98.2 %, so the honest claim is parity, not superiority. And the
benchmark corpus was synthetic, so treat the token ratio as the durable result
and re-measure the accuracy on your own data.

## Install

```sh
pip install git+https://github.com/ddfeyes/jev-mode
export TYPESAFE_API_KEY=...   # bring your own TypeSafe key
jev-mode check                # verifies config and reaches the API
```

Not published on PyPI yet, so plain `pip install jev-mode` will not work. No
dependencies either way: Python 3.8+ and the standard library only. Without
`pip` at all:

```sh
git clone https://github.com/ddfeyes/jev-mode
cd jev-mode
PYTHONPATH=src python3 -m jev_mode.cli check
```

The key can also live in a file: put `TYPESAFE_API_KEY=...` in
`~/.config/jev-mode/typesafe.env`, or point `TYPESAFE_ENV_FILE` at it. The key
is never printed, logged or committed.

## Use

Two shapes, and they have opposite answers:

**Many items** - one item per call, driven from code:

```sh
jev-mode batch --items items.jsonl --questions questions.json --out answers.jsonl --pool 6
```

**One item, many questions** - put them all in one call:

```sh
jev-mode ask --state-file one.txt --questions-file questions.json
```

From Python:

```python
from jev_mode import JevClient, run_batch

client = JevClient()                       # reads TYPESAFE_API_KEY
summary = run_batch("items.jsonl", {
    "owner": {
        "type": "choice",
        "instructions": "Which team owns the reporter's complaint?",
        "criteria": {"billing": "their own money",
                     "technical": "the product itself failing"},
    },
}, "answers.jsonl", client=client, pool=6)
print(summary["ok"], summary["failed"], summary["mean_confidence"])
```

`items.jsonl` is one JSON object per line; `--state-field` names the field to
judge (default `text`) and `--id-field` the identifier (default `id`). Answers
are written as they complete, so a long run is inspectable while it runs, a
failed item is isolated rather than fatal, and retries are counted per item.

See `examples/` for a runnable six-way triage.

## Question types

| type | answers | returns |
| --- | --- | --- |
| `choice` | which of these options? | `choice`, `probabilities`, `confidence` |
| `score` | which level on a rubric? | `score`, `legend`, `probabilities`, `confidence` |
| `noul` | is this true? | `noul` (0.0-1.0) |


## Filter every step, not just bulk batches

`classify` handles a pile of items. The gate handles the agent's *steps*: wire
`jev-mode gate` into a pre-action hook and each command, edit or spawn is judged
against the objective before it runs. A step that repeats work already done or
leaves the objective is refused, with the reason handed back to the agent.

```sh
echo '{"tool_name":"Bash","tool_input":{"cmd":"pip install pandas"},
       "objective":"publish the package","gate":{"enabled":true}}' | jev-mode gate
```

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny",
 "permissionDecisionReason":"Jev judged this step 'off_goal' at confidence 0.95; ..."}}
```

Rules that make it safe to leave on:

- **It fails open.** No key, no network, a slow answer, a malformed answer - the
  step is allowed and the reason is recorded. A gate that breaks work when the
  model is unreachable is worse than no gate.
- **It needs the history.** The recent steps are part of the state. Verified:
  an identical command was judged `advances_goal` on a stateless check and
  `repeats_or_redundant` at 0.99 once the history was included.
- **It only blocks repeats and off-goal steps, above 0.8 confidence.** The
  `unsafe_or_irreversible` answer is recorded but never enforced - a typed model
  is not a policy engine.
- **It has a denial budget** (3 by default) that counts *consecutive* refusals,
  then fails open so an agent cannot be trapped. Any allowed step clears the
  counter. A lifetime budget was the first implementation and it was wrong: one
  live task collected three denials and then ran 68 steps with no filtering,
  which contradicts the promise that every step is judged.
- **It is off until you enable it**, and it only gates state-changing tools
  (`Bash`, `apply_patch`, `write`, `edit`, ...) - not every read.

Cost: one Jev call per gated step, roughly two hundredths of a cent, against a
per-step agent context measured at ~109k tokens.

### Measuring it instead of trusting it

"Every step is filtered" is a claim, so `coverage` counts it. It reads the two
records an agent host already writes - the per-session rollouts (every tool call
and every `codex-typesafe` invocation, attributed to the session that made it)
and the hook trace (one line per hook invocation with its session id) - and
reports root sessions and subagents separately.

```sh
jev-mode coverage --task <session-id>
jev-mode coverage --day 20260918
```

```
 root: sessions=   1 steps=    389 jev=    52 (13.37%) hook_events=    501 denials=0
child: sessions=  42 steps=   4872 jev=   250 ( 5.13%) hook_events=      0 denials=0
  all: sessions=  43 steps=   5261 jev=   302 ( 5.74%) hook_events=    501 denials=0
```

That output is from a real session and its 42 subagents on 2026-09-18, and it is
the reason this tool exists: the numbers are far below "every step", and the two
reasons were both fixable-but-real.

**Subagent sessions do not receive pre-action hooks.** Measured: 339 subagent
sessions in one day, 29,146 tool calls between them, and **zero** hook
invocations - no `PreToolUse`, no `SessionStart`, nothing. Two live probes
launched specifically to test this, each running exactly one command, also
produced zero hook records while the parent's own steps were being gated
continuously in the same seconds. So a gate wired into the host's hook system
protects the parent and **not** the children it spawns. What a child *does* get
is the directive, injected through the spawn hook, which is why the honest
mitigation is to keep the child's own work narrow and to check its coverage
afterwards with `coverage`.

**The retained event table cannot measure coverage.** It keeps only the newest
200 rows per task, so a long session's early steps are deleted before you look.
That is why `coverage` reads the append-only trace for hook counts and treats the
event table as a floor for denials only.

## Design rules that were measured

These moved accuracy by 5-12 points in testing. They are the reason this is more
than a thin HTTP wrapper.

- **One `choice` question carrying a short decisive rule beats many atomic
  `noul` questions.** Nine independent "is the main subject X?" questions
  recombined by argmax scored 84.5 %; one `choice` carrying the discriminating
  rule scored 96.1 %. Each atomic question scores high on its own, so the argmax
  follows noise.
- **Keep criteria terse.** Richer prose criteria scored 82.3 % against 86.0 %
  for short ones, and one further clarifying paragraph cost another 3.7 points.
  Instruction length is a real failure mode.
- **Prefer a small `choice` to a boolean `noul` for a thresholded decision.**
  Replacing "is this a data loss?" with a three-way choice took that field from
  90.3 % to 100 %.
- **One record per call.** A state holding an array of records collapses into a
  single reading - the model will answer one question about the whole set. This
  is the failure mode most likely to bite you.
- **Split a System Two decision into atomic judgments and combine them in code**
  with your own weights. Never ask Jev to count, compare magnitudes or compare
  dates; it returns decisions, not arithmetic.
- **Keep the item text out of your own context.** Read it in code and print
  counts only. Most of the saving comes from this, not from the API call.

## Always-on mode (optional)

`jev-mode` can inject a short directive into an agent session so the agent
reaches for the typed path without being told. It is **off until you configure
it**, because an unconfigured tool should not silently change anyone's agent.

```sh
jev-mode hook                                    # prints nothing while disabled
mkdir -p ~/.config/jev-mode
echo '{"enabled": true}' > ~/.config/jev-mode/config.json
jev-mode hook                                    # now prints the directive payload
```

`JEV_MODE=1` / `JEV_MODE=0` overrides the config, `JEV_MODE_CONFIG` moves the
config file, and `"directive"` in the config replaces the text. See `hooks/` for
wiring it into a hook system.

**Honest limit, measured:** whether this changes *behaviour* depends on what
else tells your agent to use Jev. In our own repository the directive proved
redundant with an existing `AGENTS.md` instruction - the control arm used Jev
anyway - so treat it as a portable default for contexts that have no such
instruction, not as an enforcement gate.

## Limits

- **Jev is not a text model.** No prose, code, explanation, arithmetic, counting
  or date comparison. Bounded decisions only.
- **Typed answers are not proof.** A confidence is not correctness. In the
  benchmark 88-96 % of department verdicts were right, not 100 %, so route
  low-confidence items to a second pass or to a human.
- **You need your own TypeSafe API key.** This package ships none.
- **The estimates are estimates.** Request-size and token figures come from a
  calibrated character rule, not a tokenizer.
- **The benchmark corpus was synthetic** (1000 messages over 72 scenario cores).
  The token ratio is the durable result; absolute accuracies will differ on your
  data, so re-measure on your own workload before trusting the headline.
- **The host decides when hooks run, and on Codex they do not run in subagents.**
  Measured 2026-09-18: 29,146 subagent tool calls, zero hook invocations. The
  gate therefore covers the session you are in, not the children it spawns. Use
  `jev-mode coverage` to see the split on your own host rather than assuming it.

## Development

```sh
python3 -m unittest discover -s tests -v      # 66 tests, no network
```

Every test fakes the transport, so the suite runs offline and never needs a key.

## License

MIT. See `LICENSE`.
