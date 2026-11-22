"""Agent discovery through Agent Cards.

The orchestrator is configured only with a seed list of base URLs. It does not
know which agent lives where or what each can do. It learns that by fetching
every Agent Card and indexing the skills they advertise. Delegation then asks
"who has the skill `competitor_analysis`?" instead of "call the competitor URL".

Consequences worth noticing:
* swapping in a different agent that advertises the same skill needs no
  orchestrator change;
* an agent that is down is simply absent from the registry, and the
  orchestrator can degrade instead of crashing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from a2a.types import AgentCard

from protocols.a2a.agent_cards import card_to_dict
from protocols.a2a.client import A2AClient

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredAgent:
    base_url: str
    card: AgentCard
    name: str
    description: str
    skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "name": self.name,
            "description": self.description,
            "skills": self.skills,
            "card": card_to_dict(self.card),
        }


class AgentRegistry:
    def __init__(self, client: A2AClient, seed_urls: list[str]):
        self.client = client
        self.seed_urls = seed_urls
        self.agents: list[DiscoveredAgent] = []
        self.unreachable: dict[str, str] = {}

    async def _discover_one(self, base_url: str) -> DiscoveredAgent | None:
        try:
            card = await self.client.fetch_card(base_url)
        except Exception as exc:
            self.unreachable[base_url] = f"{type(exc).__name__}: {exc}"[:200]
            logger.warning("agent at %s is not discoverable: %s", base_url, exc)
            return None
        return DiscoveredAgent(
            base_url=base_url,
            card=card,
            name=card.name,
            description=card.description,
            skills=[skill.id for skill in card.skills],
        )

    async def refresh(self) -> list[DiscoveredAgent]:
        """Fetch all cards in parallel. Unreachable agents are skipped."""
        self.unreachable = {}
        found = await asyncio.gather(*[self._discover_one(url) for url in self.seed_urls])
        self.agents = [agent for agent in found if agent is not None]
        return self.agents

    def find_by_skill(self, skill_id: str) -> DiscoveredAgent | None:
        for agent in self.agents:
            if skill_id in agent.skills:
                return agent
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": [agent.to_dict() for agent in self.agents],
            "unreachable": self.unreachable,
        }
