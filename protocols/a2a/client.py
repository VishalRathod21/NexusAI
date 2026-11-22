"""A2A client used by the orchestrator.

Wraps the official SDK client with what the orchestrator needs:

* send a task (an A2A message with a JSON data part)
* follow the task lifecycle from the event stream
  (submitted -> working -> completed / failed / rejected / canceled)
* collect the result artifact
* enforce a deadline, and cancel the remote task if it is exceeded

The outcome is a plain dataclass so the rest of the code does not depend on
protobuf types.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, get_message_text, new_data_part
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
)

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


class A2AError(RuntimeError):
    """Transport-level failure talking to an agent (unreachable, timeout...)."""


def state_name(state: int) -> str:
    """TASK_STATE_COMPLETED -> 'completed'."""
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


@dataclass
class A2AOutcome:
    task_id: str = ""
    state: str = "unknown"
    lifecycle: list[str] = field(default_factory=list)
    data: Any = None
    error: str | None = None

    def push_state(self, state: str) -> None:
        self.state = state
        if not self.lifecycle or self.lifecycle[-1] != state:
            self.lifecycle.append(state)


class A2AClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 180.0,
        token: str | None = None,
    ):
        self.timeout = timeout
        self._owns_http = http_client is None
        # The read timeout must cover a whole agent run: results arrive on a stream.
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0)
        )
        if token:  # service-to-service credential, sent on every call
            self._http.headers["Authorization"] = f"Bearer {token}"
        self._factory = ClientFactory(ClientConfig(httpx_client=self._http, streaming=True))

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ---------------------------------------------------------- discovery
    async def fetch_card(self, base_url: str) -> AgentCard:
        """GET <base_url>/.well-known/agent-card.json"""
        resolver = A2ACardResolver(self._http, base_url)
        return await resolver.get_agent_card(http_kwargs={"timeout": 5.0})

    # ---------------------------------------------------------- delegation
    def _absorb_task(self, outcome: A2AOutcome, task: Task) -> None:
        outcome.task_id = task.id or outcome.task_id
        outcome.push_state(state_name(task.status.state))
        if task.status.HasField("message"):
            text = get_message_text(task.status.message)
            if text and outcome.state in ("failed", "rejected"):
                outcome.error = text
        for artifact in task.artifacts:
            for data in get_data_parts(artifact.parts):
                outcome.data = data

    async def _stream(self, card: AgentCard, payload: dict[str, Any], outcome: A2AOutcome) -> None:
        message = Message(
            role=Role.ROLE_USER,
            message_id=str(uuid.uuid4()),
            parts=[new_data_part(payload, media_type="application/json")],
        )
        client = self._factory.create(card)
        async for event in client.send_message(SendMessageRequest(message=message)):
            if event.HasField("task"):
                self._absorb_task(outcome, event.task)
            elif event.HasField("status_update"):
                update = event.status_update
                outcome.task_id = update.task_id or outcome.task_id
                outcome.push_state(state_name(update.status.state))
                if update.status.HasField("message") and outcome.state in ("failed", "rejected"):
                    outcome.error = get_message_text(update.status.message)
            elif event.HasField("artifact_update"):
                update = event.artifact_update
                outcome.task_id = update.task_id or outcome.task_id
                for data in get_data_parts(update.artifact.parts):
                    outcome.data = data
            elif event.HasField("message"):
                # An agent may answer directly without creating a task.
                for data in get_data_parts(event.message.parts):
                    outcome.data = data
                outcome.push_state("completed")

        # The stream ended. If we never saw a terminal state, ask for the task.
        if outcome.task_id and outcome.state not in TERMINAL_STATES:
            task = await client.get_task(GetTaskRequest(id=outcome.task_id))
            self._absorb_task(outcome, task)

    async def send_task(
        self, card: AgentCard, payload: dict[str, Any], timeout: float | None = None
    ) -> A2AOutcome:
        """Delegate one task and wait for its terminal state."""
        outcome = A2AOutcome()
        deadline = timeout or self.timeout
        try:
            await asyncio.wait_for(self._stream(card, payload, outcome), timeout=deadline)
        except asyncio.TimeoutError as exc:
            await self._cancel(card, outcome.task_id)
            outcome.push_state("timeout")
            raise A2AError(f"agent did not finish within {deadline}s") from exc
        except A2AError:
            raise
        except Exception as exc:
            raise A2AError(f"{type(exc).__name__}: {exc}"[:400]) from exc
        return outcome

    async def _cancel(self, card: AgentCard, task_id: str) -> None:
        """Best effort: tell the agent to stop work nobody is waiting for."""
        if not task_id:
            return
        try:
            client = self._factory.create(card)
            await asyncio.wait_for(client.cancel_task(CancelTaskRequest(id=task_id)), timeout=5)
        except Exception as exc:
            logger.info("could not cancel task %s: %s", task_id, exc)
