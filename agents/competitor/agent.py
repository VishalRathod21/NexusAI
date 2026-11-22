"""Competitor Agent: who the competitors are and how they position themselves."""

from __future__ import annotations

import json

from backend.orchestrator.state import (
    SKILL_COMPETITOR,
    BaseAnalysis,
    CompetitorAnalysis,
    CompetitorProfile,
    Source,
)
from security.permissions import AGENT_COMPETITOR

from agents.base import Evidence, ResearchAgent, RunContext


class CompetitorAgent(ResearchAgent):
    name = AGENT_COMPETITOR
    skill = SKILL_COMPETITOR
    display_name = "Competitor Agent"
    description = (
        "Identifies relevant competitors and compares their products, positioning, "
        "target customers and differentiators, with evidence for each profile."
    )
    tags = ["competitors", "positioning", "comparison"]
    examples = ["Who competes with an AI-powered HR SaaS platform in India?"]
    analysis_model = CompetitorAnalysis
    max_evidence_items = 10  # web pages plus dataset entries
    role_prompt = (
        "You are the Competitor Agent of a business intelligence system. Identify "
        "the most relevant competitors and build one profile per competitor: "
        "positioning, target customers and differentiators. Every profile must "
        "list the URLs that support it. Do not analyse market size or recommend "
        "strategy; other agents do that."
    )

    async def gather(self, ctx: RunContext) -> list[Evidence]:
        evidence = await self.web_evidence(
            ctx,
            [f"{ctx.topic} top competitors", f"{ctx.topic} alternatives comparison"],
            max_pages=2,
            max_hits=4,
        )
        # Second source: the curated dataset on the Business Data MCP server.
        dataset = await self.safe_call(
            ctx, "business_data", "search_companies", {"query": ctx.topic[:200], "limit": 6}
        )
        companies = dataset.get("companies") if isinstance(dataset, dict) else None
        for company in companies or []:
            if not isinstance(company, dict) or not company.get("website"):
                continue
            evidence.append(
                Evidence(
                    source=Source(
                        url=str(company["website"]),
                        title=f"{company.get('name', '')} (curated dataset entry)",
                        snippet=str(company.get("positioning", ""))[:300],
                        server="business_data",
                        tool="search_companies",
                    ),
                    content=json.dumps(company),
                    data=company,
                )
            )
        if companies:
            ctx.notes.append(
                "Curated dataset entries are a coarse snapshot and should be verified."
            )
        return evidence

    def offline_analysis(self, ctx: RunContext, evidence: list[Evidence]) -> BaseAnalysis:
        profiles = [
            CompetitorProfile(
                name=str(item.data.get("name", "")),
                positioning=str(item.data.get("positioning", "")),
                target_customers=str(item.data.get("target_customers", "")),
                source_urls=[item.source.url],
            )
            for item in evidence
            if item.data and item.data.get("name")
        ]
        web_only = [item for item in evidence if not item.data]
        return CompetitorAnalysis(
            summary=self.offline_summary(ctx, evidence),
            findings=self.extractive_findings(web_only),
            competitors=profiles,
            limitations=[
                self.OFFLINE_LIMITATION,
                "Competitor profiles come only from the curated dataset; differentiators were not derived.",
            ],
        )
