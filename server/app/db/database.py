"""数据库连接与初始化（异步 SQLite，一期零运维）。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite 轻量迁移：已存在的表补新列（create_all 不会改已有表）
        from sqlalchemy import text

        async def add_column(table: str, col: str, ddl: str) -> None:
            res = await conn.execute(text(f"PRAGMA table_info({table})"))
            if col not in {r[1] for r in res.fetchall()}:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

        await add_column("utterances", "char_timestamps", "TEXT DEFAULT '[]'")
        await add_column("utterances_re", "text", "TEXT DEFAULT ''")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
