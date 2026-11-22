"""Validation of the user's research request (the direct-injection boundary).

The user is the principal, so their request is mostly trusted. It is still
checked because (a) the API may be reachable by others, and (b) a request that
tries to pull out system prompts or credentials is never a research question.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from security import audit
from security.prompt_injection import Detection, max_severity, scan

MIN_LENGTH = 15
MAX_LENGTH = 2000
MIN_WORDS = 3


class InputValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidatedQuery:
    text: str
    flags: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    # Drop control and zero-width/format characters (used to hide instructions).
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def validate_query(raw: object, request_id: str = "") -> ValidatedQuery:
    if not isinstance(raw, str):
        raise InputValidationError("invalid_type", "The request must be text.")

    text = _clean(raw)
    if len(text) < MIN_LENGTH or len(text.split()) < MIN_WORDS:
        raise InputValidationError(
            "too_short",
            f"Please describe the business question in at least {MIN_LENGTH} characters.",
        )
    if len(text) > MAX_LENGTH:
        raise InputValidationError(
            "too_long", f"The request is limited to {MAX_LENGTH} characters."
        )

    detections: list[Detection] = scan(text)
    severity = max_severity(detections)
    if severity == "high":
        audit.record(
            "direct_prompt_injection_blocked",
            severity="high",
            request_id=request_id,
            detail={
                "patterns": [d.pattern_id for d in detections],
                "excerpt": detections[0].excerpt,
            },
        )
        raise InputValidationError(
            "unsafe_request",
            "This request asks the system to override its instructions or disclose "
            "internal information, so it was not run. Please submit a business "
            "research question.",
        )

    flags = [d.pattern_id for d in detections]
    if flags:
        audit.record(
            "suspicious_input_flagged",
            severity="low",
            request_id=request_id,
            detail={"patterns": flags, "action": "allowed; treated as plain text"},
        )
    return ValidatedQuery(text=text, flags=flags)
