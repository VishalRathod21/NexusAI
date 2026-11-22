"""Agent Cards.

An Agent Card is the public "business card" an A2A agent serves at
`/.well-known/agent-card.json`. It tells a client who the agent is, what
skills it has, where its endpoint is and which protocol binding to use. The
orchestrator reads these cards to decide which agent gets which task, instead
of hard-coding that knowledge.
"""

from __future__ import annotations

from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol
from google.protobuf.json_format import MessageToDict

AGENT_VERSION = "1.0.0"


def build_agent_card(agent: Any, public_url: str, requires_auth: bool = False) -> AgentCard:
    """Build the card for a `ResearchAgent` reachable at `public_url`.

    With `requires_auth`, the card tells clients that calls need a Bearer
    token. The card itself stays public: that is how a client learns the rule.
    """
    security: dict[str, Any] = {}
    if requires_auth:
        security = {
            "security_schemes": {
                "internal": SecurityScheme(
                    http_auth_security_scheme=HTTPAuthSecurityScheme(
                        scheme="Bearer", description="Shared service token of this NexusAI deployment."
                    )
                )
            },
            "security_requirements": [SecurityRequirement(schemes={"internal": StringList()})],
        }
    return AgentCard(
        **security,
        name=agent.display_name,
        description=agent.description,
        version=AGENT_VERSION,
        supported_interfaces=[
            AgentInterface(
                url=public_url.rstrip("/") + "/",
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id=agent.skill,
                name=agent.display_name,
                description=agent.description,
                tags=list(agent.tags),
                examples=list(agent.examples),
                input_modes=["application/json", "text/plain"],
                output_modes=["application/json"],
            )
        ],
    )


def card_to_dict(card: AgentCard) -> dict[str, Any]:
    return MessageToDict(card)
