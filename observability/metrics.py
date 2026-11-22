"""Latency, token and cost metrics.

Cost is only computed when model prices are configured. Prices change and
differ per model, so a built-in table would silently go stale; a missing
number is more honest than a wrong one.
"""

from __future__ import annotations

from typing import Any

from backend.config.settings import Settings
from backend.orchestrator.state import TaskRecord, ToolCallRecord, Usage


def estimate_cost(usage: Usage, settings: Settings) -> float | None:
    if settings.llm_price_input_per_1m <= 0 and settings.llm_price_output_per_1m <= 0:
        return None
    cost = (
        usage.input_tokens * settings.llm_price_input_per_1m
        + usage.output_tokens * settings.llm_price_output_per_1m
    ) / 1_000_000
    return round(cost, 6)


def summarize_run(
    tasks: list[TaskRecord],
    tool_calls: list[ToolCallRecord],
    total_latency_ms: float,
    settings: Settings,
) -> dict[str, Any]:
    """Metrics for one research run."""
    usage = Usage()
    for task in tasks:
        usage.add(task.usage)
    completed = [t for t in tasks if t.state == "completed"]
    ok_tools = [c for c in tool_calls if c.ok]
    cost = estimate_cost(usage, settings)
    return {
        "total_latency_ms": round(total_latency_ms, 1),
        "tasks_total": len(tasks),
        "tasks_completed": len(completed),
        "task_completion_rate": round(len(completed) / len(tasks), 3) if tasks else None,
        "task_attempts": sum(t.attempts for t in tasks),
        "per_agent_latency_ms": {t.skill: round(t.latency_ms, 1) for t in tasks},
        "tool_calls_total": len(tool_calls),
        "tool_calls_failed": len(tool_calls) - len(ok_tools),
        "tool_cache_hits": sum(1 for c in tool_calls if c.cached),
        "llm_calls": usage.llm_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "estimated_cost_usd": cost,
        "cost_note": None if cost is not None else "model prices not configured",
        "llm": f"{settings.llm_provider}:{settings.resolved_model}",
    }


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate metrics across stored reports (for the dashboard)."""
    runs = [r.get("metrics", {}) for r in reports if r.get("metrics")]
    if not runs:
        return {"reports": 0}
    latencies = sorted(m.get("total_latency_ms", 0) for m in runs)
    costs = [m["estimated_cost_usd"] for m in runs if m.get("estimated_cost_usd") is not None]
    tasks_total = sum(m.get("tasks_total", 0) for m in runs)
    tasks_done = sum(m.get("tasks_completed", 0) for m in runs)
    return {
        "reports": len(runs),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
        "p50_latency_ms": latencies[len(latencies) // 2],
        "max_latency_ms": latencies[-1],
        "task_completion_rate": round(tasks_done / tasks_total, 3) if tasks_total else None,
        "total_input_tokens": sum(m.get("input_tokens", 0) for m in runs),
        "total_output_tokens": sum(m.get("output_tokens", 0) for m in runs),
        "avg_cost_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "tool_calls_total": sum(m.get("tool_calls_total", 0) for m in runs),
        "tool_cache_hits": sum(m.get("tool_cache_hits", 0) for m in runs),
        "degraded_reports": sum(1 for r in reports if r.get("degraded")),
    }
