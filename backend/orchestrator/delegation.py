"""How the orchestrator hands a task to an agent.

Two interchangeable delegators implement the same interface:

* LocalDelegator - calls the agent object in-process (Phase 1 stepping stone,
                   also used by tests and evaluation).
* A2ADelegator   - discovers agents from Agent Cards and delegates over the
                   A2A protocol to independently running services.

Because the orchestrator only sees `Delegator`, the workflow, retries and
validation are identical in both modes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from agents.base import ResearchAgent
from backend.config.settings import Settings
from backend.orchestrator.state import AgentTaskInput
from protocols.a2a.client import A2AClient, A2AError
from protocols.a2a.discovery import AgentRegistry

logger = logging.getLogger(__name__)


class AgentUnavailable(RuntimeError):
    """No reachable agent offers the skill, or it did not answer in time. Retryable."""


class AgentTaskFailed(RuntimeError):
    """The agent answered, but the task ended failed/rejected/canceled."""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Delegation:
    data: Any = None  # raw, unvalidated agent result
    agent_name: str = ""
    agent_url: str = ""
    transport: str = "local"
    task_id: str = ""
    lifecycle: list[str] = field(default_factory=list)


class Delegator(Protocol):
    transport: str

    async def discover(self) -> dict[str, Any]: ...

    async def delegate(self, skill: str, task: AgentTaskInput, timeout: float) -> Delegation: ...

    async def close(self) -> None: ...


class LocalDelegator:
    transport = "local"

    def __init__(self, agents: list[ResearchAgent]):
        self.agents = {agent.skill: agent for agent in agents}

    async def discover(self) -> dict[str, Any]:
        return {
            "agents": [
                {"name": a.display_name, "skills": [a.skill], "description": a.description, "base_url": "in-process"}
                for a in self.agents.values()
            ],
            "unreachable": {},
        }

    async def delegate(self, skill: str, task: AgentTaskInput, timeout: float) -> Delegation:
        agent = self.agents.get(skill)
        if agent is None:
            raise AgentUnavailable(f"no agent offers skill '{skill}'")
        delegation = Delegation(
            agent_name=agent.display_name, agent_url="in-process", lifecycle=["submitted", "working"]
        )
        try:
            result = await asyncio.wait_for(agent.run(task), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AgentUnavailable(f"{agent.display_name} did not finish within {timeout}s") from exc
        except Exception as exc:
            raise AgentTaskFailed(f"{type(exc).__name__}: {exc}"[:400], retryable=True) from exc
        delegation.lifecycle.append("completed")
        delegation.data = result.model_dump(mode="json")
        return delegation

    async def close(self) -> None:
        return None


class A2ADelegator:
    transport = "a2a"

    def __init__(self, settings: Settings, client: A2AClient | None = None):
        self.client = client or A2AClient(
            timeout=settings.agent_timeout_seconds, token=settings.internal_token()
        )
        self.registry = AgentRegistry(self.client, settings.agent_urls())

    async def discover(self) -> dict[str, Any]:
        await self.registry.refresh()
        return self.registry.to_dict()

    async def delegate(self, skill: str, task: AgentTaskInput, timeout: float) -> Delegation:
        agent = self.registry.find_by_skill(skill)
        if agent is None:
            # The agent may have come back since the last discovery: look again once.
            await self.registry.refresh()
            agent = self.registry.find_by_skill(skill)
        if agent is None:
            raise AgentUnavailable(f"no reachable agent advertises skill '{skill}'")

        delegation = Delegation(agent_name=agent.name, agent_url=agent.base_url, transport="a2a")
        try:
            outcome = await self.client.send_task(agent.card, task.model_dump(mode="json"), timeout)
        except A2AError as exc:
            raise AgentUnavailable(f"{agent.name}: {exc}") from exc

        delegation.task_id = outcome.task_id
        delegation.lifecycle = outcome.lifecycle
        if outcome.state != "completed":
            # "rejected" means our input was refused: sending it again cannot help.
            raise AgentTaskFailed(
                f"{agent.name} task ended in state '{outcome.state}': {outcome.error or 'no detail'}",
                retryable=outcome.state == "failed",
            )
        delegation.data = outcome.data
        return delegation

    async def close(self) -> None:
        await self.client.close()
