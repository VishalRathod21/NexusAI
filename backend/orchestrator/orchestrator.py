"""The orchestrator: plan, delegate, track, validate, aggregate.

It owns the cross-cutting reliability rules so agents do not have to:

* timeout       - every delegated task has a deadline
* retry         - transient failures (agent unreachable, timeout, crashed task,
                  malformed output) are retried a bounded number of times with
                  backoff; a rejected task is not retried
* fault isolation - one agent failing does not fail the others
* graceful degradation - the report is still produced, with the missing
                  section named and confidence lowered
* validation    - whatever an agent returns is checked before it is used
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from backend.config.settings import Settings, get_settings
from backend.llm.provider import LLMClient
from backend.orchestrator import planner
from backend.orchestrator.delegation import (
    A2ADelegator,
    AgentTaskFailed,
    AgentUnavailable,
    Delegator,
    LocalDelegator,
)
from backend.orchestrator.state import (
    ANALYSIS_MODELS,
    RESEARCH_SKILLS,
    SKILL_COMPETITOR,
    SKILL_CUSTOMER,
    SKILL_MARKET,
    SKILL_STRATEGY,
    AgentResult,
    AgentTaskInput,
    FinalReport,
    ResearchPlan,
    Source,
    TaskRecord,
    Usage,
)
from observability import metrics
from observability.tracing import span, trace
from security import audit
from security.output_validation import OutputValidationError, validate_agent_result

logger = logging.getLogger(__name__)

TaskCallback = Callable[[TaskRecord], Awaitable[None]]
SECTION_FIELDS = {
    SKILL_MARKET: "market",
    SKILL_COMPETITOR: "competitors",
    SKILL_CUSTOMER: "customers",
    SKILL_STRATEGY: "strategy",
}


def build_local_agents(settings: Settings, servers: dict[str, Any] | None = None, llm: LLMClient | None = None):
    from agents.competitor.agent import CompetitorAgent
    from agents.customer_insights.agent import CustomerInsightsAgent
    from agents.market_research.agent import MarketResearchAgent
    from agents.strategy.agent import StrategyAgent

    kinds = (MarketResearchAgent, CompetitorAgent, CustomerInsightsAgent, StrategyAgent)
    return [kind(llm=llm, servers=servers, settings=settings) for kind in kinds]


class Orchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        delegator: Delegator | None = None,
        llm: LLMClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)
        if delegator is not None:
            self.delegator = delegator
        elif self.settings.orchestration_mode == "local":
            self.delegator = LocalDelegator(build_local_agents(self.settings))
        else:
            self.delegator = A2ADelegator(self.settings)

    async def close(self) -> None:
        await self.delegator.close()

    # ------------------------------------------------------------ planning
    async def plan(self, query: str) -> tuple[ResearchPlan, Usage]:
        with span("orchestrator.plan"):
            return await planner.create_plan(query, self.llm)

    async def discover(self) -> dict[str, Any]:
        with span("orchestrator.discover", transport=self.delegator.transport):
            return await self.delegator.discover()

    # ---------------------------------------------------------- delegation
    async def run_task(
        self,
        skill: str,
        task_input: AgentTaskInput,
        on_task: TaskCallback | None = None,
    ) -> tuple[AgentResult | None, TaskRecord]:
        """Delegate one task with timeout, bounded retries and output validation."""
        record = TaskRecord(skill=skill, transport=self.delegator.transport)  # type: ignore[arg-type]
        attempts = 1 + max(0, self.settings.max_retries)
        started = time.perf_counter()
        result: AgentResult | None = None

        for attempt in range(1, attempts + 1):
            record.attempts = attempt
            retryable = True
            try:
                with span(f"delegate.{skill}", attempt=attempt, transport=self.delegator.transport):
                    delegation = await self.delegator.delegate(
                        skill, task_input, self.settings.agent_timeout_seconds
                    )
                record.agent_name = delegation.agent_name
                record.agent_url = delegation.agent_url
                record.a2a_task_id = delegation.task_id
                record.lifecycle = delegation.lifecycle
                # The response crossed a trust boundary: validate before use.
                result = validate_agent_result(
                    delegation.data, skill, secrets=self.settings.secret_values()
                )
                record.state = "completed"
                record.error = None
                break
            except AgentUnavailable as exc:
                record.state, record.error = "unavailable", str(exc)
            except AgentTaskFailed as exc:
                record.state, record.error = "failed", str(exc)
                retryable = exc.retryable
            except OutputValidationError as exc:
                record.state, record.error = "invalid_output", str(exc)[:400]
                audit.record(
                    "malformed_agent_response_rejected",
                    severity="medium",
                    agent=record.agent_name or skill,
                    request_id=task_input.request_id,
                    detail={"skill": skill, "attempt": attempt, "error": str(exc)[:200]},
                )
            logger.warning("task %s attempt %d/%d: %s", skill, attempt, attempts, record.error)
            if not retryable or attempt == attempts:
                break
            await asyncio.sleep(self.settings.retry_backoff_seconds * attempt)

        record.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if result is not None:
            record.usage = result.usage
            record.degraded = result.degraded
        if on_task:
            await on_task(record)
        return result, record

    # ----------------------------------------------------------- execution
    async def execute(
        self,
        request_id: str,
        query: str,
        plan: ResearchPlan,
        on_task: TaskCallback | None = None,
        planning_usage: Usage | None = None,
        user_id: str | None = None,
    ) -> FinalReport:
        """Run the plan through the LangGraph workflow and build the report."""
        from backend.orchestrator.graph import build_graph
        from observability.tracing import langfuse_config

        started = time.perf_counter()
        with trace(request_id) as spans, audit.collect("orchestrator", request_id) as events:
            graph = build_graph(self, on_task)
            graph_config = langfuse_config(
                self.settings,
                session_id=request_id,
                user_id=user_id,
                trace_name="nexusai-research-workflow",
                tags=["research-workflow", self.settings.orchestration_mode, self.settings.llm_provider],
                metadata={
                    "topic": plan.topic,
                    "planner": plan.planner,
                    "request_id": request_id,
                },
            )
            final_state = await graph.ainvoke(
                {"request_id": request_id, "query": query, "plan": plan, "results": {}, "records": []},
                config=graph_config or None,
            )
            report: FinalReport = final_state["report"]
            report.spans = spans
            # Events raised on the orchestrator side (e.g. a rejected agent response).
            report.security_events = [*events, *report.security_events]
        total_ms = (time.perf_counter() - started) * 1000
        report.metrics = metrics.summarize_run(
            report.tasks, report.tool_calls, total_ms, self.settings
        )
        if planning_usage and planning_usage.llm_calls:
            report.metrics["planning_tokens"] = (
                planning_usage.input_tokens + planning_usage.output_tokens
            )
        return report

    # --------------------------------------------------------- aggregation
    def aggregate(
        self,
        request_id: str,
        query: str,
        plan: ResearchPlan,
        results: dict[str, AgentResult],
        records: list[TaskRecord],
    ) -> FinalReport:
        report = FinalReport(request_id=request_id, query=query, topic=plan.topic)
        order = {skill: i for i, skill in enumerate([*RESEARCH_SKILLS, SKILL_STRATEGY])}
        report.tasks = sorted(records, key=lambda r: order.get(r.skill, 99))

        sources: dict[str, Source] = {}
        for skill, field_name in SECTION_FIELDS.items():
            result = results.get(skill)
            if result is None:
                report.missing_sections.append(skill)
                continue
            analysis = ANALYSIS_MODELS[skill].model_validate(result.analysis)
            setattr(report, field_name, analysis)
            report.assumptions += [f"[{skill}] {a}" for a in analysis.assumptions]
            report.limitations += [f"[{skill}] {x}" for x in analysis.limitations]
            report.calculations += result.calculations
            report.tool_calls += result.tool_calls
            report.security_events += result.security_events
            if skill != SKILL_STRATEGY:  # strategy only re-lists upstream sources
                for source in result.sources:
                    sources.setdefault(source.url, source)
        report.sources = list(sources.values())

        for record in report.tasks:
            if record.state != "completed":
                report.limitations.append(
                    f"[{record.skill}] section unavailable after {record.attempts} "
                    f"attempt(s): {record.error}"
                )

        report.degraded = bool(report.missing_sections) or any(r.degraded for r in results.values())
        report.confidence, report.confidence_reason = self._confidence(report, results)
        return report

    @staticmethod
    def _confidence(report: FinalReport, results: dict[str, AgentResult]) -> tuple[str, str]:
        """A transparent rule, not a model's opinion of itself."""
        if report.missing_sections:
            return "low", f"Missing sections: {', '.join(report.missing_sections)}."
        if report.degraded:
            return "low", "At least one agent fell back to a degraded answer."
        if all(r.llm_mode == "offline" for r in results.values()):
            return "low", "Offline mode: findings are extracted, not synthesised."
        findings = [
            f
            for section in (report.market, report.competitors, report.customers)
            if section
            for f in section.findings
        ]
        if not findings:
            return "low", "The research agents returned no findings."
        sourced = sum(1 for f in findings if f.kind == "sourced_fact" and f.source_urls)
        share = sourced / len(findings)
        reason = f"{sourced} of {len(findings)} research findings cite a retrieved source."
        if share >= 0.7 and len(report.sources) >= 5:
            return "high", reason
        if share >= 0.4:
            return "medium", reason
        return "low", reason
