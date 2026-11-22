"""Strategy Agent: turns the other agents' findings into opportunities, risks
and recommendations.

It does no web research. Its "evidence" is the output of the three research
agents, which is derived from untrusted web content and is therefore sanitised
and fenced like a web page. That is also why this agent has no web tools.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from backend.orchestrator.state import (
    RESEARCH_SKILLS,
    SKILL_STRATEGY,
    BaseAnalysis,
    Finding,
    Recommendation,
    Source,
    StrategyAnalysis,
)
from security.permissions import AGENT_STRATEGY

from agents.base import Evidence, ResearchAgent, RunContext

MAX_UPSTREAM_CHARS = 6000


class StrategyAgent(ResearchAgent):
    name = AGENT_STRATEGY
    skill = SKILL_STRATEGY
    display_name = "Strategy Agent"
    description = (
        "Synthesises market, competitor and customer findings into opportunities, "
        "risks, positioning and prioritised recommendations, keeping sourced facts "
        "separate from reasoned recommendations."
    )
    tags = ["strategy", "synthesis", "recommendations"]
    examples = ["Given these research findings, recommend a go-to-market strategy."]
    analysis_model = StrategyAnalysis
    max_evidence_chars = MAX_UPSTREAM_CHARS
    role_prompt = (
        "You are the Strategy Agent of a business intelligence system. You receive "
        "findings from the market, competitor and customer agents. Identify "
        "opportunities and risks, propose a positioning, and give prioritised "
        "recommendations. Each recommendation's rationale must refer to specific "
        "findings. In `findings`, restate only the facts your reasoning relies on, "
        "keeping their original source URLs; your own conclusions are `hypothesis`. "
        "If a research section is missing, say so in `limitations` and lower your "
        "confidence. This is decision support: never recommend that the system "
        "itself take purchases, payments or commitments."
    )

    # ---------------------------------------------------------- upstream
    @staticmethod
    def _upstream(ctx: RunContext, skill: str) -> dict[str, Any]:
        section = ctx.task.upstream.get(skill)
        return section if isinstance(section, dict) else {}

    def _upstream_sources(self, ctx: RunContext) -> list[Source]:
        sources: dict[str, Source] = {}
        for skill in RESEARCH_SKILLS:
            for raw in self._upstream(ctx, skill).get("sources") or []:
                try:
                    source = Source.model_validate(raw)
                except ValidationError:
                    continue
                sources.setdefault(source.url, source)
        return list(sources.values())

    async def gather(self, ctx: RunContext) -> list[Evidence]:
        evidence = []
        for skill in RESEARCH_SKILLS:
            analysis = self._upstream(ctx, skill).get("analysis")
            if not isinstance(analysis, dict):
                ctx.notes.append(f"The {skill} section was not available for synthesis.")
                continue
            evidence.append(
                Evidence(
                    source=Source(url=f"nexusai://upstream/{skill}", title=f"{skill} findings"),
                    content=json.dumps(analysis)[:MAX_UPSTREAM_CHARS],
                    data=analysis,
                )
            )
        return evidence

    def allowed_urls(self, ctx: RunContext, evidence: list[Evidence]) -> set[str]:
        return {s.url for s in self._upstream_sources(ctx)}

    def result_sources(self, ctx: RunContext, evidence: list[Evidence]) -> list[Source]:
        return self._upstream_sources(ctx)

    # ----------------------------------------------------------- offline
    def offline_analysis(self, ctx: RunContext, evidence: list[Evidence]) -> BaseAnalysis:
        by_skill = {e.source.url.rsplit("/", 1)[-1]: e.data or {} for e in evidence}
        competitors = by_skill.get("competitor_analysis", {}).get("competitors") or []
        names = [c.get("name") for c in competitors if isinstance(c, dict) and c.get("name")]

        carried: list[Finding] = []
        for data in by_skill.values():
            for raw in (data.get("findings") or [])[:2]:
                try:
                    carried.append(Finding.model_validate(raw))
                except ValidationError:
                    continue

        risks = []
        if names:
            risks.append(
                f"{len(names)} established competitors were identified "
                f"({', '.join(names[:5])}); differentiation is required."
            )
        missing = [s for s in RESEARCH_SKILLS if s not in by_skill]
        if missing:
            risks.append(f"Research is incomplete: missing {', '.join(missing)}.")

        return StrategyAnalysis(
            summary=(
                "Rule-based synthesis (no language model was used). It restates key "
                "upstream findings and lists generic next steps; it is not a tailored strategy."
            ),
            findings=carried,
            risks=risks,
            recommendations=[
                Recommendation(
                    title="Validate the reported customer pain points with direct interviews",
                    rationale="Customer findings come from secondary sources only.",
                    priority="high",
                ),
                Recommendation(
                    title="Pick one underserved segment before building broadly",
                    rationale="Competitor profiles show established vendors across segments."
                    if names
                    else "No competitor evidence was available to rule segments in or out.",
                    priority="medium",
                ),
                Recommendation(
                    title="Re-run this research with a language model enabled",
                    rationale="Opportunities and positioning need synthesis, which offline mode cannot do.",
                    priority="medium",
                ),
            ],
            limitations=[self.OFFLINE_LIMITATION, "Recommendations are generic, not derived."],
        )
