"""Output validation: the last layer before data leaves an agent or enters the report.

Two entry points:

* `validate_analysis`     - run by an agent on its own LLM output.
* `validate_agent_result` - run by the orchestrator on what an agent returned
                            over A2A. A remote agent's response is untrusted
                            input, exactly like a web page.

Checks:
* schema            - output must match the agreed contract
* grounded citations- a finding may only cite URLs the agent actually retrieved;
                      a "sourced fact" with no valid source is downgraded
* stray links       - URLs in free text that were never retrieved are removed
                      (a classic exfiltration channel is a crafted link)
* secrets           - key-shaped strings and the real configured secrets are redacted
* injection echo    - instruction-like text in the output is removed
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import ValidationError

from backend.orchestrator.state import ANALYSIS_MODELS, AgentResult, BaseAnalysis
from security import audit
from security.prompt_injection import neutralize

A = TypeVar("A", bound=BaseAnalysis)

MAX_RESULT_BYTES = 400_000
REDACTED = "[redacted secret]"
LINK_REMOVED = "[unverified link removed]"

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),  # OpenAI-style
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),  # Groq
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),  # Google
    re.compile(r"\b(sk|pk)-lf-[A-Za-z0-9\-]{16,}\b"),  # Langfuse
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b"),
    re.compile(
        r"\b(api[_\s-]?key|secret|password|access[_\s-]?token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}",
        re.IGNORECASE,
    ),
]
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_URL_FIELDS = {"source_urls"}


class OutputValidationError(ValueError):
    pass


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/.,;").lower()


def redact_secrets(text: str, extra_secrets: list[str] | None = None) -> tuple[str, int]:
    count = 0
    for secret in extra_secrets or []:
        if secret and secret in text:
            count += text.count(secret)
            text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text, n = pattern.subn(REDACTED, text)
        count += n
    return text, count


def _clean_text(
    text: str, allowed: set[str], secrets: list[str], issues: dict[str, int]
) -> str:
    text, n = redact_secrets(text, secrets)
    issues["secrets_redacted"] += n

    def _check_link(match: re.Match[str]) -> str:
        if normalize_url(match.group(0)) in allowed:
            return match.group(0)
        issues["stray_links_removed"] += 1
        return LINK_REMOVED

    text = _URL.sub(_check_link, text)
    text, detections = neutralize(text)
    issues["injection_echo_removed"] += len(detections)
    return text


def _walk(
    node: Any, allowed: set[str], secrets: list[str], issues: dict[str, int], key: str = ""
) -> Any:
    if isinstance(node, dict):
        return {k: _walk(v, allowed, secrets, issues, k) for k, v in node.items()}
    if isinstance(node, list):
        if key in _URL_FIELDS:
            valid = [u for u in node if isinstance(u, str) and normalize_url(u) in allowed]
            issues["ungrounded_citations_removed"] += len(node) - len(valid)
            return valid
        return [_walk(v, allowed, secrets, issues, key) for v in node]
    if isinstance(node, str):
        return _clean_text(node, allowed, secrets, issues)
    return node


def validate_analysis(
    analysis: A,
    allowed_urls: set[str] | list[str],
    secrets: list[str] | None = None,
    agent: str = "",
) -> tuple[A, dict[str, int]]:
    """Clean an analysis and return it with a count of what was fixed."""
    allowed = {normalize_url(u) for u in allowed_urls}
    issues = {
        "secrets_redacted": 0,
        "stray_links_removed": 0,
        "ungrounded_citations_removed": 0,
        "injection_echo_removed": 0,
        "facts_downgraded": 0,
    }
    data = _walk(analysis.model_dump(), allowed, secrets or [], issues)

    # A "sourced fact" must keep at least one grounded citation.
    for finding in data.get("findings", []):
        if finding.get("kind") == "sourced_fact" and not finding.get("source_urls"):
            finding["kind"] = "hypothesis"
            finding["confidence"] = "low"
            issues["facts_downgraded"] += 1

    cleaned = type(analysis).model_validate(data)

    if issues["secrets_redacted"]:
        audit.record(
            "secret_redacted_from_output",
            severity="high",
            agent=agent,
            detail={"count": issues["secrets_redacted"]},
        )
    if issues["stray_links_removed"] or issues["ungrounded_citations_removed"]:
        audit.record(
            "ungrounded_reference_removed",
            severity="medium",
            agent=agent,
            detail={
                "stray_links": issues["stray_links_removed"],
                "citations": issues["ungrounded_citations_removed"],
                "facts_downgraded": issues["facts_downgraded"],
            },
        )
    if issues["injection_echo_removed"]:
        audit.record(
            "instruction_like_text_in_output",
            severity="high",
            agent=agent,
            detail={"count": issues["injection_echo_removed"]},
        )
    return cleaned, issues


def validate_agent_result(
    raw: Any, expected_skill: str, secrets: list[str] | None = None
) -> AgentResult:
    """Validate a remote agent's response. Raises `OutputValidationError`."""
    if not isinstance(raw, dict):
        raise OutputValidationError("agent result is not a JSON object")
    if len(json.dumps(raw, default=str)) > MAX_RESULT_BYTES:
        raise OutputValidationError("agent result exceeds the size limit")
    try:
        result = AgentResult.model_validate(raw)
    except ValidationError as exc:
        raise OutputValidationError(f"agent result failed schema validation: {exc}") from exc
    if result.skill != expected_skill:
        raise OutputValidationError(
            f"agent answered for skill '{result.skill}', expected '{expected_skill}'"
        )
    model = ANALYSIS_MODELS[expected_skill]
    try:
        analysis = model.model_validate(result.analysis)
    except ValidationError as exc:
        raise OutputValidationError(f"analysis failed schema validation: {exc}") from exc

    # Re-check on the orchestrator side: do not assume the remote agent did.
    allowed = {s.url for s in result.sources}
    cleaned, _issues = validate_analysis(
        analysis, allowed, secrets=secrets, agent=f"orchestrator<-{result.agent}"
    )
    result.analysis = cleaned.model_dump()
    return result
