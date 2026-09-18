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
    "JEV MODE. For a batch of bounded semantic judgments over 5+ items - classifying, routing, "
    "triaging, ranking, relevance-filtering, or mapping text onto a fixed option set - send those "
    "judgments to Jev instead of deciding them in your own context: "
    "`jev-mode batch --items <items.jsonl> --questions <questions.json> --out <answers.jsonl>`, "
    "or `jev-mode ask --state-file <f> --questions-file <f>` for a single item. "
    "Design rules that were measured: one `choice` question carrying a short decisive rule beats "
    "many atomic `noul` questions; keep criteria terse, because long criteria cost accuracy; one "
    "record per call; combine answers in code and never ask Jev to reason, count, compare numbers "
    "or compare dates; keep item text out of your own context - read it in code and print counts "
    "only. Non-triggers: not for prose, code, explanation, arithmetic, counting or date comparison, "
    "and not for fewer than 5 items."
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
