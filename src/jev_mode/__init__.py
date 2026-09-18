"""jev-mode: route bulk bounded semantic judgments to TypeSafe Jev.

Public API:

    from jev_mode import JevClient, run_batch

    client = JevClient()                      # reads TYPESAFE_API_KEY
    result = client.ask("the message text", {
        "team": {"type": "choice", "instructions": "Which team?",
                 "criteria": {"billing": "money", "technical": "broken"}},
    })
    result["answers"]["team"]["choice"]

The batch runner applies one call per item, in parallel, with retries and
resumable JSONL output.
"""

from .client import JevClient, JevError, estimate_tokens, validate_questions
from .batch import run_batch

__version__ = "1.0.0"
__all__ = ["JevClient", "JevError", "run_batch", "estimate_tokens", "validate_questions"]
