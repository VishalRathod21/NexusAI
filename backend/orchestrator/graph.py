"""The research workflow as a LangGraph state machine.

                         +--> market_research ----+
    START --> discover --+--> competitor_analysis +--> synthesize --> aggregate --> END
                         +--> customer_insights --+

* The three research nodes are independent, so LangGraph runs them in the same
  step, concurrently (fan-out).
* `synthesize` has an edge from all three, so it runs only after every one of
  them has finished (fan-in), whether it succeeded or not.
* State fields written by parallel nodes need a reducer that says how to merge
  concurrent updates: results are merged as a dict, records are appended.

Why a graph and not just `asyncio.gather`? The dependency structure is explicit
and inspectable, and adding a step (a review node, a human approval interrupt,
a checkpoint) is a local change instead of a rewrite.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.orchestrator.state import (
    RESEARCH_SKILLS,
    SKILL_STRATEGY,
    AgentResult,
    AgentTaskInput,
    FinalReport,
    ResearchPlan,
    TaskRecord,
)

if TYPE_CHECKING:
    from backend.orchestrator.orchestrator import Orchestrator, TaskCallback


def merge_results(left: dict[str, AgentResult], right: dict[str, AgentResult]) -> dict[str, AgentResult]:
    return {**left, **right}


class WorkflowState(TypedDict, total=False):
    request_id: str
    query: str
    plan: ResearchPlan
    discovery: dict[str, Any]
    results: Annotated[dict[str, AgentResult], merge_results]
    records: Annotated[list[TaskRecord], operator.add]
    report: FinalReport


def build_graph(orchestrator: "Orchestrator", on_task: "TaskCallback | None" = None):
    def task_input(state: WorkflowState, skill: str, upstream: dict[str, Any] | None = None) -> AgentTaskInput:
        plan = state["plan"]
        planned = next((t for t in plan.tasks if t.skill == skill), None)
        return AgentTaskInput(
            request_id=state["request_id"],
            query=state["query"],
            topic=plan.topic,
            focus_questions=planned.focus_questions if planned else [],
            upstream=upstream or {},
        )

    async def discover(state: WorkflowState) -> dict[str, Any]:
        return {"discovery": await orchestrator.discover()}

    def research_node(skill: str):
        async def node(state: WorkflowState) -> dict[str, Any]:
            result, record = await orchestrator.run_task(skill, task_input(state, skill), on_task)
            return {"results": {skill: result} if result else {}, "records": [record]}

        return node

    async def synthesize(state: WorkflowState) -> dict[str, Any]:
        # Strategy receives only analysis + sources: the minimum it needs.
        upstream = {
            skill: {
                "analysis": result.analysis,
                "sources": [s.model_dump() for s in result.sources],
            }
            for skill, result in state.get("results", {}).items()
            if skill in RESEARCH_SKILLS
        }
        result, record = await orchestrator.run_task(
            SKILL_STRATEGY, task_input(state, SKILL_STRATEGY, upstream), on_task
        )
        return {"results": {SKILL_STRATEGY: result} if result else {}, "records": [record]}

    async def aggregate(state: WorkflowState) -> dict[str, Any]:
        report = orchestrator.aggregate(
            state["request_id"],
            state["query"],
            state["plan"],
            state.get("results", {}),
            state.get("records", []),
        )
        return {"report": report}

    graph = StateGraph(WorkflowState)
    graph.add_node("discover", discover)
    for skill in RESEARCH_SKILLS:
        graph.add_node(skill, research_node(skill))
    graph.add_node("synthesize", synthesize)
    graph.add_node("aggregate", aggregate)

    graph.add_edge(START, "discover")
    for skill in RESEARCH_SKILLS:
        graph.add_edge("discover", skill)  # fan-out
    graph.add_edge(list(RESEARCH_SKILLS), "synthesize")  # fan-in: wait for all
    graph.add_edge("synthesize", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()
