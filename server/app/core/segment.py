"""句子缓冲：收集 ASR offline 结果，按窗口供给总结器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class UtteranceItem:
    seq: int
    speaker: str
    text: str
    start_ms: int
    end_ms: int


@dataclass
class UtteranceBuffer:
    """append-only 句子缓冲：
    - _items：待总结窗口（drain 消费）
    - _all：会议全量累积（属性提取用，drain 不影响）
    """

    _items: list[UtteranceItem] = field(default_factory=list)
    _all: list[UtteranceItem] = field(default_factory=list)
    _next_seq: int = 1
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add(self, speaker: str, text: str, start_ms: int, end_ms: int) -> UtteranceItem:
        async with self._lock:
            item = UtteranceItem(self._next_seq, speaker, text, start_ms, end_ms)
            self._next_seq += 1
            self._items.append(item)
            self._all.append(item)
            return item

    async def drain(self) -> list[UtteranceItem]:
        """取走全部未消费句子（总结窗口用）。"""
        async with self._lock:
            items, self._items = self._items, []
            return items

    async def peek_text(self, max_chars: int) -> str:
        """预览（不消费）格式化文本，用于总结前判断窗口是否够料。"""
        async with self._lock:
            lines = [f"{it.speaker or '发言人'}: {it.text}" for it in self._items]
            return "\n".join(lines)[:max_chars]

    async def pending_count(self) -> int:
        async with self._lock:
            return len(self._items)

    async def total_chars(self) -> int:
        """会议全量累计字符量（仅统计）。"""
        async with self._lock:
            return sum(len(it.text) for it in self._all)

    async def text_from(self, item_seq: int) -> str:
        """seq > item_seq 的所有句子（属性提取用，不消费，按句子游标避免字符错位）。"""
        async with self._lock:
            lines = [f"{it.speaker or '发言人'}: {it.text}" for it in self._all if it.seq > item_seq]
            return "\n".join(lines)

    async def last_seq(self) -> int:
        async with self._lock:
            return self._all[-1].seq if self._all else 0

    async def full_text(self, max_chars: int = 200000) -> str:
        """会议全量文本（不消费，含已被总结消费的句子）。"""
        async with self._lock:
            lines = [f"{it.speaker or '发言人'}: {it.text}" for it in self._all]
            return "\n".join(lines)[:max_chars]

    async def drain_text(self, max_chars: int) -> str:
        """取走并格式化为「说话人: 内容」，超出 max_chars 的部分留下次。"""
        items = await self.drain()
        lines = [f"{it.speaker or '发言人'}: {it.text}" for it in items]
        return "\n".join(lines)[:max_chars]

    async def __len__(self) -> int:  # type: ignore[override]
        async with self._lock:
            return len(self._items)
