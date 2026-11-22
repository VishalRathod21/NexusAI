"""Shared entry point for the MCP servers.

Each server is its own process speaking MCP over Streamable HTTP, so agents in
other processes (or containers) can discover and call its tools.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings


def run_server(server: MCPServer, default_port: int) -> None:
    import uvicorn

    from backend.config.settings import get_settings
    from security.internal_auth import InternalTokenMiddleware

    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", str(default_port)))
    kwargs: dict = {
        "host": host,
        "streamable_http_path": "/mcp",
        # Tools here are plain request/response: no server-side session needed.
        "stateless_http": True,
        "json_response": True,
    }
    if host not in ("127.0.0.1", "localhost"):
        # Inside Docker the Host header is the service name (e.g. mcp-web:9101),
        # which the SDK's localhost-only DNS-rebinding check would reject.
        # The compose network is private; do not expose these ports publicly.
        kwargs["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    app = server.streamable_http_app(**kwargs)
    # Only callers holding the shared service token may list or call tools.
    app = InternalTokenMiddleware(app, token=get_settings().internal_token(), service=server.name)
    uvicorn.run(app, host=host, port=port, log_level="info")
