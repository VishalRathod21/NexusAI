"""Least-privilege tool access.

Each agent may call only the MCP tools its job needs. The policy is an explicit
allow-list: anything not listed is denied.

Why the Strategy Agent has no web tools: it reads the other agents' output,
which is derived from untrusted web content. If something malicious slipped
through, the agent that reads it must not be able to reach the network. This
limits the blast radius of an injection.

Where it is enforced: in `ToolClient`, the single choke point every tool call
goes through. Limitation, stated honestly: this is client-side enforcement. The
MCP servers here do not authenticate callers, so a process that bypasses
`ToolClient` could call any tool. In production you would add per-agent
credentials (MCP supports OAuth) and enforce the same policy on the server.
"""

from __future__ import annotations

from security import audit

AGENT_MARKET = "market_research_agent"
AGENT_COMPETITOR = "competitor_agent"
AGENT_CUSTOMER = "customer_insights_agent"
AGENT_STRATEGY = "strategy_agent"

# agent -> server -> allowed tools
TOOL_POLICY: dict[str, dict[str, frozenset[str]]] = {
    AGENT_MARKET: {
        "web_research": frozenset({"web_search", "fetch_page"}),
        "market_calculator": frozenset(
            {"percentage_change", "cagr", "market_size_top_down", "compare_values"}
        ),
    },
    AGENT_COMPETITOR: {
        "web_research": frozenset({"web_search", "fetch_page"}),
        "business_data": frozenset(
            {"search_companies", "get_company_profile", "list_categories"}
        ),
    },
    AGENT_CUSTOMER: {
        "web_research": frozenset({"web_search", "fetch_page"}),
    },
    AGENT_STRATEGY: {
        "market_calculator": frozenset({"percentage_change", "compare_values"}),
    },
}


class PermissionDenied(PermissionError):
    pass


def permitted_tools(agent: str) -> dict[str, frozenset[str]]:
    return TOOL_POLICY.get(agent, {})


def is_allowed(agent: str, server: str, tool: str) -> bool:
    return tool in TOOL_POLICY.get(agent, {}).get(server, frozenset())


def authorize_tool(agent: str, server: str, tool: str) -> None:
    """Raise `PermissionDenied` (and audit it) unless the call is allow-listed."""
    if is_allowed(agent, server, tool):
        return
    audit.record(
        "unauthorized_tool_call_blocked",
        severity="high",
        agent=agent,
        detail={"server": server, "tool": tool},
    )
    raise PermissionDenied(f"{agent} is not permitted to call {server}.{tool}")
