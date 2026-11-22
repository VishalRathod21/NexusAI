"""Application service: the lifecycle of a research request.

    submit -> validate -> limits -> (duplicate?) -> plan -> [awaiting_approval]
           -> running -> completed | failed

Every method takes the acting `User`. That is the tenant boundary: a user can
only see and act on their own requests.

Controls that live here:

* Idempotency    - the same request submitted twice by the same user returns
                   the existing one. Enforced by a unique database constraint,
                   so it also holds under a race.
* Human approval - with `require_approval`, nothing is delegated until the
                   user approves the plan.
* Plan limits    - submissions per minute, concurrent runs, and reports per
                   month, all taken from the user's plan.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.auth import User
from backend.config.settings import Settings
from backend.orchestrator.orchestrator import Orchestrator
from backend.orchestrator.state import ResearchPlan, TaskRecord
from backend.plans import get_plan
from backend.rate_limit import RateLimiter
from database.repository import DuplicateRequest, Repository
from observability import metrics
from security import audit
from security.input_validation import InputValidationError, validate_query

logger = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


class LimitExceeded(Exception):
    """A plan limit was hit. `status` is the HTTP status to return."""

    def __init__(self, code: str, message: str, status: int, retry_after: float = 0):
        super().__init__(message)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


def idempotency_key(user_id: str, query: str) -> str:
    normalized = " ".join(query.lower().split())
    return f"{user_id}:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class ResearchService:
    def __init__(self, settings: Settings, repository: Repository, orchestrator: Orchestrator):
        self.settings = settings
        self.repo = repository
        self.orchestrator = orchestrator
        self.limiter = RateLimiter()
        self._running: set[asyncio.Task[None]] = set()

    # --------------------------------------------------------------- usage
    async def usage(self, user: User) -> dict[str, Any]:
        plan = get_plan(user.plan)
        used = await self.repo.count_requests_since(user.id, month_start())
        return {
            "plan": plan.id,
            "plan_name": plan.name,
            "reports_used": used,
            "reports_limit": plan.reports_per_month,
            "reports_remaining": max(0, plan.reports_per_month - used),
            "active_runs": await self.repo.count_active(user.id),
            "concurrent_runs_limit": plan.concurrent_runs,
            "submissions_per_minute": plan.submissions_per_minute,
            "api_keys_limit": plan.api_keys,
            "period_start": month_start().isoformat(),
        }

    async def _enforce_limits(self, user: User) -> None:
        plan = get_plan(user.plan)
        wait = self.limiter.allow(f"submit:{user.id}", plan.submissions_per_minute, 60)
        if wait:
            raise LimitExceeded(
                "rate_limited",
                f"Too many requests. Your plan allows {plan.submissions_per_minute} per minute.",
                status=429,
                retry_after=wait,
            )
        if await self.repo.count_active(user.id) >= plan.concurrent_runs:
            raise LimitExceeded(
                "too_many_running",
                f"Your plan runs {plan.concurrent_runs} request(s) at a time. Wait for one to finish.",
                status=429,
                retry_after=15,
            )
        if await self.repo.count_requests_since(user.id, month_start()) >= plan.reports_per_month:
            raise LimitExceeded(
                "quota_exceeded",
                f"You have used all {plan.reports_per_month} reports included in the "
                f"{plan.name} plan this month.",
                status=402,
            )

    # -------------------------------------------------------------- submit
    async def submit(
        self, user: User, query: str, require_approval: bool = False, force_new: bool = False
    ) -> tuple[dict[str, Any], bool]:
        """Returns (request, is_duplicate). Raises InputValidationError or LimitExceeded."""
        request_id = str(uuid.uuid4())
        with audit.collect(agent="api", request_id=request_id) as events:
            try:
                validated = validate_query(query, request_id=request_id)
            except InputValidationError:
                await self.repo.add_security_events(events, user_id=user.id)
                raise
        await self.repo.add_security_events(events, user_id=user.id)

        key = idempotency_key(user.id, validated.text)
        if force_new:
            key = f"{key}:{request_id}"
        else:
            existing = await self.repo.find_by_key(key)
            if existing and existing["status"] != "failed":
                return existing, True  # a duplicate costs nothing, so no limits apply
            if existing:  # a failed run may be retried under a fresh key
                key = f"{key}:{request_id}"

        await self._enforce_limits(user)
        try:
            await self.repo.create_request(request_id, user.id, validated.text, key, status="planning")
        except DuplicateRequest:
            # Lost a race with an identical submission: return the winner.
            existing = await self.repo.find_by_key(key)
            if existing:
                return existing, True
            raise

        plan, _usage = await self.orchestrator.plan(validated.text)
        status = "awaiting_approval" if require_approval else "approved"
        await self.repo.update_request(request_id, plan=plan.model_dump(mode="json"), status=status)
        if not require_approval:
            await self._start(request_id, from_status="approved")
        request = await self.repo.get_request(request_id, user.id)
        assert request is not None
        return request, False

    async def approve(self, user: User, request_id: str) -> dict[str, Any]:
        request = await self.repo.get_request(request_id, user.id)
        if request is None:
            raise NotFound(request_id)
        if not await self._start(request_id, from_status="awaiting_approval"):
            raise InvalidState(f"request is '{request['status']}', not awaiting approval")
        updated = await self.repo.get_request(request_id, user.id)
        assert updated is not None
        return updated

    async def _start(self, request_id: str, from_status: str) -> bool:
        if not await self.repo.claim_for_run(request_id, from_status):
            return False
        task = asyncio.create_task(self._run(request_id))
        self._running.add(task)  # keep a reference so it is not garbage collected
        task.add_done_callback(self._running.discard)
        return True

    # ----------------------------------------------------------------- run
    async def _run(self, request_id: str) -> None:
        request = await self.repo.get_request(request_id)
        if request is None:
            return
        try:
            plan = ResearchPlan.model_validate(request["plan"])

            async def on_task(record: TaskRecord) -> None:
                await self.repo.add_task(request_id, record)

            report = await self.orchestrator.execute(
                request_id,
                request["query"],
                plan,
                on_task,
                user_id=request.get("user_id"),
            )
            await self.repo.add_security_events(report.security_events, request_id, request["user_id"])
            await self.repo.update_request(
                request_id, status="completed", report=report.model_dump(mode="json"), error=None
            )
        except Exception as exc:
            logger.exception("research %s failed", request_id)
            await self.repo.update_request(
                request_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:500]
            )
        finally:
            from observability.tracing import langfuse_flush

            langfuse_flush()

    async def wait_idle(self) -> None:
        """Wait for background runs (used by tests and on shutdown)."""
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)
        from observability.tracing import langfuse_flush

        langfuse_flush()

    # ---------------------------------------------------------------- read
    async def get(self, user: User, request_id: str) -> dict[str, Any]:
        request = await self.repo.get_request(request_id, user.id)
        if request is None:
            raise NotFound(request_id)  # also the answer for someone else's request
        request["tasks"] = await self.repo.list_tasks(request_id)
        return request

    async def list(self, user: User, limit: int = 50) -> list[dict[str, Any]]:
        return await self.repo.list_requests(user.id, limit)

    async def security_events(self, user: User, limit: int = 100, everyone: bool = False) -> list[dict[str, Any]]:
        scope = None if (everyone and user.is_admin) else user.id
        return await self.repo.list_security_events(scope, limit)

    async def metrics(self, user: User, everyone: bool = False) -> dict[str, Any]:
        scope = None if (everyone and user.is_admin) else user.id
        requests = await self.repo.list_requests(scope, limit=200, with_reports=True)
        reports = [r["report"] for r in requests if r.get("report")]
        summary = metrics.aggregate(reports)
        summary["requests_by_status"] = {}
        for request in requests:
            status = request["status"]
            summary["requests_by_status"][status] = summary["requests_by_status"].get(status, 0) + 1
        return summary
