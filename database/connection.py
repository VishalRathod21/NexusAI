"""Async database engine and sessions.

The same code runs on SQLite (zero-setup local development) and PostgreSQL
(Docker / production): only `DATABASE_URL` changes.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base

logger = logging.getLogger(__name__)


class SchemaOutdated(RuntimeError):
    pass


def _missing_columns(connection) -> list[str]:
    """Columns the models expect but the existing tables do not have."""
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in present]
    return missing


class Database:
    def __init__(self, url: str):
        self.url = url
        self._connect()

    def _connect(self) -> None:
        self.engine: AsyncEngine = create_async_engine(self.url, pool_pre_ping=True)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    def _sqlite_file(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if not self.url.startswith(prefix) or ":memory:" in self.url:
            return None
        return Path(self.url[len(prefix):])

    async def init(self) -> None:
        """Create tables if they do not exist.

        There is no migration tool here. `create_all` adds missing tables but
        never changes an existing one, so a database created by an older
        version of the code is detected instead of failing later with obscure
        SQL errors:

        * local SQLite file - moved aside (kept as a backup) and recreated
        * anything else     - startup stops with a clear message

        A real deployment would use versioned migrations (Alembic).
        """
        async with self.engine.begin() as connection:
            missing = await connection.run_sync(_missing_columns)
        if missing:
            path = self._sqlite_file()
            if path is None or not path.exists():
                raise SchemaOutdated(
                    f"The database schema is older than the code (missing: {', '.join(missing[:5])}). "
                    "Migrate it or point DATABASE_URL at a new database."
                )
            await self.engine.dispose()
            backup = path.with_name(f"{path.stem}.backup-{int(time.time())}{path.suffix}")
            try:
                path.rename(backup)
            except OSError as exc:  # typically: another NexusAI process has the file open
                raise SchemaOutdated(
                    f"{path.name} was created by an older version and is in use by another "
                    "process. Stop the other NexusAI processes and start again."
                ) from exc
            logger.warning(
                "Database schema was from an older version. Old data kept in %s; "
                "a new empty database was created.",
                backup.name,
            )
            self._connect()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
