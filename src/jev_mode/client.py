"""TypeSafe Jev client: one state, typed questions, structured answers.

Jev is not a text model. It evaluates a state against typed questions and
returns answers constrained to the options you supplied, each with a calibrated
confidence. There is no prose to parse and no value that was never offered.

Types:
  choice  which of these options?   -> choice, probabilities, confidence
  score   which level on a rubric?  -> score, legend, probabilities, confidence
  noul    is this true?             -> noul (0.0-1.0)

Credentials come from the TYPESAFE_API_KEY environment variable, or from a file
whose path is in TYPESAFE_ENV_FILE. The key is never logged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
ENV_VAR = "TYPESAFE_API_KEY"
ENV_FILE = "TYPESAFE_ENV_FILE"
ENV_ENDPOINT = "TYPESAFE_ENDPOINT"
QUESTION_TYPES = ("choice", "score", "noul")

# Jev documents a 32k ceiling for state plus the longest single question.
MAX_REQUEST_TOKENS = 32_000


def default_config_path():
    """Where to look for a key file when the environment says nothing."""

    return Path(os.environ.get(ENV_FILE) or "~/.config/jev-mode/typesafe.env").expanduser()


class JevError(RuntimeError):
    """Raised for configuration, validation and transport failures."""


def estimate_tokens(value):
    """Cheap request-size estimate, calibrated from measured Jev usage.

    Jev reports roughly one token per six characters of English text, and
    measured calls land within a few percent of that rule. This is a guard rail,
    not a tokenizer: it exists so an oversized request fails locally instead of
    costing a call.
    """

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return len(text) // 6 + 1


def validate_questions(questions):
    """Raise JevError unless `questions` is a valid question map."""

    if not isinstance(questions, dict) or not questions:
        raise JevError("questions must be a non-empty object")
    for qid, question in questions.items():
        if not isinstance(qid, str) or not qid:
            raise JevError("question ids must be non-empty strings")
        if not isinstance(question, dict):
            raise JevError(f"question {qid!r} must be an object")
        unsupported = set(question) - {"type", "instructions", "criteria"}
        if unsupported:
            raise JevError(f"question {qid!r} has unsupported fields: {', '.join(sorted(unsupported))}")
        kind = question.get("type")
        if kind not in QUESTION_TYPES:
            raise JevError(f"question {qid!r} has unsupported type {kind!r}")
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise JevError(f"question {qid!r} needs non-empty instructions")
        criteria = question.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, dict) or len(criteria) < 2:
                raise JevError(
                    f"choice question {qid!r} needs a criteria map with at least two options")
        elif kind == "score":
            if not isinstance(criteria, list) or len(criteria) < 2:
                raise JevError(
                    f"score question {qid!r} needs an ordered criteria list of at least two levels")
        elif criteria is not None:
            if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
                raise JevError(
                    f"noul question {qid!r} criteria may only define true and false")
    return questions


def parse_env_file(text):
    """Parse a minimal KEY=value file, tolerating comments, quotes and export."""

    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_api_key(explicit=None, env=None, path=None):
    """Resolve the API key without ever printing it."""

    if explicit:
        return explicit
    environ = os.environ if env is None else env
    key = environ.get(ENV_VAR)
    if key and key.strip():
        return key.strip()
    target = Path(path) if path else default_config_path()
    try:
        values = parse_env_file(target.read_text())
    except OSError:
        raise JevError(
            f"no API key: set {ENV_VAR} in the environment, or write "
            f"{ENV_VAR}=<your key> into {target}") from None
    key = values.get(ENV_VAR)
    if not key:
        raise JevError(f"{target} does not define {ENV_VAR}")
    return key


def _http_post_json(url, body, key, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:  # noqa: BLE001 - best effort only
            pass
        raise JevError(f"HTTP {exc.code} from TypeSafe: {detail}") from None
    except urllib.error.URLError as exc:
        raise JevError(f"cannot reach TypeSafe: {exc.reason}") from None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        raise JevError("TypeSafe returned a non-JSON response") from None


class JevClient:
    """Minimal TypeSafe Jev client. Bring your own API key."""

    def __init__(self, api_key=None, model=None, endpoint=None, timeout=120, transport=None):
        self.model = model or DEFAULT_MODEL
        self.endpoint = endpoint or os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT
        self.timeout = timeout
        self._api_key = api_key
        # `transport` is the test seam: a callable (url, body, key, timeout) that
        # returns a decoded dict. Tests inject a fake; production uses urllib.
        self._transport = transport or _http_post_json

    @property
    def api_key(self):
        if self._api_key is None:
            self._api_key = load_api_key()
        return self._api_key

    def ask(self, state, questions, timeout=None):
        """Evaluate one state against typed questions."""

        validate_questions(questions)
        if not isinstance(state, str) or not state.strip():
            raise JevError("state must be a non-empty string")
        if estimate_tokens(state) + estimate_tokens(questions) > MAX_REQUEST_TOKENS:
            raise JevError(
                f"request exceeds the {MAX_REQUEST_TOKENS}-token ceiling; "
                "split the state or shorten the questions")
        body = {"state": state, "model": self.model, "questions": questions}
        result = self._transport(self.endpoint, body, self.api_key, timeout or self.timeout)
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise JevError("response carried no answers")
        return result
