"""Service-to-service authentication.

The backend, the agents and the MCP servers share one secret
(`INTERNAL_API_TOKEN`). Callers send it as `Authorization: Bearer <token>`;
agents and MCP servers reject calls without it. This closes the gap where any
process on the network could call an agent or a tool directly.

It is one shared secret, not per-service identity: it proves "this caller is
part of the system", not which part. Per-agent credentials (and enforcing the
tool policy on the MCP server with them) is the next step up.
"""

from __future__ import annotations

import hmac
from typing import Any

from security import audit


def bearer_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class InternalTokenMiddleware:
    """ASGI middleware: require the shared token, except on public paths."""

    def __init__(self, app: Any, token: str | None, public_paths: tuple[str, ...] = (), service: str = ""):
        self.app = app
        self.token = token
        self.public_paths = public_paths
        self.service = service

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not self.token or scope.get("path") in self.public_paths:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        presented = headers.get(b"authorization", b"")
        expected = f"Bearer {self.token}".encode()
        if hmac.compare_digest(presented, expected):
            await self.app(scope, receive, send)
            return
        audit.record(
            "unauthenticated_internal_call_rejected",
            severity="high",
            agent=self.service,
            detail={"path": scope.get("path"), "had_credentials": bool(presented)},
        )
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b'{"error": "missing or invalid internal credentials"}'}
        )
