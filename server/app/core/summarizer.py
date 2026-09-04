"""定时总结调度器：每 N 秒把积累的句子送给 LLM 做滚动总结。"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class SummaryScheduler:
    """挂在一个 MeetingSession 上的后台任务。

    一期策略：固定间隔窗口（SUMMARY_INTERVAL_SEC）。
    二期可替换为「静音边界 + LLM 话题判断」的三层边界检测，
    接口不变：都是触发 session.run_summary()。
    """

    def __init__(self, session: "MeetingSession") -> None:  # noqa: F821 - 前向引用
        self._session = session
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        interval = settings.summary_interval_sec
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                break  # 被 stop() 唤醒
            except (TimeoutError, asyncio.TimeoutError):
                pass
            try:
                await self._session.run_summary(final=False)
            except Exception:
                logger.exception("periodic summary failed")

    async def stop(self, final_summary: bool = True) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if final_summary:
            await self._session.run_summary(final=True)
