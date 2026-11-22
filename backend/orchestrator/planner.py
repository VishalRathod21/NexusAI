"""Research planning: turn one request into tasks for the specialised agents.

The *shape* of the plan is fixed: three independent research tasks, then a
synthesis task that depends on them. A fixed shape keeps the workflow
predictable and testable. What the planner adds per request is the short
search topic and focus questions for each agent.

With an LLM configured the planner asks it for the topic and questions; if that
fails (or in offline mode) it falls back to a template. Planning must never be
the reason a request fails.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from agents.base import derive_topic
from backend.llm.provider import LLMClient, LLMError
from backend.orchestrator.state import (
    RESEARCH_SKILLS,
    SKILL_COMPETITOR,
    SKILL_CUSTOMER,
    SKILL_MARKET,
    SKILL_STRATEGY,
    PlannedTask,
    ResearchPlan,
    Usage,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = {
    SKILL_MARKET: "Research market trends, demand signals and market context for: {topic}",
    SKILL_COMPETITOR: "Identify and compare the relevant competitors for: {topic}",
    SKILL_CUSTOMER: "Identify customer segments, pain points and needs for: {topic}",
    SKILL_STRATEGY: "Synthesise the research into opportunities, risks and recommendations for: {topic}",
}


class PlanDraft(BaseModel):
    """What the LLM contributes to the plan."""

    topic: str = Field(
        description="The business idea as a short search phrase, max 10 words, "
        "e.g. 'AI-powered HR SaaS platform in India'."
    )
    market_questions: list[str] = Field(default_factory=list, max_length=3)
    competitor_questions: list[str] = Field(default_factory=list, max_length=3)
    customer_questions: list[str] = Field(default_factory=list, max_length=3)
    rationale: str = ""


PLANNER_SYSTEM = (
    "You plan business research. Given a request, extract the business idea as a "
    "short search phrase and write up to 3 short, searchable focus questions for "
    "each of: market research, competitor analysis, customer insights. Questions "
    "must be specific to the request. Do not answer them."
)


def _build(topic: str, questions: dict[str, list[str]], planner: str, rationale: str) -> ResearchPlan:
    tasks = [
        PlannedTask(
            skill=skill,
            instruction=INSTRUCTIONS[skill].format(topic=topic),
            focus_questions=[q.strip()[:200] for q in questions.get(skill, []) if q.strip()][:3],
        )
        for skill in RESEARCH_SKILLS
    ]
    tasks.append(
        PlannedTask(
            skill=SKILL_STRATEGY,
            instruction=INSTRUCTIONS[SKILL_STRATEGY].format(topic=topic),
            depends_on=list(RESEARCH_SKILLS),
        )
    )
    return ResearchPlan(topic=topic, tasks=tasks, planner=planner, rationale=rationale)  # type: ignore[arg-type]


def template_plan(query: str) -> ResearchPlan:
    return _build(
        derive_topic(query),
        {},
        "template",
        "Three research tasks run in parallel; strategy runs after them because it "
        "depends on their findings.",
    )


async def create_plan(query: str, llm: LLMClient) -> tuple[ResearchPlan, Usage]:
    if llm.is_offline:
        return template_plan(query), Usage()
    try:
        from observability.tracing import langfuse_context

        with langfuse_context(trace_name="planner.create_plan", tags=["planner", "planning"], metadata={"query": query}):
            draft, usage = await llm.structured(PLANNER_SYSTEM, f"REQUEST:\n{query}", PlanDraft)
    except LLMError as exc:
        logger.warning("LLM planning failed, using template plan: %s", exc)
        return template_plan(query), Usage()
    topic = " ".join(draft.topic.split()[:12]) or derive_topic(query)
    plan = _build(
        topic,
        {
            SKILL_MARKET: draft.market_questions,
            SKILL_COMPETITOR: draft.competitor_questions,
            SKILL_CUSTOMER: draft.customer_questions,
        },
        "llm",
        draft.rationale[:500],
    )
    return plan, usage
