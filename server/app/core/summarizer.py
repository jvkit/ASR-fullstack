"""总结调度器：定时/字数双触发滚动总结 + 按累计字数触发属性提取。

- 总结（summary）：回到定时窗口策略——每 SUMMARY_INTERVAL_SEC 秒且句子数达标触发一次，
  单次送 LLM 的文本以 SUMMARY_MAX_CHARS 为上限（多了留到下次）。
- 属性提取（attributes）：全文累计每达到 ATTR_TRIGGER_CHARS 字触发一次，
  提取「事实性属性 + 会议主题」，结果累积合并到 session.final_attributes。
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class SummaryScheduler:
    """挂在一个 MeetingSession 上的后台任务。"""

    def __init__(self, session: "MeetingSession") -> None:  # noqa: F821
        self._session = session
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._attr_seen_seq = 0  # 属性提取已处理到的句子 seq

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
                await self._maybe_extract_attributes()
            except Exception:
                logger.exception("periodic summary failed")

    async def _maybe_extract_attributes(self) -> None:
        """累计新增字符达到阈值则对新增句子提取属性，合并进 final_attributes。"""
        total = await self._session.buffer.total_chars()
        if total < settings.attr_trigger_chars:
            return
        new_text = await self._session.buffer.text_from(self._attr_seen_seq)
        if not new_text.strip():
            return
        self._attr_seen_seq = await self._session.buffer.last_seq()
        await self._session.extract_attributes(new_text)

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
            # 收尾时剩余未提取的句子也提取一次，不丢尾巴
            new_text = await self._session.buffer.text_from(self._attr_seen_seq)
            if new_text.strip():
                self._attr_seen_seq = await self._session.buffer.last_seq()
                await self._session.extract_attributes(new_text)
