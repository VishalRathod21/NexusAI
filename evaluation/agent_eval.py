"""Agent-level evaluation: is each agent's own output any good?

    python -m evaluation.agent_eval                # fixture corpus
    python -m evaluation.agent_eval --web ddgs     # live web search

Scoring rules (defined before measuring):

* task completion        - the agent returned a result without raising
* structured validity    - the result parses into the agent's output contract
* citation coverage      - share of findings that are sourced facts with at
                           least one citation that was actually retrieved
* citation grounding     - share of cited URLs that are in the agent's sources
                           (should be 100%: output validation enforces it)
* source relevance       - share of sources whose title or snippet shares a
                           content word with the topic. A crude lexical proxy,
                           not a judgement of whether the source supports the claim.
* degraded rate          - share of runs that fell back or found no evidence
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from evaluation.common import (
    eval_settings,
    in_process_servers,
    load_questions,
    parse_args,
    print_table,
    rate,
    save,
    setup,
)

STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "into", "platform", "want", "launch"}


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z\-]{2,}", text.lower()) if w not in STOPWORDS}


def score_result(result: Any, topic: str) -> dict[str, Any]:
    from backend.orchestrator.state import ANALYSIS_MODELS
    from pydantic import ValidationError

    try:
        analysis = ANALYSIS_MODELS[result.skill].model_validate(result.analysis)
        valid = True
    except ValidationError:
        return {"valid": False}

    retrieved = {s.url for s in result.sources}
    findings = analysis.findings
    cited = [u for f in findings for u in f.source_urls]
    sourced = [f for f in findings if f.kind == "sourced_fact" and set(f.source_urls) & retrieved]
    topic_words = content_words(topic)
    relevant = [s for s in result.sources if content_words(f"{s.title} {s.snippet}") & topic_words]
    return {
        "valid": valid,
        "findings": len(findings),
        "sourced_findings": len(sourced),
        "cited_urls": len(cited),
        "grounded_urls": sum(1 for u in cited if u in retrieved),
        "sources": len(result.sources),
        "relevant_sources": len(relevant),
        "degraded": result.degraded,
        "tool_calls": len(result.tool_calls),
        "tool_failures": sum(1 for c in result.tool_calls if not c.ok),
        "tokens": result.usage.input_tokens + result.usage.output_tokens,
    }


async def run(web: str, limit: int) -> dict[str, Any]:
    from agents.base import derive_topic
    from agents.competitor.agent import CompetitorAgent
    from agents.customer_insights.agent import CustomerInsightsAgent
    from agents.market_research.agent import MarketResearchAgent
    from backend.orchestrator.state import AgentTaskInput

    settings = eval_settings(web_search_backend=web)
    servers = in_process_servers()
    questions = load_questions(web, limit)
    per_agent: dict[str, list[dict[str, Any]]] = {}

    for question in questions:
        topic = derive_topic(question["query"])
        for agent_class in (MarketResearchAgent, CompetitorAgent, CustomerInsightsAgent):
            agent = agent_class(servers=servers, settings=settings)
            started = time.perf_counter()
            row: dict[str, Any] = {"question": question["id"]}
            try:
                result = await agent.run(AgentTaskInput(query=question["query"], topic=topic))
                row.update(completed=True, **score_result(result, topic))
            except Exception as exc:
                row.update(completed=False, error=f"{type(exc).__name__}: {exc}"[:200])
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            per_agent.setdefault(agent.skill, []).append(row)

    summary = {}
    for skill, rows in per_agent.items():
        done = [r for r in rows if r.get("completed")]
        valid = [r for r in done if r.get("valid")]
        total = lambda key: sum(r.get(key, 0) for r in valid)  # noqa: E731
        summary[skill] = {
            "n": len(rows),
            "task_completion_rate": rate(len(done), len(rows)),
            "structured_output_validity": rate(len(valid), len(rows)),
            "citation_coverage": rate(total("sourced_findings"), total("findings")),
            "citation_grounding": rate(total("grounded_urls"), total("cited_urls")),
            "source_relevance": rate(total("relevant_sources"), total("sources")),
            "degraded_rate": rate(sum(1 for r in valid if r["degraded"]), len(valid)),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in rows) / len(rows), 1),
            "avg_tokens": round(total("tokens") / len(valid), 1) if valid else None,
        }
    return {
        "llm": f"{settings.llm_provider}:{settings.resolved_model}",
        "web_backend": web,
        "questions": len(questions),
        "summary": summary,
        "rows": per_agent,
    }


def main() -> None:
    args = parse_args(__doc__.split("\n")[0])
    setup(args.web)
    results = asyncio.run(run(args.web, args.limit))
    for skill, stats in results["summary"].items():
        print_table(f"{skill} (n={stats['n']})", [(k, v) for k, v in stats.items() if k != "n"])
    path = save("agent_eval", results)
    print(f"\nLLM: {results['llm']} | web: {results['web_backend']} | questions: {results['questions']}")
    if results["llm"].startswith("offline"):
        print("Note: offline mode extracts sentences, so citation metrics are trivially high. "
              "Use a real LLM_PROVIDER for meaningful quality numbers.")
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
