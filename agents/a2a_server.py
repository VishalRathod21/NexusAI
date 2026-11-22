"""Turns a `ResearchAgent` into an independent A2A service.

Pieces, all from the official A2A SDK:

* AgentCard               - served at /.well-known/agent-card.json (discovery)
* AgentExecutor           - our bridge: A2A request in, agent run, A2A events out
* DefaultRequestHandler   - implements the protocol methods (message/send,
                            message/stream, tasks/get, tasks/cancel...)
* TaskStore               - remembers tasks so clients can query them later
* JSON-RPC routes         - the HTTP endpoint clients talk to

Task lifecycle published by the executor:
    submitted -> working -> (artifact) -> completed
                         \\-> failed / rejected / canceled
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from a2a.helpers import (
    get_data_parts,
    get_text_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from fastapi import FastAPI
from pydantic import ValidationError

from backend.orchestrator.state import AgentTaskInput
from protocols.a2a.agent_cards import build_agent_card
from security import audit
from security.internal_auth import InternalTokenMiddleware

from agents.base import ResearchAgent

logger = logging.getLogger(__name__)

RESULT_ARTIFACT_NAME = "agent_result"


def parse_task_input(context: RequestContext) -> AgentTaskInput:
    """Read the incoming A2A message. It is validated like any external input."""
    message = context.message
    if message is None:
        raise ValueError("request has no message")
    for data in get_data_parts(message.parts):
        if isinstance(data, dict):
            return AgentTaskInput.model_validate(data)
    text = "\n".join(get_text_parts(message.parts)).strip()
    if text:  # plain-text clients are supported too
        return AgentTaskInput(query=text)
    raise ValueError("message has no usable data or text part")


class ResearchAgentExecutor(AgentExecutor):
    def __init__(self, agent: ResearchAgent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)  # state: submitted
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            task_input = parse_task_input(context)
        except (ValidationError, ValueError) as exc:
            audit.record(
                "invalid_a2a_request_rejected",
                severity="medium",
                agent=self.agent.name,
                detail={"error": str(exc)[:300]},
            )
            await updater.reject(
                updater.new_agent_message([new_text_part(f"invalid task input: {exc}"[:500])])
            )
            return

        await updater.start_work(
            updater.new_agent_message([new_text_part(f"{self.agent.display_name} is working")])
        )
        try:
            result = await self.agent.run(task_input)
        except Exception as exc:
            logger.exception("%s failed", self.agent.name)
            await updater.failed(
                updater.new_agent_message(
                    [new_text_part(f"{type(exc).__name__}: {exc}"[:500])]
                )
            )
            return

        await updater.add_artifact(
            [new_data_part(result.model_dump(mode="json"), media_type="application/json")],
            name=RESULT_ARTIFACT_NAME,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id and context.context_id:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def build_agent_app(agent: ResearchAgent, public_url: str) -> FastAPI:
    token = agent.settings.internal_token()
    card = build_agent_card(agent, public_url, requires_auth=bool(token))
    handler = DefaultRequestHandler(
        agent_executor=ResearchAgentExecutor(agent),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await handler.aclose()

    app = FastAPI(title=agent.display_name, version=card.version, lifespan=lifespan)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "agent": agent.name, "skill": agent.skill, "llm": agent.llm.mode}

    # The Agent Card and the health check stay public; everything else (the
    # JSON-RPC endpoint) requires the shared service token when one is set.
    app.add_middleware(
        InternalTokenMiddleware,
        token=token,
        public_paths=(AGENT_CARD_WELL_KNOWN_PATH, "/health"),
        service=agent.name,
    )

    return app


def serve(app: FastAPI, public_url: str) -> None:
    """Run one agent service. Bind address comes from AGENT_HOST / AGENT_PORT."""
    import uvicorn

    port = int(os.getenv("AGENT_PORT") or urlparse(public_url).port or 9000)
    uvicorn.run(app, host=os.getenv("AGENT_HOST", "127.0.0.1"), port=port, log_level="info")
