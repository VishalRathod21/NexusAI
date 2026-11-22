"""All database access in one place (repository pattern).

The service layer calls these methods and never writes SQL, so storage can
change without touching business logic, and tests can swap in a throwaway
SQLite database.

Tenant isolation lives here: every method that reads or changes a research
request takes the owner's `user_id` and puts it in the WHERE clause. A request
that belongs to someone else is simply not found.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from backend.orchestrator.state import SecurityEvent, TaskRecord
from database.connection import Database
from database.models import (
    AuthTokenRow,
    ResearchRequestRow,
    SecurityEventRow,
    TaskRow,
    UserRow,
)

ACTIVE_STATUSES = ("planning", "approved", "running")


class DuplicateRequest(Exception):
    """Another request with the same idempotency key already exists."""


class DuplicateUser(Exception):
    """An account with this email already exists."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def request_to_dict(row: ResearchRequestRow, include_report: bool = True) -> dict[str, Any]:
    data = {
        "id": row.id,
        "user_id": row.user_id,
        "query": row.query,
        "status": row.status,
        "plan": row.plan,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_report:
        data["report"] = row.report
    return data


def user_to_dict(row: UserRow, include_hash: bool = True) -> dict[str, Any]:
    data = {
        "id": row.id,
        "email": row.email,
        "name": row.name,
        "plan": row.plan,
        "is_admin": row.is_admin,
        "is_active": row.is_active,
        "created_at": _iso(row.created_at),
    }
    if include_hash:
        data["password_hash"] = row.password_hash
    return data


def token_to_dict(row: AuthTokenRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "created_at": _iso(row.created_at),
        "last_used_at": _iso(row.last_used_at),
    }


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # -------------------------------------------------------------- users
    async def count_users(self) -> int:
        async with self.db.session() as session:
            return await session.scalar(select(func.count()).select_from(UserRow)) or 0

    async def count_admins(self) -> int:
        async with self.db.session() as session:
            return (
                await session.scalar(
                    select(func.count()).select_from(UserRow).where(UserRow.is_admin, UserRow.is_active)
                )
                or 0
            )

    async def create_user(
        self, user_id: str, email: str, name: str, password_hash: str, plan: str, is_admin: bool
    ) -> dict[str, Any]:
        row = UserRow(
            id=user_id, email=email, name=name, password_hash=password_hash, plan=plan, is_admin=is_admin
        )
        async with self.db.session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateUser(email) from exc
        return user_to_dict(row)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        async with self.db.session() as session:
            row = await session.get(UserRow, user_id)
            return user_to_dict(row) if row else None

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        async with self.db.session() as session:
            row = await session.scalar(select(UserRow).where(UserRow.email == email))
            return user_to_dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            users = (await session.scalars(select(UserRow).order_by(UserRow.created_at))).all()
            counts = dict(
                (
                    await session.execute(
                        select(ResearchRequestRow.user_id, func.count()).group_by(ResearchRequestRow.user_id)
                    )
                ).all()
            )
            return [
                {**user_to_dict(user, include_hash=False), "requests_total": counts.get(user.id, 0)}
                for user in users
            ]

    async def update_user(self, user_id: str, **fields: Any) -> None:
        async with self.db.session() as session:
            await session.execute(update(UserRow).where(UserRow.id == user_id).values(**fields))
            await session.commit()

    async def delete_user_data(self, user_id: str) -> None:
        """Remove an account and everything it owns."""
        async with self.db.session() as session:
            request_ids = select(ResearchRequestRow.id).where(ResearchRequestRow.user_id == user_id)
            await session.execute(delete(TaskRow).where(TaskRow.request_id.in_(request_ids)))
            await session.execute(delete(SecurityEventRow).where(SecurityEventRow.user_id == user_id))
            await session.execute(delete(ResearchRequestRow).where(ResearchRequestRow.user_id == user_id))
            await session.execute(delete(AuthTokenRow).where(AuthTokenRow.user_id == user_id))
            await session.execute(delete(UserRow).where(UserRow.id == user_id))
            await session.commit()

    # ------------------------------------------------- sessions / API keys
    async def add_token(
        self,
        user_id: str,
        kind: str,
        token_hash: str,
        name: str = "",
        prefix: str = "",
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        row = AuthTokenRow(
            user_id=user_id, kind=kind, token_hash=token_hash, name=name, prefix=prefix, expires_at=expires_at
        )
        async with self.db.session() as session:
            session.add(row)
            await session.commit()
        return token_to_dict(row)

    async def get_token(self, token_hash: str) -> dict[str, Any] | None:
        async with self.db.session() as session:
            row = await session.scalar(select(AuthTokenRow).where(AuthTokenRow.token_hash == token_hash))
            if not row:
                return None
            return {
                "id": row.id,
                "user_id": row.user_id,
                "kind": row.kind,
                "token_hash": row.token_hash,
                "expires_at": row.expires_at,
            }

    async def touch_token(self, token_id: int) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(AuthTokenRow)
                .where(AuthTokenRow.id == token_id)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await session.commit()

    async def list_tokens(self, user_id: str, kind: str) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            rows = await session.scalars(
                select(AuthTokenRow)
                .where(AuthTokenRow.user_id == user_id, AuthTokenRow.kind == kind)
                .order_by(AuthTokenRow.id)
            )
            return [token_to_dict(row) for row in rows]

    async def delete_token(self, user_id: str, token_id: int, kind: str) -> bool:
        async with self.db.session() as session:
            result = await session.execute(
                delete(AuthTokenRow).where(
                    AuthTokenRow.id == token_id, AuthTokenRow.user_id == user_id, AuthTokenRow.kind == kind
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def delete_token_by_hash(self, token_hash: str) -> None:
        async with self.db.session() as session:
            await session.execute(delete(AuthTokenRow).where(AuthTokenRow.token_hash == token_hash))
            await session.commit()

    async def delete_tokens(self, user_id: str, kind: str | None = None) -> None:
        statement = delete(AuthTokenRow).where(AuthTokenRow.user_id == user_id)
        if kind:
            statement = statement.where(AuthTokenRow.kind == kind)
        async with self.db.session() as session:
            await session.execute(statement)
            await session.commit()

    # ----------------------------------------------------------- requests
    async def create_request(
        self, request_id: str, user_id: str, query: str, idempotency_key: str, status: str
    ) -> dict[str, Any]:
        row = ResearchRequestRow(
            id=request_id, user_id=user_id, query=query, idempotency_key=idempotency_key, status=status
        )
        async with self.db.session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateRequest(idempotency_key) from exc
        return request_to_dict(row)

    async def get_request(self, request_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        """`user_id=None` is for internal use only (the background runner)."""
        async with self.db.session() as session:
            row = await session.get(ResearchRequestRow, request_id)
            if not row or (user_id is not None and row.user_id != user_id):
                return None
            return request_to_dict(row)

    async def find_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        async with self.db.session() as session:
            row = await session.scalar(
                select(ResearchRequestRow).where(ResearchRequestRow.idempotency_key == idempotency_key)
            )
            return request_to_dict(row) if row else None

    async def list_requests(
        self, user_id: str | None, limit: int = 50, with_reports: bool = False
    ) -> list[dict[str, Any]]:
        statement = select(ResearchRequestRow).order_by(ResearchRequestRow.created_at.desc()).limit(limit)
        if user_id is not None:
            statement = statement.where(ResearchRequestRow.user_id == user_id)
        async with self.db.session() as session:
            rows = await session.scalars(statement)
            return [request_to_dict(row, include_report=with_reports) for row in rows]

    async def count_requests_since(self, user_id: str, since: datetime) -> int:
        """Requests that count against the monthly quota (failed runs do not)."""
        async with self.db.session() as session:
            return (
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchRequestRow)
                    .where(
                        ResearchRequestRow.user_id == user_id,
                        ResearchRequestRow.created_at >= since,
                        ResearchRequestRow.status != "failed",
                    )
                )
                or 0
            )

    async def count_active(self, user_id: str) -> int:
        async with self.db.session() as session:
            return (
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchRequestRow)
                    .where(
                        ResearchRequestRow.user_id == user_id,
                        ResearchRequestRow.status.in_(ACTIVE_STATUSES),
                    )
                )
                or 0
            )

    async def update_request(self, request_id: str, **fields: Any) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(ResearchRequestRow).where(ResearchRequestRow.id == request_id).values(**fields)
            )
            await session.commit()

    async def claim_for_run(self, request_id: str, from_status: str) -> bool:
        """Move a request to `running` only if it is still in `from_status`.

        The UPDATE ... WHERE status = ? is atomic, so if two approvals arrive
        at once only one of them starts the run.
        """
        async with self.db.session() as session:
            result = await session.execute(
                update(ResearchRequestRow)
                .where(ResearchRequestRow.id == request_id, ResearchRequestRow.status == from_status)
                .values(status="running")
            )
            await session.commit()
            return result.rowcount == 1

    async def fail_interrupted(self) -> int:
        """Requests left unfinished by a previous process can never finish."""
        async with self.db.session() as session:
            result = await session.execute(
                update(ResearchRequestRow)
                .where(ResearchRequestRow.status.in_(ACTIVE_STATUSES))
                .values(status="failed", error="interrupted: the backend restarted during this run")
            )
            await session.commit()
            return result.rowcount or 0

    # -------------------------------------------------------------- tasks
    async def add_task(self, request_id: str, record: TaskRecord) -> None:
        row = TaskRow(
            request_id=request_id,
            skill=record.skill,
            agent_name=record.agent_name,
            transport=record.transport,
            a2a_task_id=record.a2a_task_id,
            state=record.state,
            attempts=record.attempts,
            latency_ms=record.latency_ms,
            error=record.error,
            record=record.model_dump(mode="json"),
        )
        async with self.db.session() as session:
            session.add(row)
            await session.commit()

    async def list_tasks(self, request_id: str) -> list[dict[str, Any]]:
        async with self.db.session() as session:
            rows = await session.scalars(
                select(TaskRow).where(TaskRow.request_id == request_id).order_by(TaskRow.id)
            )
            return [row.record for row in rows]

    # ----------------------------------------------------- security events
    async def add_security_events(
        self, events: list[SecurityEvent], request_id: str = "", user_id: str = ""
    ) -> None:
        if not events:
            return
        async with self.db.session() as session:
            session.add_all(
                SecurityEventRow(
                    request_id=event.request_id or request_id,
                    user_id=user_id,
                    event_type=event.event_type,
                    severity=event.severity,
                    agent=event.agent,
                    detail=event.detail,
                    occurred_at=event.timestamp,
                )
                for event in events
            )
            await session.commit()

    async def list_security_events(self, user_id: str | None, limit: int = 100) -> list[dict[str, Any]]:
        statement = select(SecurityEventRow).order_by(SecurityEventRow.id.desc()).limit(limit)
        if user_id is not None:
            statement = statement.where(SecurityEventRow.user_id == user_id)
        async with self.db.session() as session:
            rows = await session.scalars(statement)
            return [
                {
                    "request_id": row.request_id,
                    "user_id": row.user_id,
                    "event_type": row.event_type,
                    "severity": row.severity,
                    "agent": row.agent,
                    "detail": row.detail,
                    "timestamp": row.occurred_at,
                }
                for row in rows
            ]
