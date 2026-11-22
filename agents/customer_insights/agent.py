"""Customer Insights Agent: segments, pain points, needs and demand signals."""

from __future__ import annotations

from backend.orchestrator.state import SKILL_CUSTOMER, BaseAnalysis, CustomerAnalysis
from security.permissions import AGENT_CUSTOMER

from agents.base import Evidence, ResearchAgent, RunContext


class CustomerInsightsAgent(ResearchAgent):
    name = AGENT_CUSTOMER
    skill = SKILL_CUSTOMER
    display_name = "Customer Insights Agent"
    description = (
        "Identifies customer segments, their pain points and needs, and separates "
        "what the evidence shows from what is only a hypothesis."
    )
    tags = ["customers", "segments", "pain points", "demand"]
    examples = ["What do HR teams in Indian SMBs struggle with today?"]
    analysis_model = CustomerAnalysis
    role_prompt = (
        "You are the Customer Insights Agent of a business intelligence system. "
        "Identify customer segments and, for each, their pain points and needs. "
        "Set `evidence_kind` to `sourced_fact` only when the evidence explicitly "
        "reports that segment's problems; otherwise use `hypothesis`. Do not "
        "profile competitors or recommend strategy; other agents do that."
    )

    async def gather(self, ctx: RunContext) -> list[Evidence]:
        queries = [
            f"{ctx.topic} customer pain points challenges",
            f"{ctx.topic} user reviews complaints",
            *ctx.task.focus_questions[:1],
        ]
        return await self.web_evidence(ctx, queries)

    def offline_analysis(self, ctx: RunContext, evidence: list[Evidence]) -> BaseAnalysis:
        return CustomerAnalysis(
            summary=self.offline_summary(ctx, evidence),
            findings=self.extractive_findings(evidence),
            limitations=[self.OFFLINE_LIMITATION, "Customer segments were not derived."],
        )
