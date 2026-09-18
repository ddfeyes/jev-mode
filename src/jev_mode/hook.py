"""Always-on directive for hook systems.

Some agent runtimes call an external command on session start and on each prompt,
then merge the printed context into the model's window. This module builds the
text that command should print, and holds the switch.

Switch resolution order:
  1. JEV_MODE=0/off/false in the environment turns it off.
  2. JEV_MODE=1/on/true turns it on.
  3. Otherwise the config file (default ~/.config/jev-mode/config.json, override
     with JEV_MODE_CONFIG) decides; an absent config means off, so an
     unconfigured install never changes anyone's agent silently.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_SWITCH = "JEV_MODE"
ENV_CONFIG = "JEV_MODE_CONFIG"

# Kept short on purpose: session context is re-sent with every request, and the
# measurement showed long instructions also degrade the typed model's accuracy.
DIRECTIVE = (
    "JEV MODE - MANDATORY FOR BULK JUDGMENTS. If you are about to make the SAME kind of decision "
    "about 5 or more separate things - classifying, routing, triaging, ranking, scoring, filtering "
    "for relevance, or mapping each item onto a fixed option set - you MUST send those decisions to "
    "Jev first, one item per call, instead of deciding them by reading. Do not batch them in your "
    "own context and do not judge a sample and extrapolate. This is not optional and not a "
    "suggestion: it is the cheapest correct path, and skipping it is the failure this mode exists "
    "to prevent.\n"
    "Recognise the trigger in these shapes: a list of files to keep or drop; N log lines or errors "
    "to sort into categories; N messages, tickets, comments or diffs to label; N candidates to rank "
    "or filter; any loop where each iteration needs a semantic verdict.\n"
    "Fastest path, one command - no request files to hand-write:\n"
    "  jev-mode classify --input <file|'dir/*.txt'> --question \"<one question>\" "
    "--options \"a=<what a means>,b=<what b means>\" --out answers.jsonl\n"
    "Or when you already have items.jsonl plus a question map:\n"
    "  jev-mode batch --items items.jsonl --questions questions.json --out answers.jsonl\n"
    "Read the counts the command prints, not every answer.\n"
    "Measured rules: one `choice` question carrying a short decisive rule beats many atomic `noul` "
    "questions; keep criteria terse, because long criteria cost accuracy; one record per call; "
    "combine answers in code and never ask Jev to reason, count, compare numbers or compare dates; "
    "keep item text out of your own context - read it in code and print counts only.\n"
    "Do NOT use Jev for prose, code, explanation, arithmetic, counting, date comparison, or for "
    "fewer than 5 items.\n"
    "Measured payoff: 78 % fewer tokens, 16x less work-attributable input, 65 % fewer model round "
    "trips, accuracy equal or better."
)

_TRUE = {"1", "on", "true", "yes", "enabled"}
_FALSE = {"0", "off", "false", "no", "disabled"}


def config_path(env=None):
    """Resolve the config path, honouring an explicitly passed environment."""

    environ = os.environ if env is None else env
    return Path(environ.get(ENV_CONFIG) or "~/.config/jev-mode/config.json").expanduser()


def enabled(env=None):
    """True when the directive should be injected."""

    environ = os.environ if env is None else env
    raw = str(environ.get(ENV_SWITCH, "")).strip().lower()
    if raw in _FALSE:
        return False
    if raw in _TRUE:
        return True
    try:
        config = json.loads(config_path(environ).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(config.get("enabled"))


def directive(env=None, text=None):
    """Return the directive to inject, or '' when disabled or overridden."""

    environ = os.environ if env is None else env
    if not enabled(environ):
        return ""
    if text:
        return text.strip()
    try:
        override = json.loads(config_path(environ).read_text()).get("directive")
    except (OSError, json.JSONDecodeError):
        override = None
    return override.strip() if isinstance(override, str) and override.strip() else DIRECTIVE
