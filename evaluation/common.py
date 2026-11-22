"""Shared helpers for the evaluation scripts.

Evaluation runs in-process (real MCP servers and real agents, no sockets), so a
run is reproducible and needs no services started. The LLM comes from `.env`:
offline gives a wiring/robustness baseline, a real provider gives quality
numbers. Every result file records which one was used and the sample size.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATASETS = Path(__file__).parent / "datasets"
RESULTS = Path(__file__).parent / "results"


def parse_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--web",
        choices=["fixture", "ddgs"],
        default="fixture",
        help="fixture = bundled synthetic corpus (deterministic); ddgs = live web search",
    )
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions")
    return parser.parse_args()


def setup(web: str) -> None:
    """Must run before project modules are imported."""
    os.environ["WEB_SEARCH_BACKEND"] = web
    os.environ.setdefault("AUDIT_LOG_PATH", str(RESULTS / "audit.jsonl"))
    os.chdir(ROOT)
    logging.basicConfig(level=logging.ERROR)
    logging.disable(logging.WARNING)


def load_questions(web: str, limit: int = 0) -> list[dict[str, Any]]:
    questions = json.loads((DATASETS / "research_questions.json").read_text(encoding="utf-8"))["questions"]
    if web == "fixture":
        questions = [q for q in questions if q["fixture"]]
    return questions[:limit] if limit else questions


def in_process_servers() -> dict[str, Any]:
    from protocols.mcp.servers import business_data, market_calculator, web_research

    return {
        "web_research": web_research.mcp,
        "business_data": business_data.mcp,
        "market_calculator": market_calculator.mcp,
    }


def eval_settings(**overrides: Any):
    from backend.config.settings import Settings

    base = Settings()
    return base.model_copy(update={"retry_backoff_seconds": 0.1, **overrides})


def reset_cache() -> None:
    """Fresh tool cache, so one measured run cannot speed up the next."""
    from database.cache import ToolCache
    from protocols.mcp.clients import tool_client

    tool_client._shared_cache = ToolCache()


def rate(passed: int, total: int) -> float | None:
    return round(passed / total, 3) if total else None


def save(name: str, payload: dict[str, Any]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), **payload}
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def print_table(title: str, rows: list[tuple[str, Any]]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value}")
