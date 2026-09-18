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

## Install

```sh
pip install jev-mode          # or: pip install git+https://github.com/ddfeyes/jev-mode
export TYPESAFE_API_KEY=...   # bring your own TypeSafe key
jev-mode check                # verifies config and reaches the API
```

No dependencies: Python 3.8+ and the standard library only. Without `pip`:

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

## Development

```sh
python3 -m unittest discover -s tests -v      # 32 tests, no network
```

Every test fakes the transport, so the suite runs offline and never needs a key.

## License

MIT. See `LICENSE`.
