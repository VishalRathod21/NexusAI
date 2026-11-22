"""Security audit trail.

Every security-relevant decision (blocked input, injection detected in a web
page, denied tool call, redacted secret) becomes a `SecurityEvent`.

Events go to three places:
* an in-memory ring buffer (quick inspection, tests)
* a JSONL file (survives restarts, easy to grep)
* the active *collector*, if any: an agent collects the events raised during
  one task and returns them inside its `AgentResult`, so the orchestrator can
  persist events that happened in another process.
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.orchestrator.state import SecurityEvent

logger = logging.getLogger("nexusai.security")

_recent: deque[SecurityEvent] = deque(maxlen=500)
_collector: contextvars.ContextVar[list[SecurityEvent] | None] = contextvars.ContextVar(
    "security_event_collector", default=None
)
_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "security_event_context", default={}
)


def _write_jsonl(event: SecurityEvent) -> None:
    try:
        from backend.config.settings import get_settings

        path = Path(get_settings().audit_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump()) + "\n")
    except OSError as exc:  # auditing must never break the request
        logger.warning("could not write audit log: %s", exc)


def record(
    event_type: str,
    severity: str = "medium",
    detail: dict[str, Any] | None = None,
    agent: str = "",
    request_id: str = "",
) -> SecurityEvent:
    ctx = _context.get()
    event = SecurityEvent(
        event_type=event_type,
        severity=severity,  # type: ignore[arg-type]
        agent=agent or ctx.get("agent", ""),
        request_id=request_id or ctx.get("request_id", ""),
        detail=detail or {},
    )
    _recent.append(event)
    collector = _collector.get()
    if collector is not None:
        collector.append(event)
    _write_jsonl(event)
    logger.warning(
        "security event %s severity=%s agent=%s detail=%s",
        event.event_type,
        event.severity,
        event.agent,
        event.detail,
    )
    return event


@contextmanager
def collect(agent: str = "", request_id: str = "") -> Iterator[list[SecurityEvent]]:
    """Collect every event recorded inside the `with` block."""
    events: list[SecurityEvent] = []
    token_c = _collector.set(events)
    token_x = _context.set({"agent": agent, "request_id": request_id})
    try:
        yield events
    finally:
        _collector.reset(token_c)
        _context.reset(token_x)


def recent(limit: int = 100) -> list[SecurityEvent]:
    return list(_recent)[-limit:]


def clear() -> None:
    _recent.clear()
