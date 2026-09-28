"""数据库连接与初始化（异步 SQLite，一期零运维）。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

# pool_pre_ping：回收任务取消时死掉的 aiosqlite 连接（checkout 后查询永久挂起的
# 事故 2026-09-28：final_pass 首条 SQL 挂死 12min+，根因=池内僵尸连接；
# pre_ping 检出即弃用换连）。pool_timeout：checkout 上限，防再次静默挂死。
engine = create_async_engine(settings.database_url, echo=False,
                             pool_pre_ping=True, pool_timeout=30)
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
        # 双版本架构（2026-09-28）：版本模式 + Summary 版本归属 + 会末待办聚合
        await add_column("meetings", "version_mode", "VARCHAR(8) DEFAULT 'both'")
        await add_column("meetings", "source_mode", "VARCHAR(8) DEFAULT 'mic'")
        await add_column("meetings", "action_items", "TEXT DEFAULT '[]'")
        await add_column("summaries", "scope", "VARCHAR(8) DEFAULT 'live'")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
