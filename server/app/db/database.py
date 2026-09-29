"""数据库连接与初始化（异步 MySQL，本地/服务器双配置）。

2026-09-29 由 SQLite 迁移到 MySQL(asyncmy)。启动时 create_all 自动建表；
SQLite 旧数据已弃用，不再迁移。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

# pool_pre_ping：回收死连接；pool_recycle：MySQL 默认 8h 超时，提前回收
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_timeout=30,
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
