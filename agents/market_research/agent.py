"""Market Research Agent: trends, demand signals and market context."""

from __future__ import annotations

from backend.orchestrator.state import (
    SKILL_MARKET,
    BaseAnalysis,
    Calculation,
    MarketAnalysis,
)
from security.permissions import AGENT_MARKET

from agents.base import Evidence, ResearchAgent, RunContext


class MarketResearchAgent(ResearchAgent):
    name = AGENT_MARKET
    skill = SKILL_MARKET
    display_name = "Market Research Agent"
    description = (
        "Researches market trends, demand signals and market context for a business "
        "idea, and returns structured findings with sources and stated uncertainty."
    )
    tags = ["market", "trends", "demand", "research"]
    examples = ["Analyse the market for an AI-powered HR SaaS platform in India."]
    analysis_model = MarketAnalysis
    role_prompt = (
        "You are the Market Research Agent of a business intelligence system. "
        "Your only job is market context: category trends, demand signals and how "
        "the market is evolving. Do not profile competitors or recommend strategy; "
        "other agents do that. If, and only if, the evidence states usable numbers, "
        "fill `sizing_inputs` so a calculator tool can compute market size; explain "
        "in `basis` where each number came from."
    )

    async def gather(self, ctx: RunContext) -> list[Evidence]:
        queries = [
            f"{ctx.topic} market size growth",
            f"{ctx.topic} industry trends",
            *ctx.task.focus_questions[:1],
        ]
        return await self.web_evidence(ctx, queries)

    def offline_analysis(self, ctx: RunContext, evidence: list[Evidence]) -> BaseAnalysis:
        return MarketAnalysis(
            summary=self.offline_summary(ctx, evidence),
            findings=self.extractive_findings(evidence),
            limitations=[self.OFFLINE_LIMITATION, "Trends and market size were not derived."],
        )

    async def post_process(self, ctx: RunContext, analysis: BaseAnalysis) -> list[Calculation]:
        """Arithmetic goes to the Market Calculator MCP tool, not the model."""
        sizing = getattr(analysis, "sizing_inputs", None)
        if sizing is None:
            return []
        inputs = sizing.model_dump(exclude={"basis"})
        result = await self.safe_call(ctx, "market_calculator", "market_size_top_down", inputs)
        if not isinstance(result, dict):
            analysis.limitations.append("Market size could not be calculated (calculator unavailable).")
            return []
        return [Calculation(tool="market_calculator.market_size_top_down", inputs=inputs, result=result)]
