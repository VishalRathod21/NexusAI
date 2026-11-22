"""MCP client used by every agent.

This is the single choke point for tool access. One call goes through, in order:

1. authorization   - is this agent allowed to call this tool? (least privilege)
2. discovery check - does the server actually advertise the tool?
3. cache           - have we already made this exact call?
4. execution       - real MCP `tools/call`, with a timeout and bounded retries
5. recording       - every call (ok, failed, cached, denied) is logged for the
                     activity dashboard

A "server" target is normally a Streamable HTTP URL. Tests pass an `MCPServer`
object instead, which the SDK connects to in-process: same protocol messages,
no network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import anyio

from backend.config.settings import Settings, get_settings
from backend.orchestrator.state import ToolCallRecord
from database.cache import ToolCache, make_key
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from observability.tracing import span
from security.internal_auth import bearer_headers
from security.permissions import PermissionDenied, authorize_tool, permitted_tools

logger = logging.getLogger(__name__)

# Results from these servers are stable enough to reuse.
CACHEABLE_SERVERS = {"web_research", "business_data"}

_shared_cache: ToolCache | None = None


def get_shared_cache(settings: Settings) -> ToolCache:
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = ToolCache(settings.redis_url, settings.cache_ttl_seconds)
    return _shared_cache


class ToolError(RuntimeError):
    """The tool ran and reported an error (bad arguments, upstream failure)."""


class ToolUnavailable(RuntimeError):
    """The tool could not be reached within the retry budget."""


def _parse_result(result: Any) -> Any:
    """Turn a CallToolResult into plain Python data."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    texts = [getattr(block, "text", "") for block in (result.content or [])]
    joined = "\n".join(t for t in texts if t)
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return joined


def _error_text(result: Any) -> str:
    texts = [getattr(block, "text", "") for block in (result.content or [])]
    return " ".join(t for t in texts if t)[:400] or "tool returned an error"


class ToolClient:
    def __init__(
        self,
        agent: str,
        servers: dict[str, Any] | None = None,
        settings: Settings | None = None,
        cache: ToolCache | None = None,
    ):
        self.agent = agent
        self.settings = settings or get_settings()
        # `{}` is a valid choice (no servers), so only None falls back to the defaults.
        self.servers: dict[str, Any] = (
            dict(self.settings.mcp_servers()) if servers is None else servers
        )
        self.cache = cache or get_shared_cache(self.settings)
        self.records: list[ToolCallRecord] = []
        self._discovered: dict[str, dict[str, dict[str, Any]]] = {}

    # ---------------------------------------------------------- discovery
    async def discover(self, server: str) -> dict[str, dict[str, Any]]:
        """Ask a server which tools it offers (MCP `tools/list`)."""
        if server in self._discovered:
            return self._discovered[server]
        with anyio.fail_after(self.settings.tool_timeout_seconds):
            async with self._connect(server) as client:
                listing = await client.list_tools()
        tools = {
            tool.name: {
                "description": tool.description or "",
                "input_schema": tool.input_schema,
            }
            for tool in listing.tools
        }
        self._discovered[server] = tools
        return tools

    async def discover_permitted(self) -> dict[str, list[dict[str, Any]]]:
        """Tools this agent may use = (what servers advertise) AND (policy)."""
        available: dict[str, list[dict[str, Any]]] = {}
        for server, allowed in permitted_tools(self.agent).items():
            if server not in self.servers:
                continue
            try:
                advertised = await self.discover(server)
            except Exception as exc:
                logger.warning("discovery failed for %s: %s", server, exc)
                continue
            available[server] = [
                {"name": name, **meta} for name, meta in advertised.items() if name in allowed
            ]
        return available

    def _target(self, server: str) -> Any:
        if server not in self.servers:
            raise ToolUnavailable(f"unknown MCP server '{server}'")
        return self.servers[server]

    @asynccontextmanager
    async def _connect(self, server: str) -> AsyncIterator[Client]:
        """Open one MCP connection. Remote servers get the shared service token."""
        target = self._target(server)
        token = self.settings.internal_token()
        if isinstance(target, str) and token:
            async with create_mcp_http_client(headers=bearer_headers(token)) as http:
                async with Client(streamable_http_client(target, http_client=http)) as client:
                    yield client
        else:
            async with Client(target) as client:
                yield client

    # ---------------------------------------------------------- execution
    async def call(self, server: str, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call one tool. Raises PermissionDenied, ToolError or ToolUnavailable."""
        arguments = arguments or {}
        record = ToolCallRecord(agent=self.agent, server=server, tool=tool, arguments=arguments)
        self.records.append(record)
        started = time.perf_counter()
        try:
            with span(f"mcp.{server}.{tool}", agent=self.agent) as trace_span:
                data = await self._call(server, tool, arguments, record)
                trace_span["attributes"]["cached"] = record.cached
                return data
        except Exception as exc:
            record.ok = False
            record.error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            record.latency_ms = round((time.perf_counter() - started) * 1000, 1)

    async def _call(
        self, server: str, tool: str, arguments: dict[str, Any], record: ToolCallRecord
    ) -> Any:
        authorize_tool(self.agent, server, tool)  # 1. least privilege
        self._target(server)  # fail fast on an unknown server

        try:  # 2. the tool must really exist on the server
            advertised = await self.discover(server)
        except PermissionDenied:
            raise
        except Exception as exc:
            raise ToolUnavailable(f"could not reach MCP server '{server}': {exc}") from exc
        if tool not in advertised:
            raise ToolError(f"server '{server}' does not offer a tool named '{tool}'")

        key = make_key(server, tool, arguments)  # 3. cache
        if server in CACHEABLE_SERVERS:
            cached = await self.cache.get(key)
            if cached is not None:
                record.cached = True
                return cached

        attempts = 1 + max(0, self.settings.max_retries)  # 4. execute
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            record.attempts = attempt
            try:
                with anyio.fail_after(self.settings.tool_timeout_seconds):
                    async with self._connect(server) as client:
                        result = await client.call_tool(tool, arguments)
            except TimeoutError:
                last_error = TimeoutError(
                    f"{server}.{tool} exceeded {self.settings.tool_timeout_seconds}s"
                )
            except Exception as exc:  # transport / protocol failure: retryable
                last_error = exc
            else:
                if result.is_error:
                    # The tool itself said no (validation, upstream 404...).
                    # Retrying the same arguments would not help.
                    raise ToolError(_error_text(result))
                data = _parse_result(result)
                if server in CACHEABLE_SERVERS:
                    await self.cache.set(key, data)
                return data
            logger.warning("%s.%s attempt %d/%d failed: %s", server, tool, attempt, attempts, last_error)
            if attempt < attempts:
                await asyncio.sleep(self.settings.retry_backoff_seconds * attempt)

        raise ToolUnavailable(f"{server}.{tool} failed after {attempts} attempts: {last_error}")
