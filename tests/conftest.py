"""Shared test setup.

Tests are hermetic: no network, no API key, no running services.

* MCP     - agents connect to the real `MCPServer` objects in-process.
* A2A     - the real agent apps are served through an in-memory HTTP transport.
* Web     - the synthetic fixture corpus (includes one poisoned page).
* LLM     - offline mode, or a scripted fake where a test needs model output.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

# Must be set before any project module reads settings.
os.environ["LLM_PROVIDER"] = "offline"
os.environ["WEB_SEARCH_BACKEND"] = "fixture"
os.environ["REDIS_URL"] = ""
os.environ["AUDIT_LOG_PATH"] = str(Path(tempfile.mkdtemp(prefix="nexusai-test-")) / "audit.jsonl")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402

from backend.config.settings import Settings  # noqa: E402
from backend.llm.provider import LLMClient  # noqa: E402
from backend.orchestrator.state import Usage  # noqa: E402
from database.cache import ToolCache  # noqa: E402
from protocols.mcp.clients import tool_client  # noqa: E402
from protocols.mcp.servers import business_data, market_calculator, web_research  # noqa: E402
from security import audit  # noqa: E402

QUERY = (
    "I want to launch an AI-powered HR SaaS platform in India. Analyze the market, "
    "identify competitors, understand customer demand, and recommend a business strategy."
)
AGENT_HOSTS = {
    "market_agent_url": "http://market.test:9001",
    "competitor_agent_url": "http://competitor.test:9002",
    "customer_agent_url": "http://customer.test:9003",
    "strategy_agent_url": "http://strategy.test:9004",
}


@pytest.fixture(autouse=True)
def clean_state():
    """Each test starts with an empty audit trail and an empty tool cache."""
    audit.clear()
    tool_client._shared_cache = ToolCache()
    yield


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="offline",
        orchestration_mode="local",
        web_search_backend="fixture",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        max_retries=1,
        retry_backoff_seconds=0.0,
        llm_max_retries=1,
        agent_timeout_seconds=20,
        tool_timeout_seconds=10,
        **AGENT_HOSTS,
    )


@pytest.fixture
def servers() -> dict[str, Any]:
    """The real MCP servers, connected in-process."""
    return {
        "web_research": web_research.mcp,
        "business_data": business_data.mcp,
        "market_calculator": market_calculator.mcp,
    }


class FakeLLM(LLMClient):
    """Scripted model: `responder(schema, user_prompt)` returns the 'LLM output'."""

    def __init__(self, settings: Settings, responder: Callable[[type, str], Any]):
        super().__init__(settings)
        self.responder = responder
        self.calls = 0

    @property
    def is_offline(self) -> bool:
        return False

    @property
    def mode(self) -> str:
        return "fake:scripted"

    async def structured(self, system: str, user: str, schema: type):
        self.calls += 1
        return self.responder(schema, user), Usage(input_tokens=100, output_tokens=40, llm_calls=1)


class RouterTransport(httpx.AsyncBaseTransport):
    """Routes requests to in-process ASGI apps by host:port. Unknown hosts
    fail like a machine that is down."""

    def __init__(self, apps: dict[str, Any]):
        self.transports = {
            httpx.URL(url).netloc.decode(): httpx.ASGITransport(app=app) for url, app in apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self.transports.get(request.url.netloc.decode())
        if transport is None:
            raise httpx.ConnectError("connection refused", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture
def agent_apps(settings, servers) -> dict[str, Any]:
    """The four real A2A agent apps, keyed by their public URL."""
    from agents.a2a_server import build_agent_app
    from backend.orchestrator.orchestrator import build_local_agents

    agents = build_local_agents(settings, servers)
    urls = settings.agent_urls()
    return {url: build_agent_app(agent, url) for url, agent in zip(urls, agents)}


@pytest.fixture
def make_a2a_orchestrator(settings):
    """Factory: an orchestrator that talks A2A to the given agent apps."""
    from backend.orchestrator.delegation import A2ADelegator
    from backend.orchestrator.orchestrator import Orchestrator
    from protocols.a2a.client import A2AClient

    def factory(apps: dict[str, Any]) -> Orchestrator:
        http = httpx.AsyncClient(transport=RouterTransport(apps), timeout=30)
        delegator = A2ADelegator(settings, client=A2AClient(http_client=http))
        return Orchestrator(settings, delegator=delegator)

    return factory


@pytest.fixture
def local_orchestrator(settings, servers):
    from backend.orchestrator.delegation import LocalDelegator
    from backend.orchestrator.orchestrator import Orchestrator, build_local_agents

    return Orchestrator(settings, delegator=LocalDelegator(build_local_agents(settings, servers)))
