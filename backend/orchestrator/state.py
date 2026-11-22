"""Typed contracts shared across NexusAI.

Three groups live here:

* Analysis models   - what each agent's LLM must produce (the output contract).
* Wire models       - what travels between orchestrator and agents
                      (`AgentTaskInput` in, `AgentResult` out).
* Report models     - the plan, per-task records and the final report.

What is shared vs agent-local: only these contracts are shared. Prompts,
retrieved page text and tool sessions stay inside the agent that owns them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]

SKILL_MARKET = "market_research"
SKILL_COMPETITOR = "competitor_analysis"
SKILL_CUSTOMER = "customer_insights"
SKILL_STRATEGY = "business_strategy"
RESEARCH_SKILLS = [SKILL_MARKET, SKILL_COMPETITOR, SKILL_CUSTOMER]
ALL_SKILLS = [*RESEARCH_SKILLS, SKILL_STRATEGY]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------- evidence
class Source(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    server: str = Field(default="", description="MCP server that returned it")
    tool: str = Field(default="", description="MCP tool that returned it")


class Finding(BaseModel):
    claim: str = Field(description="One specific, self-contained statement.")
    kind: Literal["sourced_fact", "hypothesis"] = Field(
        default="hypothesis",
        description="sourced_fact only if a listed source directly supports it; "
        "otherwise hypothesis.",
    )
    source_urls: list[str] = Field(
        default_factory=list,
        description="URLs copied exactly from the evidence list. Empty for hypotheses.",
    )
    confidence: Confidence = "medium"


# -------------------------------------------------------- analysis models
class BaseAnalysis(BaseModel):
    summary: str = Field(description="3-5 sentence overview of what was found.")
    findings: list[Finding] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Things taken as given without evidence."
    )
    limitations: list[str] = Field(
        default_factory=list, description="Gaps, weak evidence, things not checked."
    )


class SizingInputs(BaseModel):
    """Numbers for a top-down market size. The arithmetic is done by the
    Market Calculator MCP tool, never by the model."""

    total_addressable_units: float = Field(description="e.g. number of companies")
    target_share_pct: float = Field(ge=0, le=100)
    adoption_pct: float = Field(ge=0, le=100)
    annual_revenue_per_unit: float = Field(ge=0)
    currency: str = "USD"
    basis: str = Field(default="", description="Where each number came from.")


class MarketAnalysis(BaseAnalysis):
    trends: list[str] = Field(default_factory=list)
    demand_signals: list[str] = Field(default_factory=list)
    sizing_inputs: SizingInputs | None = Field(
        default=None,
        description="Only fill this in if the evidence gives usable numbers.",
    )


class CompetitorProfile(BaseModel):
    name: str
    positioning: str = ""
    target_customers: str = ""
    differentiators: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class CompetitorAnalysis(BaseAnalysis):
    competitors: list[CompetitorProfile] = Field(default_factory=list)


class CustomerSegment(BaseModel):
    name: str
    pain_points: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    evidence_kind: Literal["sourced_fact", "hypothesis"] = "hypothesis"


class CustomerAnalysis(BaseAnalysis):
    segments: list[CustomerSegment] = Field(default_factory=list)


class Recommendation(BaseModel):
    title: str
    rationale: str = Field(description="Why, tied to specific findings.")
    priority: Literal["high", "medium", "low"] = "medium"


class StrategyAnalysis(BaseAnalysis):
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    positioning: str = ""
    recommendations: list[Recommendation] = Field(default_factory=list)


ANALYSIS_MODELS: dict[str, type[BaseAnalysis]] = {
    SKILL_MARKET: MarketAnalysis,
    SKILL_COMPETITOR: CompetitorAnalysis,
    SKILL_CUSTOMER: CustomerAnalysis,
    SKILL_STRATEGY: StrategyAnalysis,
}


# ------------------------------------------------------------ wire models
class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.llm_calls += other.llm_calls


class ToolCallRecord(BaseModel):
    agent: str = ""
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    cached: bool = False
    attempts: int = 1
    latency_ms: float = 0.0
    error: str | None = None


class SecurityEvent(BaseModel):
    event_type: str
    severity: Literal["info", "low", "medium", "high"] = "medium"
    agent: str = ""
    request_id: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow)


class Calculation(BaseModel):
    tool: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class AgentTaskInput(BaseModel):
    """Payload the orchestrator sends to an agent (as an A2A data part)."""

    request_id: str = ""
    query: str = Field(min_length=3, max_length=4000)
    topic: str = Field(default="", max_length=300)
    focus_questions: list[str] = Field(default_factory=list, max_length=8)
    upstream: dict[str, Any] = Field(
        default_factory=dict,
        description="Other agents' results, for the Strategy Agent only.",
    )


class AgentResult(BaseModel):
    """Payload an agent returns (as an A2A artifact)."""

    agent: str
    skill: str
    analysis: dict[str, Any]
    sources: list[Source] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    security_events: list[SecurityEvent] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""
    llm_mode: str = "offline"
    latency_ms: float = 0.0


# ---------------------------------------------------------- report models
class PlannedTask(BaseModel):
    skill: str
    instruction: str
    focus_questions: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    topic: str
    tasks: list[PlannedTask]
    planner: Literal["llm", "template"] = "template"
    rationale: str = ""


class TaskRecord(BaseModel):
    skill: str
    agent_name: str = ""
    agent_url: str = ""
    transport: Literal["a2a", "local"] = "a2a"
    a2a_task_id: str = ""
    state: str = "submitted"
    lifecycle: list[str] = Field(default_factory=list)
    attempts: int = 0
    latency_ms: float = 0.0
    error: str | None = None
    degraded: bool = False
    usage: Usage = Field(default_factory=Usage)
    started_at: str = Field(default_factory=utcnow)


class FinalReport(BaseModel):
    request_id: str
    query: str
    topic: str = ""
    created_at: str = Field(default_factory=utcnow)
    market: MarketAnalysis | None = None
    competitors: CompetitorAnalysis | None = None
    customers: CustomerAnalysis | None = None
    strategy: StrategyAnalysis | None = None
    calculations: list[Calculation] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"
    confidence_reason: str = ""
    degraded: bool = False
    missing_sections: list[str] = Field(default_factory=list)
    tasks: list[TaskRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    security_events: list[SecurityEvent] = Field(default_factory=list)
    spans: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
