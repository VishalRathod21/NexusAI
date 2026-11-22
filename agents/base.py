"""Shared behaviour of the four specialised agents.

Every agent follows the same pipeline; subclasses only say *what* to research
and *what shape* the answer has:

    gather      -> call MCP tools chosen by code (plan-then-execute)
    sanitize    -> treat everything retrieved as untrusted data
    analyze     -> one LLM call that must return the agent's output contract
    validate    -> ground citations, strip stray links/secrets
    post-process-> optional follow-up tool calls (e.g. the calculator)

Why tools are chosen by code and not by the model: the model reads web pages.
If it also decided which tool to call next, a malicious page could steer it.
Here the tool plan is fixed before any untrusted text is read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from backend.config.settings import Settings, get_settings
from backend.llm.provider import LLMClient, LLMError
from backend.orchestrator.state import (
    AgentResult,
    AgentTaskInput,
    BaseAnalysis,
    Calculation,
    Finding,
    Source,
    Usage,
)
from observability.tracing import span
from protocols.mcp.clients.tool_client import ToolClient
from security import audit
from security.output_validation import validate_analysis
from security.prompt_injection import (
    UNTRUSTED_CONTENT_POLICY,
    neutralize,
    sanitize_untrusted,
    wrap_untrusted,
)

logger = logging.getLogger(__name__)

MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 2500

OUTPUT_RULES = (
    "OUTPUT RULES:\n"
    "- Base the analysis on the evidence provided. Do not add facts from memory.\n"
    "- A finding is `sourced_fact` only if a specific evidence item supports it; "
    "copy that item's URL exactly into `source_urls`. Otherwise it is `hypothesis`.\n"
    "- Do not perform arithmetic or invent numbers.\n"
    "- Put anything you took for granted in `assumptions` and every gap or weak "
    "point in `limitations`.\n"
    "- Be specific and concise."
)

_LEAD_IN = re.compile(
    r"^(i|we)\s+(want|would like|plan|am planning|are planning|need|wish|intend)\s+to\s+"
    r"|^(please\s+)?(analy[sz]e|research|evaluate|assess|study)\s+(the\s+)?(market\s+for\s+)?"
    r"|^(launch|build|start|create|open|introduce)\s+",
    re.IGNORECASE,
)


def derive_topic(query: str) -> str:
    """Short search topic from a long request (used when no planner topic is given)."""
    first = re.split(r"(?<=[.!?])\s+", query.strip())[0]
    previous = None
    while previous != first:
        previous = first
        first = _LEAD_IN.sub("", first).strip()
    first = re.sub(r"^(an?|the)\s+", "", first, flags=re.IGNORECASE)
    words = first.rstrip(".!?").split()
    return " ".join(words[:12]) or query[:80]


def first_sentences(text: str, count: int = 2, max_chars: int = 280) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    picked = " ".join(s for s in sentences[:count] if s)
    return picked[:max_chars].strip()


class Evidence(BaseModel):
    source: Source
    content: str
    data: dict[str, Any] | None = None  # structured payload, when the tool gave one


@dataclass
class RunContext:
    task: AgentTaskInput
    topic: str
    tools: ToolClient
    notes: list[str] = field(default_factory=list)


class ResearchAgent(ABC):
    name: ClassVar[str]
    skill: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    examples: ClassVar[list[str]] = []
    tags: ClassVar[list[str]] = []
    analysis_model: ClassVar[type[BaseAnalysis]]
    role_prompt: ClassVar[str]
    max_evidence_items: ClassVar[int] = MAX_EVIDENCE_ITEMS
    max_evidence_chars: ClassVar[int] = MAX_EVIDENCE_CHARS

    def __init__(
        self,
        llm: LLMClient | None = None,
        servers: dict[str, Any] | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)
        self._servers = servers

    # ------------------------------------------------------- to override
    @abstractmethod
    async def gather(self, ctx: RunContext) -> list[Evidence]:
        """Collect evidence with MCP tools."""

    @abstractmethod
    def offline_analysis(self, ctx: RunContext, evidence: list[Evidence]) -> BaseAnalysis:
        """Extractive answer used when no LLM is available (or as fallback)."""

    async def post_process(self, ctx: RunContext, analysis: BaseAnalysis) -> list[Calculation]:
        return []

    def allowed_urls(self, ctx: RunContext, evidence: list[Evidence]) -> set[str]:
        return {e.source.url for e in evidence}

    def result_sources(self, ctx: RunContext, evidence: list[Evidence]) -> list[Source]:
        return [e.source for e in evidence]

    # ------------------------------------------------------------ prompts
    def system_prompt(self) -> str:
        return f"{self.role_prompt}\n\n{OUTPUT_RULES}\n\n{UNTRUSTED_CONTENT_POLICY}"

    def user_prompt(self, ctx: RunContext, evidence: list[Evidence]) -> str:
        lines = [
            "RESEARCH REQUEST (from the user):",
            ctx.task.query,
            "",
            f"TOPIC: {ctx.topic}",
        ]
        if ctx.task.focus_questions:
            lines.append("FOCUS QUESTIONS:")
            lines.extend(f"- {q}" for q in ctx.task.focus_questions)
        lines.append("")
        lines.append(f"EVIDENCE ({len(evidence)} items). Cite items by their URL.")
        for index, item in enumerate(evidence, start=1):
            lines.append(f"\n[{index}] URL: {item.source.url}")
            body = f"TITLE: {item.source.title}\n{item.content}"
            lines.append(wrap_untrusted(body, label=f"item-{index}"))
        if not evidence:
            lines.append("(no evidence was retrieved; say so in `limitations`)")
        return "\n".join(lines)

    # -------------------------------------------------------- tool helpers
    async def safe_call(
        self, ctx: RunContext, server: str, tool: str, arguments: dict[str, Any]
    ) -> Any | None:
        """Call a tool; a failure costs that piece of evidence, not the task."""
        try:
            return await ctx.tools.call(server, tool, arguments)
        except Exception as exc:
            logger.warning("%s: %s.%s failed: %s", self.name, server, tool, exc)
            return None

    async def web_evidence(
        self, ctx: RunContext, queries: list[str], max_pages: int = 3, max_hits: int = 8
    ) -> list[Evidence]:
        """Search, de-duplicate, then fetch the top pages in parallel."""
        searches = await asyncio.gather(
            *[
                self.safe_call(ctx, "web_research", "web_search", {"query": q[:300], "max_results": 4})
                for q in queries
            ]
        )
        hits: dict[str, dict[str, Any]] = {}
        for result in searches:
            if not isinstance(result, dict):
                continue
            if result.get("backend") == "fixture":
                note = "Web results came from the synthetic fixture corpus, not the live web."
                if note not in ctx.notes:
                    ctx.notes.append(note)
            for hit in result.get("results") or []:
                url = str(hit.get("url", "")).strip() if isinstance(hit, dict) else ""
                if url.startswith(("http://", "https://")) and url not in hits:
                    hits[url] = hit
        ordered = list(hits.items())[:max_hits]

        pages = await asyncio.gather(
            *[
                self.safe_call(ctx, "web_research", "fetch_page", {"url": url, "max_chars": 3000})
                for url, _hit in ordered[:max_pages]
            ]
        )
        evidence: list[Evidence] = []
        for index, (url, hit) in enumerate(ordered):
            page = pages[index] if index < len(pages) else None
            snippet = str(hit.get("snippet", ""))
            fetched = isinstance(page, dict) and bool(page.get("text"))
            evidence.append(
                Evidence(
                    source=Source(
                        url=url,
                        title=str(hit.get("title", ""))[:200],
                        snippet=snippet[:300],
                        server="web_research",
                        tool="fetch_page" if fetched else "web_search",
                    ),
                    content=str(page["text"]) if fetched else snippet,
                )
            )
        return evidence

    def extractive_findings(self, evidence: list[Evidence], limit: int = 6) -> list[Finding]:
        findings = []
        for item in evidence[:limit]:
            claim = first_sentences(item.content)
            if claim:
                findings.append(
                    Finding(
                        claim=claim,
                        kind="sourced_fact",
                        source_urls=[item.source.url],
                        confidence="low",
                    )
                )
        return findings

    def offline_summary(self, ctx: RunContext, evidence: list[Evidence]) -> str:
        return (
            f"Extractive summary (no language model was used): {len(evidence)} sources "
            f"were retrieved for '{ctx.topic}'. The findings are sentences taken from "
            "those sources, not a synthesised analysis."
        )

    OFFLINE_LIMITATION: ClassVar[str] = (
        "No language model was used, so nothing was synthesised or cross-checked."
    )

    # --------------------------------------------------------------- run
    def _sanitize(self, evidence: list[Evidence]) -> list[Evidence]:
        clean: list[Evidence] = []
        for item in evidence[: self.max_evidence_items]:
            origin = item.source.url
            content = sanitize_untrusted(item.content[: self.max_evidence_chars], origin=origin)
            title, _ = neutralize(item.source.title)
            snippet, _ = neutralize(item.source.snippet)
            clean.append(
                Evidence(
                    source=item.source.model_copy(update={"title": title, "snippet": snippet}),
                    content=content,
                    data=item.data,
                )
            )
        return clean

    async def run(self, task: AgentTaskInput) -> AgentResult:
        started = time.perf_counter()
        ctx = RunContext(
            task=task,
            topic=task.topic or derive_topic(task.query),
            tools=ToolClient(self.name, servers=self._servers, settings=self.settings),
        )
        usage = Usage()
        degraded, reason = False, ""

        with audit.collect(agent=self.name, request_id=task.request_id) as events:
            with span(f"agent.{self.skill}.gather"):
                evidence = self._sanitize(await self.gather(ctx))
            if not evidence:
                degraded, reason = True, "No evidence could be retrieved."

            with span(f"agent.{self.skill}.analyze", llm=self.llm.mode):
                from observability.tracing import langfuse_context

                with langfuse_context(
                    trace_name=f"agent.{self.skill}.analyze",
                    session_id=task.request_id,
                    tags=[self.name, self.skill, "agent-analysis"],
                    metadata={
                        "topic": ctx.topic,
                        "agent": self.name,
                        "skill": self.skill,
                        "evidence_count": len(evidence),
                    },
                ):
                    if self.llm.is_offline:
                        analysis = self.offline_analysis(ctx, evidence)
                    else:
                        try:
                            analysis, usage = await self.llm.structured(
                                self.system_prompt(),
                                self.user_prompt(ctx, evidence),
                                self.analysis_model,
                            )
                        except LLMError as exc:
                            # Fallback: a weaker but honest answer beats no answer.
                            analysis = self.offline_analysis(ctx, evidence)
                            degraded = True
                            reason = f"LLM unavailable, extractive fallback used ({exc})"[:300]

            analysis, _issues = validate_analysis(
                analysis,
                self.allowed_urls(ctx, evidence),
                secrets=self.settings.secret_values(),
                agent=self.name,
            )
            for note in [*ctx.notes, *([reason] if reason else [])]:
                if note not in analysis.limitations:
                    analysis.limitations.append(note)

            with span(f"agent.{self.skill}.post_process"):
                calculations = await self.post_process(ctx, analysis)

        return AgentResult(
            agent=self.name,
            skill=self.skill,
            analysis=json.loads(analysis.model_dump_json()),
            sources=self.result_sources(ctx, evidence),
            tool_calls=ctx.tools.records,
            calculations=calculations,
            usage=usage,
            security_events=events,
            degraded=degraded,
            degraded_reason=reason,
            llm_mode=self.llm.mode,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
