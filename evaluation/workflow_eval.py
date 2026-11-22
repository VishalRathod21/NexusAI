"""End-to-end evaluation: full workflow quality, failure recovery, and a
single-agent baseline.

    python -m evaluation.workflow_eval
    python -m evaluation.workflow_eval --web ddgs

Part 1 - workflow: run every question through plan -> agents -> report.
Part 2 - failure scenarios: inject one fault at a time and check the system
         does what it promises (retry, isolate, degrade, flag). "Recovered"
         means the expected handling happened, not that the fault vanished.
Part 3 - baseline: one generalist agent with every tool and a single LLM call,
         for comparison with the multi-agent design.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from evaluation.common import (
    eval_settings,
    in_process_servers,
    load_questions,
    parse_args,
    print_table,
    rate,
    reset_cache,
    save,
    setup,
)


# ------------------------------------------------------------------ workflow
async def evaluate_workflow(questions: list[dict[str, Any]], settings: Any, servers: dict[str, Any]) -> dict[str, Any]:
    from backend.orchestrator.delegation import LocalDelegator
    from backend.orchestrator.orchestrator import Orchestrator, build_local_agents

    rows = []
    for question in questions:
        reset_cache()
        orchestrator = Orchestrator(settings, delegator=LocalDelegator(build_local_agents(settings, servers)))
        started = time.perf_counter()
        row: dict[str, Any] = {"question": question["id"]}
        try:
            plan, _ = await orchestrator.plan(question["query"])
            report = await orchestrator.execute(question["id"], question["query"], plan)
            findings = [
                f for s in (report.market, report.competitors, report.customers) if s for f in s.findings
            ]
            row.update(
                completed=True,
                complete_report=not report.missing_sections,
                degraded=report.degraded,
                confidence=report.confidence,
                tasks_total=len(report.tasks),
                tasks_completed=sum(1 for t in report.tasks if t.state == "completed"),
                findings=len(findings),
                sourced_findings=sum(1 for f in findings if f.kind == "sourced_fact" and f.source_urls),
                sources=len(report.sources),
                tool_calls=report.metrics["tool_calls_total"],
                tokens=report.metrics["input_tokens"] + report.metrics["output_tokens"],
                cost_usd=report.metrics["estimated_cost_usd"],
                security_events=len(report.security_events),
            )
        except Exception as exc:
            row.update(completed=False, error=f"{type(exc).__name__}: {exc}"[:200])
        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        rows.append(row)

    done = [r for r in rows if r.get("completed")]
    total = lambda key: sum(r.get(key) or 0 for r in done)  # noqa: E731
    costs = [r["cost_usd"] for r in done if r.get("cost_usd") is not None]
    latencies = sorted(r["latency_ms"] for r in rows)
    return {
        "n": len(rows),
        "report_completion_rate": rate(len(done), len(rows)),
        "complete_report_rate": rate(sum(1 for r in done if r["complete_report"]), len(rows)),
        "agent_task_completion_rate": rate(total("tasks_completed"), total("tasks_total")),
        "citation_coverage": rate(total("sourced_findings"), total("findings")),
        "avg_sources_per_report": round(total("sources") / len(done), 1) if done else None,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
        "max_latency_ms": latencies[-1],
        "avg_tokens_per_report": round(total("tokens") / len(done), 1) if done else None,
        "avg_cost_per_report_usd": round(sum(costs) / len(costs), 6) if costs else "not configured",
        "rows": rows,
    }


# ---------------------------------------------------------- failure scenarios
class FaultyDelegator:
    """Wraps a delegator and injects one kind of fault for one skill."""

    def __init__(self, inner: Any, skill: str, fault: str):
        self.inner, self.skill, self.fault = inner, skill, fault
        self.transport = inner.transport
        self.calls = 0

    async def discover(self) -> dict[str, Any]:
        return await self.inner.discover()

    async def close(self) -> None:
        await self.inner.close()

    async def delegate(self, skill: str, task: Any, timeout: float) -> Any:
        from backend.orchestrator.delegation import AgentUnavailable

        if skill != self.skill:
            return await self.inner.delegate(skill, task, timeout)
        self.calls += 1
        if self.fault == "unavailable":
            raise AgentUnavailable("connection refused (simulated)")
        if self.fault == "flaky" and self.calls == 1:
            raise AgentUnavailable("temporary network error (simulated)")
        delegation = await self.inner.delegate(skill, task, timeout)
        if self.fault == "malformed":
            delegation.data = {"agent": "x", "skill": skill, "analysis": {"findings": "not a list"}}
        return delegation


async def failure_scenarios(servers: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic: always offline LLM + fixture corpus, so results are stable."""
    from agents.customer_insights.agent import CustomerInsightsAgent
    from backend.auth import AuthService
    from backend.llm.provider import LLMClient, LLMError
    from backend.orchestrator.delegation import LocalDelegator
    from backend.orchestrator.orchestrator import Orchestrator, build_local_agents
    from backend.orchestrator.state import AgentTaskInput
    from backend.service import ResearchService
    from database.connection import Database
    from database.repository import Repository

    settings = eval_settings(llm_provider="offline", web_search_backend="fixture", max_retries=1)
    query = load_questions("fixture")[0]["query"]

    async def run_with(fault_skill: str, fault: str):
        inner = LocalDelegator(build_local_agents(settings, servers))
        delegator = FaultyDelegator(inner, fault_skill, fault)
        orchestrator = Orchestrator(settings, delegator=delegator)
        plan, _ = await orchestrator.plan(query)
        report = await orchestrator.execute(f"fault-{fault}", query, plan)
        return report, next(t for t in report.tasks if t.skill == fault_skill)

    scenarios: list[dict[str, Any]] = []

    def record(name: str, expected: str, checks: dict[str, bool]) -> None:
        scenarios.append({"scenario": name, "expected": expected, "checks": checks, "recovered": all(checks.values())})

    report, task = await run_with("competitor_analysis", "unavailable")
    record("agent_unavailable", "retry, then a degraded report naming the missing section", {
        "retried": task.attempts == 2,
        "other_agents_completed": sum(1 for t in report.tasks if t.state == "completed") == 3,
        "missing_section_named": report.missing_sections == ["competitor_analysis"],
        "confidence_lowered": report.confidence == "low" and report.degraded,
    })

    report, task = await run_with("customer_insights", "flaky")
    record("transient_agent_failure", "retry succeeds, report complete", {
        "recovered_on_retry": task.state == "completed" and task.attempts == 2,
        "report_complete": not report.missing_sections,
    })

    report, task = await run_with("market_research", "malformed")
    record("malformed_agent_output", "rejected by validation, audited, section marked missing", {
        "rejected": task.state == "invalid_output",
        "not_in_report": report.market is None,
        "audited": any(e.event_type == "malformed_agent_response_rejected" for e in report.security_events),
    })

    class TimeoutLLM(LLMClient):
        is_offline = False  # type: ignore[assignment]

        async def structured(self, system, user, schema):
            raise LLMError("LLM call exceeded timeout (simulated)")

    result = await CustomerInsightsAgent(llm=TimeoutLLM(settings), servers=servers, settings=settings).run(AgentTaskInput(query=query))
    record("llm_provider_timeout", "extractive fallback, flagged as degraded", {
        "answer_returned": bool(result.analysis["findings"]),
        "flagged_degraded": result.degraded,
        "limitation_stated": any("fallback" in x for x in result.analysis["limitations"]),
    })

    result = await CustomerInsightsAgent(servers={}, settings=settings).run(AgentTaskInput(query=query))
    record("mcp_server_error", "agent survives, reports no evidence", {
        "no_crash": True,
        "flagged_degraded": result.degraded,
        "failures_recorded": bool(result.tool_calls) and all(not c.ok for c in result.tool_calls),
    })

    orchestrator = Orchestrator(settings, delegator=LocalDelegator(build_local_agents(settings, servers)))
    off_topic = "Assess the market for deep sea mineral extraction robots near Antarctica."
    plan, _ = await orchestrator.plan(off_topic)
    report = await orchestrator.execute("fault-incomplete", off_topic, plan)
    record("incomplete_research", "no invented findings; gap stated; low confidence", {
        "no_fabricated_findings": not (report.customers and report.customers.findings),
        "gap_stated": any("No evidence" in x for x in report.limitations),
        "confidence_low": report.confidence == "low",
    })

    report, _task = await run_with("business_strategy", "none")
    dumped = report.model_dump_json(exclude={"security_events"})
    record("retrieved_malicious_content", "instructions removed, event logged, no attacker link", {
        "event_logged": any(e.event_type == "indirect_prompt_injection_detected" for e in report.security_events),
        "attacker_link_absent": "attacker.example" not in dumped,
        "instruction_absent": "Ignore all previous" not in dumped,
    })

    db = Database("sqlite+aiosqlite:///:memory:")
    await db.init()
    repository = Repository(db)
    service = ResearchService(settings, repository, orchestrator)
    user, _session = await AuthService(settings, repository).signup(
        "eval@example.com", "Eval", "evaluation-only-password"
    )
    first, _ = await service.submit(user, query)
    second, duplicate = await service.submit(user, query)
    await service.wait_idle()
    record("duplicate_task_submission", "second submission returns the first request", {
        "same_request_returned": duplicate and first["id"] == second["id"],
        "ran_once": len(await service.list(user)) == 1,
    })
    await db.close()
    return scenarios


# ------------------------------------------------------------------ baseline
async def evaluate_baseline(questions: list[dict[str, Any]], settings: Any, servers: dict[str, Any]) -> dict[str, Any]:
    """One generalist agent: all tools, one prompt, one LLM call."""
    from agents.base import ResearchAgent, derive_topic
    from backend.orchestrator.state import AgentTaskInput, BaseAnalysis, StrategyAnalysis
    from security.permissions import TOOL_POLICY

    class GeneralistAgent(ResearchAgent):
        name = "baseline_generalist_agent"
        skill = "business_strategy"
        display_name = "Single-agent baseline"
        description = "Does market, competitor, customer and strategy work in one pass."
        analysis_model = StrategyAnalysis
        max_evidence_items = 12
        role_prompt = (
            "You are a business analyst. From the evidence, cover market trends, competitors and "
            "customer pain points as findings, then give opportunities, risks, positioning and "
            "recommendations."
        )

        async def gather(self, ctx):
            queries = [
                f"{ctx.topic} market size growth",
                f"{ctx.topic} top competitors",
                f"{ctx.topic} customer pain points challenges",
            ]
            return await self.web_evidence(ctx, queries, max_pages=4, max_hits=12)

        def offline_analysis(self, ctx, evidence) -> BaseAnalysis:
            return StrategyAnalysis(
                summary=self.offline_summary(ctx, evidence),
                findings=self.extractive_findings(evidence, limit=12),
                limitations=[self.OFFLINE_LIMITATION],
            )

    # The baseline deliberately gets broad access: that is part of the comparison.
    TOOL_POLICY[GeneralistAgent.name] = {"web_research": frozenset({"web_search", "fetch_page"})}
    rows = []
    for question in questions:
        reset_cache()
        started = time.perf_counter()
        result = await GeneralistAgent(servers=servers, settings=settings).run(
            AgentTaskInput(query=question["query"], topic=derive_topic(question["query"]))
        )
        findings = result.analysis["findings"]
        rows.append({
            "question": question["id"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "findings": len(findings),
            "sourced_findings": sum(1 for f in findings if f["kind"] == "sourced_fact" and f["source_urls"]),
            "sources": len(result.sources),
            "tokens": result.usage.input_tokens + result.usage.output_tokens,
            "tool_calls": len(result.tool_calls),
        })
    total = lambda key: sum(r[key] for r in rows)  # noqa: E731
    return {
        "n": len(rows),
        "citation_coverage": rate(total("sourced_findings"), total("findings")),
        "avg_sources_per_report": round(total("sources") / len(rows), 1),
        "avg_latency_ms": round(total("latency_ms") / len(rows), 1),
        "avg_tokens_per_report": round(total("tokens") / len(rows), 1),
        "avg_tool_calls": round(total("tool_calls") / len(rows), 1),
        "rows": rows,
    }


async def run(web: str, limit: int) -> dict[str, Any]:
    settings = eval_settings(web_search_backend=web)
    servers = in_process_servers()
    questions = load_questions(web, limit)
    workflow = await evaluate_workflow(questions, settings, servers)
    baseline = await evaluate_baseline(questions, settings, servers)
    scenarios = await failure_scenarios(servers)
    return {
        "llm": f"{settings.llm_provider}:{settings.resolved_model}",
        "web_backend": web,
        "workflow": workflow,
        "single_agent_baseline": baseline,
        "failure_scenarios": scenarios,
        "failure_recovery_rate": rate(sum(1 for s in scenarios if s["recovered"]), len(scenarios)),
    }


def main() -> None:
    args = parse_args(__doc__.split("\n")[0])
    setup(args.web)
    results = asyncio.run(run(args.web, args.limit))

    workflow, baseline = results["workflow"], results["single_agent_baseline"]
    print_table(f"Multi-agent workflow (n={workflow['n']})", [(k, v) for k, v in workflow.items() if k not in ("rows", "n")])
    print_table(f"Single-agent baseline (n={baseline['n']})", [(k, v) for k, v in baseline.items() if k not in ("rows", "n")])
    print_table(
        f"Failure scenarios (n={len(results['failure_scenarios'])})",
        [(s["scenario"], "handled as expected" if s["recovered"] else f"NOT handled: {s['checks']}") for s in results["failure_scenarios"]],
    )
    print(f"\nFailure recovery rate: {results['failure_recovery_rate']}")
    print(f"LLM: {results['llm']} | web: {results['web_backend']}")
    if results["llm"].startswith("offline"):
        print("Note: in offline mode the workflow/baseline comparison only shows latency and "
              "tool usage. Quality differences need a real LLM_PROVIDER.")
    print(f"Saved: {save('workflow_eval', results)}")


if __name__ == "__main__":
    main()
