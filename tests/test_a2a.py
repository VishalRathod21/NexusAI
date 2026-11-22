"""A2A: Agent Cards, discovery, task lifecycle, artifacts, and failures.

The agents here are the real A2A apps built with the official SDK. Only the
socket is replaced by an in-memory transport.
"""

from __future__ import annotations

import httpx
import pytest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

from backend.orchestrator.state import ALL_SKILLS
from protocols.a2a.client import A2AClient
from protocols.a2a.discovery import AgentRegistry
from tests.conftest import QUERY, RouterTransport


def a2a_client(apps) -> A2AClient:
    return A2AClient(http_client=httpx.AsyncClient(transport=RouterTransport(apps), timeout=30))


async def test_agent_card_is_served_at_the_well_known_path(agent_apps, settings):
    http = httpx.AsyncClient(transport=RouterTransport(agent_apps))
    response = await http.get(settings.competitor_agent_url + AGENT_CARD_WELL_KNOWN_PATH)
    card = response.json()
    assert card["name"] == "Competitor Agent"
    assert card["skills"][0]["id"] == "competitor_analysis"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["capabilities"]["streaming"] is True


async def test_discovery_indexes_agents_by_skill(agent_apps, settings):
    registry = AgentRegistry(a2a_client(agent_apps), settings.agent_urls())
    await registry.refresh()
    assert sorted(s for a in registry.agents for s in a.skills) == sorted(ALL_SKILLS)
    assert registry.find_by_skill("business_strategy").name == "Strategy Agent"
    assert registry.find_by_skill("does_not_exist") is None


async def test_unreachable_agent_is_skipped_not_fatal(agent_apps, settings):
    alive = {u: a for u, a in agent_apps.items() if u != settings.competitor_agent_url}
    registry = AgentRegistry(a2a_client(alive), settings.agent_urls())
    await registry.refresh()
    assert len(registry.agents) == 3
    assert list(registry.unreachable) == [settings.competitor_agent_url]


async def test_task_lifecycle_and_result_artifact(agent_apps, settings):
    client = a2a_client(agent_apps)
    card = await client.fetch_card(settings.market_agent_url)
    outcome = await client.send_task(card, {"request_id": "r1", "query": QUERY})

    assert outcome.lifecycle == ["submitted", "working", "completed"]
    assert outcome.task_id
    assert outcome.data["skill"] == "market_research"
    assert outcome.data["analysis"]["findings"]


async def test_invalid_task_input_is_rejected_by_the_agent(agent_apps, settings):
    client = a2a_client(agent_apps)
    card = await client.fetch_card(settings.market_agent_url)
    outcome = await client.send_task(card, {"query": ""})
    assert outcome.state == "rejected" and "invalid task input" in outcome.error
    assert outcome.data is None


async def test_agent_crash_becomes_a_failed_task(agent_apps, settings, monkeypatch):
    from agents.market_research.agent import MarketResearchAgent

    async def boom(self, ctx):
        raise RuntimeError("tool backend exploded")

    monkeypatch.setattr(MarketResearchAgent, "gather", boom)
    client = a2a_client(agent_apps)
    card = await client.fetch_card(settings.market_agent_url)
    outcome = await client.send_task(card, {"query": QUERY})
    assert outcome.state == "failed" and "tool backend exploded" in outcome.error


async def test_full_workflow_over_a2a(agent_apps, make_a2a_orchestrator):
    orchestrator = make_a2a_orchestrator(agent_apps)
    plan, _ = await orchestrator.plan(QUERY)
    seen = []

    async def on_task(record):
        seen.append(record.skill)

    report = await orchestrator.execute("req-a2a", QUERY, plan, on_task)

    assert report.missing_sections == []
    assert {t.transport for t in report.tasks} == {"a2a"}
    assert all(t.lifecycle == ["submitted", "working", "completed"] and t.a2a_task_id for t in report.tasks)
    assert seen[-1] == "business_strategy"  # synthesis runs after the research tasks
    assert report.strategy.recommendations
    # the injection in the fixture page was caught inside the competitor agent
    # process and reported back through the A2A artifact
    assert any(e.event_type == "indirect_prompt_injection_detected" for e in report.security_events)


async def test_stopped_agent_gives_a_degraded_report(agent_apps, make_a2a_orchestrator, settings):
    """Roadmap experiment: stop one agent and observe the orchestrator."""
    alive = {u: a for u, a in agent_apps.items() if u != settings.competitor_agent_url}
    orchestrator = make_a2a_orchestrator(alive)
    plan, _ = await orchestrator.plan(QUERY)
    report = await orchestrator.execute("req-degraded", QUERY, plan)

    by_skill = {t.skill: t for t in report.tasks}
    failed = by_skill["competitor_analysis"]
    assert failed.state == "unavailable" and failed.attempts == 2  # 1 + max_retries
    assert report.missing_sections == ["competitor_analysis"]
    assert report.degraded and report.confidence == "low"
    # fault isolation: everything else still completed
    assert by_skill["market_research"].state == by_skill["customer_insights"].state == "completed"
    assert report.strategy is not None
    assert any("competitor_analysis" in line for line in report.limitations)


async def test_transient_agent_failure_is_retried(agent_apps, make_a2a_orchestrator, monkeypatch):
    from agents.customer_insights.agent import CustomerInsightsAgent

    original = CustomerInsightsAgent.gather
    calls = {"n": 0}

    async def flaky(self, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary glitch")
        return await original(self, ctx)

    monkeypatch.setattr(CustomerInsightsAgent, "gather", flaky)
    orchestrator = make_a2a_orchestrator(agent_apps)
    plan, _ = await orchestrator.plan(QUERY)
    report = await orchestrator.execute("req-retry", QUERY, plan)

    record = next(t for t in report.tasks if t.skill == "customer_insights")
    assert record.state == "completed" and record.attempts == 2
    assert report.missing_sections == []


async def test_malformed_agent_response_is_not_trusted(agent_apps, make_a2a_orchestrator, monkeypatch):
    """An agent that returns the wrong shape must not corrupt the report."""
    from backend.orchestrator.state import AgentResult

    original = AgentResult.model_dump

    def corrupt(self, **kwargs):
        data = original(self, **kwargs)
        if self.skill == "market_research":
            data["analysis"] = {"findings": "this should be a list"}
        return data

    monkeypatch.setattr(AgentResult, "model_dump", corrupt)
    orchestrator = make_a2a_orchestrator(agent_apps)
    plan, _ = await orchestrator.plan(QUERY)
    report = await orchestrator.execute("req-malformed", QUERY, plan)

    record = next(t for t in report.tasks if t.skill == "market_research")
    assert record.state == "invalid_output" and record.attempts == 2
    assert report.market is None and "market_research" in report.missing_sections
    assert any(e.event_type == "malformed_agent_response_rejected" for e in report.security_events)
