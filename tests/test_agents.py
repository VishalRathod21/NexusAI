"""Agents: output contracts, grounding, fallback, and the local workflow."""

from __future__ import annotations

import pytest

from agents.base import derive_topic
from agents.competitor.agent import CompetitorAgent
from agents.customer_insights.agent import CustomerInsightsAgent
from agents.market_research.agent import MarketResearchAgent
from agents.strategy.agent import StrategyAgent
from backend.llm.provider import LLMClient, LLMError, extract_json
from backend.orchestrator.state import (
    ANALYSIS_MODELS,
    AgentTaskInput,
    Finding,
    MarketAnalysis,
    SizingInputs,
    Usage,
)
from tests.conftest import QUERY, FakeLLM


@pytest.mark.parametrize(
    "query,expected",
    [
        (QUERY, "AI-powered HR SaaS platform in India"),
        ("Analyze the market for electric scooters in Vietnam", "electric scooters in Vietnam"),
        ("We plan to open a specialty coffee chain in Pune.", "specialty coffee chain in Pune"),
    ],
)
def test_topic_is_derived_from_a_long_request(query, expected):
    assert derive_topic(query) == expected


@pytest.mark.parametrize("agent_class", [MarketResearchAgent, CompetitorAgent, CustomerInsightsAgent])
async def test_research_agent_returns_valid_grounded_output(agent_class, servers, settings):
    result = await agent_class(servers=servers, settings=settings).run(AgentTaskInput(query=QUERY))

    analysis = ANALYSIS_MODELS[result.skill].model_validate(result.analysis)  # contract holds
    assert analysis.summary and analysis.findings
    retrieved = {s.url for s in result.sources}
    for finding in analysis.findings:  # every citation points at something retrieved
        assert finding.source_urls and set(finding.source_urls) <= retrieved
    assert all(call.ok for call in result.tool_calls)
    assert any("fixture corpus" in note for note in analysis.limitations)  # honest about its data


async def test_competitor_agent_combines_web_and_dataset(servers, settings):
    result = await CompetitorAgent(servers=servers, settings=settings).run(AgentTaskInput(query=QUERY))
    names = {c["name"] for c in result.analysis["competitors"]}
    assert {"Keka", "Darwinbox"} <= names
    assert {s.server for s in result.sources} == {"web_research", "business_data"}


async def test_strategy_agent_uses_only_upstream_findings(servers, settings):
    market = await MarketResearchAgent(servers=servers, settings=settings).run(AgentTaskInput(query=QUERY))
    upstream = {
        "market_research": {"analysis": market.analysis, "sources": [s.model_dump() for s in market.sources]}
    }
    result = await StrategyAgent(servers=servers, settings=settings).run(
        AgentTaskInput(query=QUERY, upstream=upstream)
    )
    assert result.tool_calls == []  # no web access, by design
    assert result.analysis["recommendations"]
    assert any("competitor_analysis" in note for note in result.analysis["limitations"])
    assert {s.url for s in result.sources} == {s.url for s in market.sources}


async def test_llm_output_is_used_and_citations_are_checked(servers, settings):
    def model(_schema, prompt):
        assert "<<UNTRUSTED_DATA" in prompt  # evidence is fenced
        real = "https://fixtures.nexusai.local/market/india-hr-tech-overview"
        return MarketAnalysis(
            summary="Cloud HR adoption is growing among mid-sized firms.",
            trends=["Bundled HR and payroll"],
            findings=[
                Finding(claim="SMBs are moving to cloud HR.", kind="sourced_fact", source_urls=[real], confidence="high"),
                Finding(claim="Invented statistic.", kind="sourced_fact", source_urls=["https://invented.example/stat"]),
            ],
        )

    llm = FakeLLM(settings, model)
    result = await MarketResearchAgent(llm=llm, servers=servers, settings=settings).run(AgentTaskInput(query=QUERY))
    kinds = [f["kind"] for f in result.analysis["findings"]]
    assert kinds == ["sourced_fact", "hypothesis"]  # the invented citation was dropped
    assert result.usage.llm_calls == 1 and result.llm_mode == "fake:scripted"
    assert not result.degraded


async def test_market_sizing_arithmetic_is_done_by_the_calculator_tool(servers, settings):
    def model(_schema, _prompt):
        return MarketAnalysis(
            summary="Sizing inputs found.",
            sizing_inputs=SizingInputs(
                total_addressable_units=60000, target_share_pct=30, adoption_pct=5,
                annual_revenue_per_unit=1200, currency="USD", basis="fixture overview",
            ),
        )

    result = await MarketResearchAgent(
        llm=FakeLLM(settings, model), servers=servers, settings=settings
    ).run(AgentTaskInput(query=QUERY))
    assert result.calculations[0].result["som"] == 1_080_000
    assert any(c.server == "market_calculator" for c in result.tool_calls)


async def test_llm_failure_falls_back_to_extractive_answer(servers, settings):
    def broken(_schema, _prompt):
        raise LLMError("provider timeout")

    result = await MarketResearchAgent(
        llm=FakeLLM(settings, broken), servers=servers, settings=settings
    ).run(AgentTaskInput(query=QUERY))
    assert result.degraded and "provider timeout" in result.degraded_reason
    assert result.analysis["findings"]  # still useful
    assert any("fallback" in note for note in result.analysis["limitations"])


async def test_mcp_server_down_degrades_instead_of_crashing(settings):
    result = await CustomerInsightsAgent(servers={}, settings=settings).run(AgentTaskInput(query=QUERY))
    assert result.degraded and result.degraded_reason == "No evidence could be retrieved."
    assert all(not call.ok for call in result.tool_calls)


async def test_llm_client_retries_then_recovers_from_malformed_output(settings):
    class Flaky(LLMClient):
        attempts = 0

        @property
        def is_offline(self):
            return False

        async def _invoke_tool_calling(self, messages, schema):
            self.attempts += 1
            raise ValueError("structured output not parsed")

        async def _invoke_json_text(self, messages, schema):
            self.attempts += 1
            return schema.model_validate(extract_json('```json\n{"summary": "ok"}\n```')), Usage(llm_calls=1)

    client = Flaky(settings)  # llm_max_retries=1 -> 2 attempts, the last one in JSON-text mode
    parsed, usage = await client.structured("sys", "user", MarketAnalysis)
    assert parsed.summary == "ok" and client.attempts == 2 and usage.llm_calls == 2


async def test_llm_client_timeout_is_bounded(settings):
    import asyncio

    class Slow(LLMClient):
        @property
        def is_offline(self):
            return False

        async def _invoke_tool_calling(self, messages, schema):
            await asyncio.sleep(5)

        _invoke_json_text = _invoke_tool_calling

    slow_settings = settings.model_copy(update={"llm_timeout_seconds": 0.05})
    with pytest.raises(LLMError, match="after 2 attempts"):
        await Slow(slow_settings).structured("sys", "user", MarketAnalysis)


async def test_local_workflow_produces_a_complete_report(local_orchestrator):
    plan, _ = await local_orchestrator.plan(QUERY)
    assert [t.skill for t in plan.tasks][-1] == "business_strategy"
    assert plan.tasks[-1].depends_on == ["market_research", "competitor_analysis", "customer_insights"]

    report = await local_orchestrator.execute("req-local", QUERY, plan)
    assert report.market and report.competitors and report.customers and report.strategy
    assert report.missing_sections == [] and not report.degraded
    assert [t.state for t in report.tasks] == ["completed"] * 4
    assert report.metrics["task_completion_rate"] == 1.0
    assert len({s.url for s in report.sources}) == len(report.sources)  # de-duplicated
    assert report.confidence == "low"  # offline mode never claims more
    assert {"delegate.market_research", "mcp.web_research.web_search"} <= {s["name"] for s in report.spans}


async def test_llm_client_works_with_a_real_langchain_chat_model(settings):
    """Exercises the real LangChain call path. The fake model has no native
    structured output, so the client must fall back to JSON-text mode."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    model = FakeListChatModel(responses=['Here you go:\n```json\n{"summary": "Market is growing."}\n```'])
    client = LLMClient(settings.model_copy(update={"llm_provider": "openai"}), model=model)
    assert not client.is_offline
    parsed, usage = await client.structured("system", "user", MarketAnalysis)
    assert parsed.summary == "Market is growing." and usage.llm_calls == 2
