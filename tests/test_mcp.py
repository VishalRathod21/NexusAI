"""MCP: discovery, execution, validation, permissions-by-discovery, caching."""

from __future__ import annotations

import json

import pytest

from mcp import Client
from protocols.mcp.clients.tool_client import ToolClient, ToolError, ToolUnavailable
from protocols.mcp.servers import business_data
from security.permissions import AGENT_COMPETITOR, AGENT_MARKET, AGENT_STRATEGY


async def test_tool_discovery_lists_what_each_server_offers(servers, settings):
    client = ToolClient(AGENT_COMPETITOR, servers=servers, settings=settings)
    web = await client.discover("web_research")
    assert set(web) == {"web_search", "fetch_page"}
    assert web["web_search"]["input_schema"]["properties"]["query"]["minLength"] == 3


async def test_agent_only_sees_tools_it_is_permitted_to_use(servers, settings):
    competitor = await ToolClient(AGENT_COMPETITOR, servers=servers, settings=settings).discover_permitted()
    strategy = await ToolClient(AGENT_STRATEGY, servers=servers, settings=settings).discover_permitted()
    assert set(competitor) == {"web_research", "business_data"}
    assert "web_research" not in strategy
    assert {t["name"] for t in strategy["market_calculator"]} == {"percentage_change", "compare_values"}


async def test_calculator_returns_result_with_formula_and_steps(servers, settings):
    client = ToolClient(AGENT_MARKET, servers=servers, settings=settings)
    size = await client.call(
        "market_calculator",
        "market_size_top_down",
        {"total_addressable_units": 60000, "target_share_pct": 30, "adoption_pct": 5, "annual_revenue_per_unit": 1200},
    )
    assert (size["tam"], size["sam"], size["som"]) == (72_000_000, 21_600_000, 1_080_000)
    assert size["formula"] and len(size["steps"]) == 3

    growth = await client.call("market_calculator", "cagr", {"start_value": 100, "end_value": 200, "years": 3})
    assert growth["result_pct"] == pytest.approx(25.99, abs=0.01)


async def test_invalid_arguments_are_rejected_by_the_server(servers, settings):
    client = ToolClient(AGENT_MARKET, servers=servers, settings=settings)
    with pytest.raises(ToolError):
        await client.call("market_calculator", "market_size_top_down", {"total_addressable_units": -5,
                          "target_share_pct": 250, "adoption_pct": 5, "annual_revenue_per_unit": 1})
    assert client.records[-1].ok is False
    # A tool error is not retried: the same arguments would fail again.
    assert client.records[-1].attempts == 1


async def test_fetch_page_blocks_internal_addresses(servers, settings):
    client = ToolClient(AGENT_COMPETITOR, servers=servers, settings=settings)
    for url in ("http://127.0.0.1:8000/admin", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
        with pytest.raises(ToolError):
            await client.call("web_research", "fetch_page", {"url": url})


async def test_unknown_tool_and_unknown_server(servers, settings):
    client = ToolClient(AGENT_COMPETITOR, servers={"web_research": servers["web_research"]}, settings=settings)
    with pytest.raises(ToolUnavailable):
        await client.call("business_data", "search_companies", {"query": "payroll"})


async def test_repeated_call_is_served_from_cache(servers, settings):
    client = ToolClient(AGENT_COMPETITOR, servers=servers, settings=settings)
    args = {"query": "HR SaaS India competitors", "max_results": 3}
    first = await client.call("web_research", "web_search", args)
    second = await client.call("web_research", "web_search", args)
    assert first == second
    assert [r.cached for r in client.records] == [False, True]


async def test_two_agents_share_one_server_but_use_it_differently(servers, settings):
    """Roadmap experiment: same MCP server, different queries per agent."""
    from agents.competitor.agent import CompetitorAgent
    from agents.customer_insights.agent import CustomerInsightsAgent
    from backend.orchestrator.state import AgentTaskInput

    task = AgentTaskInput(query="Launch an AI-powered HR SaaS platform in India.")
    competitor = await CompetitorAgent(servers=servers, settings=settings).run(task)
    customer = await CustomerInsightsAgent(servers=servers, settings=settings).run(task)

    def searches(result):
        return {c.arguments["query"] for c in result.tool_calls if c.tool == "web_search"}

    assert searches(competitor) and searches(customer)
    assert searches(competitor).isdisjoint(searches(customer))
    assert {c.server for c in customer.tool_calls} == {"web_research"}
    assert "business_data" in {c.server for c in competitor.tool_calls}


async def test_resources_and_prompts_are_exposed():
    """The business data server shows all three MCP primitives."""
    async with Client(business_data.mcp) as client:
        resources = await client.list_resources()
        assert [str(r.uri) for r in resources.resources] == ["business://dataset/info"]
        content = await client.read_resource("business://dataset/info")
        assert json.loads(content.contents[0].text)["company_count"] > 0

        prompts = await client.list_prompts()
        assert [p.name for p in prompts.prompts] == ["competitor_comparison"]
        prompt = await client.get_prompt("competitor_comparison", {"company_a": "Keka", "company_b": "Zimyo"})
        assert "Keka" in prompt.messages[0].content.text
