"""字数触发总结器：缓冲句子达到阈值（或结束时）触发一次总结。"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class SummaryTrigger:
    """挂在一个 MeetingSession 上的后台任务。

    策略：缓冲区达到 summary_max_chars 字数（由 buffer 内部累计触发信号）
    或结束时强制触发。触发即调 session.run_summary(final=...)，
    得到摘要后立即消费清空缓冲——不做滚动累积，只取当段原文给 LLM。
    """

    def __init__(self, session: "MeetingSession") -> None:  # noqa: F821
        self._session = session
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._threshold = settings.summary_max_chars

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=2.0)
                break
            except (TimeoutError, asyncio.TimeoutError):
                pass
            try:
                n = await self._session.buffer.pending_chars()
                if n >= self._threshold:
                    await self._session.run_summary(final=False)
            except Exception:
                logger.exception("char-triggered summary failed")

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
