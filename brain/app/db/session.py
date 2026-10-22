from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base


class Database:
    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self.maker: async_sessionmaker[AsyncSession] | None = None

    def setup(self, url: str) -> None:
        kwargs: dict = {}
        if url.startswith("sqlite"):
            if ":memory:" in url:
                kwargs |= {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
            else:
                Path(url.split("///", 1)[1]).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        else:
            kwargs |= {"pool_size": 10, "max_overflow": 10, "pool_pre_ping": True}
        self.engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name if self.engine else ""

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            if self.dialect == "postgresql":
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_add_missing_columns)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.maker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        if self.engine:
            await self.engine.dispose()


def _add_missing_columns(sync_conn) -> None:
    inspector = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing or not column.nullable:
                continue
            kind = column.type.compile(dialect=sync_conn.dialect)
            sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'))


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


db = Database()
