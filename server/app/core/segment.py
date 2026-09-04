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
    """append-only 句子缓冲，draiｎ语义：取走即消费。"""

    _items: list[UtteranceItem] = field(default_factory=list)
    _next_seq: int = 1
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add(self, speaker: str, text: str, start_ms: int, end_ms: int) -> UtteranceItem:
        async with self._lock:
            item = UtteranceItem(self._next_seq, speaker, text, start_ms, end_ms)
            self._next_seq += 1
            self._items.append(item)
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

    async def pending_chars(self) -> int:
        """当前缓冲的字符总量（字数触发总结用）。"""
        async with self._lock:
            return sum(len(it.text) for it in self._items)

    async def full_text(self, max_chars: int = 100000) -> str:
        """从头到尾的全文（不消费），结束时整体提取属性用。"""
        async with self._lock:
            lines = [f"{it.speaker or '发言人'}: {it.text}" for it in self._items]
            return "\n".join(lines)[:max_chars]

    async def drain_text(self, max_chars: int) -> str:
        """取走并格式化为「说话人: 内容」，超出 max_chars 的部分留下次。"""
        items = await self.drain()
        lines = [f"{it.speaker or '发言人'}: {it.text}" for it in items]
        return "\n".join(lines)[:max_chars]

    async def __len__(self) -> int:  # type: ignore[override]
        async with self._lock:
            return len(self._items)
