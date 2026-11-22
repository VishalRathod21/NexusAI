"""Execution tracing.

Two levels:

* Local spans (always on). `span("name")` records what ran, for how long and
  whether it failed. Spans are collected per request and stored with the
  report, so the dashboard can show a timeline without any external service.
* Langfuse (optional). If Langfuse keys are configured, every LLM call is also
  sent there through a LangChain callback, which gives prompt/response level
  traces and token counts.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from backend.config.settings import Settings

logger = logging.getLogger(__name__)

_spans: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "trace_spans", default=None
)
_trace_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "langfuse_trace_ctx", default=None
)
_langfuse_ready: bool | None = None


@contextmanager
def langfuse_context(**kwargs: Any) -> Iterator[dict[str, Any]]:
    """Context manager to attach Langfuse metadata to the current async context."""
    current = dict(_trace_ctx.get() or {})
    current.update({k: v for k, v in kwargs.items() if v is not None})
    token = _trace_ctx.set(current)
    try:
        yield current
    finally:
        _trace_ctx.reset(token)


def current_langfuse_context() -> dict[str, Any]:
    return dict(_trace_ctx.get() or {})


@contextmanager
def trace(request_id: str) -> Iterator[list[dict[str, Any]]]:
    """Start collecting spans for one request."""
    spans: list[dict[str, Any]] = []
    token = _spans.set(spans)
    try:
        yield spans
    finally:
        _spans.reset(token)
        logger.info("trace %s finished with %d spans", request_id, len(spans))


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Record one unit of work. Safe to use when no trace is active."""
    record: dict[str, Any] = {
        "name": name,
        "started_at": time.time(),
        "status": "ok",
        "attributes": dict(attributes),
    }
    start = time.perf_counter()
    try:
        yield record
    except BaseException as exc:
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        record["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        spans = _spans.get()
        if spans is not None:
            spans.append(record)


def langfuse_callbacks(settings: Settings) -> list[Any]:
    """LangChain callbacks for Langfuse, or [] when it is not configured."""
    global _langfuse_ready
    secret = settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else ""
    if not (settings.langfuse_public_key and secret):
        return []
    if _langfuse_ready is False:
        return []
    try:
        # The Langfuse SDK reads its credentials from the environment.
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
        os.environ.setdefault("LANGFUSE_SECRET_KEY", secret)
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
        from langfuse.langchain import CallbackHandler

        _langfuse_ready = True
        return [CallbackHandler()]
    except Exception as exc:  # tracing must never break a request
        _langfuse_ready = False
        logger.warning("Langfuse disabled: %s", exc)
        return []


def langfuse_config(
    settings: Settings,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    trace_name: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build LangChain / LangGraph run config with Langfuse callbacks and metadata."""
    callbacks = langfuse_callbacks(settings)
    if not callbacks:
        return {}
    meta: dict[str, Any] = dict(metadata or {})
    if session_id:
        meta["langfuse_session_id"] = str(session_id)
    if user_id:
        meta["langfuse_user_id"] = str(user_id)
    if trace_name:
        meta["langfuse_trace_name"] = str(trace_name)
    if tags:
        meta["langfuse_tags"] = list(tags)

    config: dict[str, Any] = {"callbacks": callbacks}
    if meta:
        config["metadata"] = meta
    if tags:
        config["tags"] = list(tags)
    return config


def langfuse_flush() -> None:
    """Flush any pending Langfuse events synchronously."""
    global _langfuse_ready
    if _langfuse_ready:
        try:
            from langfuse import get_client

            get_client().flush()
        except Exception as exc:
            logger.debug("Langfuse flush failed: %s", exc)
