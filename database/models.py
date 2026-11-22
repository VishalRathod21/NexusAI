"""Database tables.

* users             - accounts: login, plan and role
* auth_tokens       - browser sessions and API keys (only a hash is stored)
* research_requests - one row per user request: status, plan and final report
* research_tasks    - one row per delegated agent task (execution history)
* security_events   - the audit trail

The report is stored as one JSON document because it is always read and
written whole. Tasks and security events are separate tables because they are
queried on their own (dashboards, filtering by agent or severity).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    plan: Mapped[str] = mapped_column(String(32), default="free")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuthTokenRow(Base):
    """A browser session or an API key. The token itself is never stored:
    only its SHA-256 hash, so a database leak does not expose usable tokens."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "session" | "api_key"
    name: Mapped[str] = mapped_column(String(80), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ResearchRequestRow(Base):
    __tablename__ = "research_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Tenant boundary: every read and write of a request is filtered by owner.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    # Unique: two submissions of the same request map to the same row.
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TaskRow(Base):
    __tablename__ = "research_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("research_requests.id"), index=True)
    skill: Mapped[str] = mapped_column(String(64))
    agent_name: Mapped[str] = mapped_column(String(120), default="")
    transport: Mapped[str] = mapped_column(String(16), default="a2a")
    a2a_task_id: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SecurityEventRow(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[str] = mapped_column(String(40))
