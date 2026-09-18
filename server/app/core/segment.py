"""句子缓冲：收集 ASR offline 结果，按窗口供给总结器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


def merge_display_rows(rows: list[dict], gap_ms: int) -> list[dict]:
    """显示层合并：同 speaker 且间隙 <= gap_ms 的相邻行并成一行（纯展示，不动数据）。

    2026-09-18 配合 VAD 回 250ms 的碎段治理：DB 保留原子段（LLM 纠错、
    滚动纪要、GT 评测都按原子段工作），仅在详情接口序列化时合并视图。
    行格式 {"seq","speaker","text","start_ms","end_ms"}；text 直接拼接
    （CT-Transformer 标点已按段补齐，拼接后语义连贯）。
    """
    out: list[dict] = []
    for r in rows:
        if out and out[-1]["speaker"] == r["speaker"] \
                and r["start_ms"] - out[-1]["end_ms"] <= gap_ms:
            prev = out[-1]
            prev["end_ms"] = r["end_ms"]
            prev["text"] = (prev["text"] + (r["text"] or "")).strip()
        else:
            out.append(dict(r))
    return out


@dataclass
class UtteranceItem:
    seq: int
    speaker: str
    text: str
    start_ms: int
    end_ms: int
    char_ts: list | None = None  # 字级时间戳 [[start_ms, end_ms], ...]（会后版按字分配用）


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

    async def add(
        self, speaker: str, text: str, start_ms: int, end_ms: int, char_ts: list | None = None
    ) -> UtteranceItem:
        async with self._lock:
            item = UtteranceItem(self._next_seq, speaker, text, start_ms, end_ms, char_ts=char_ts)
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

    async def items_in(self, start_ms: int, end_ms: int) -> list[UtteranceItem]:
        """时间区间 [start_ms, end_ms) 内的句子（滚动打标贴段用）。"""
        async with self._lock:
            return [
                it for it in self._all
                if it.start_ms < end_ms and it.end_ms > start_ms
            ]

    async def items_by_seqs(self, seqs: list[int]) -> list[UtteranceItem]:
        """按 seq 取句子（滚动纠错窗口用），保持 seq 升序。"""
        want = set(seqs)
        async with self._lock:
            return [it for it in self._all if it.seq in want]

    def items_by_seqs_nowait(self, seqs: list[int]) -> list[UtteranceItem]:
        """同步版 items_by_seqs：供句终回调等同步上下文使用（无 await）。

        注意：append-only 列表读取在 CPython 单事件循环下是安全的。
        """
        want = set(seqs)
        return [it for it in self._all if it.seq in want]

    async def recent_summary_hint(self) -> str:
        """最近一次总结要点（纠错时给 LLM 当全局上下文）。"""
        # 由 session 注入（避免循环引用），返回缓存的 prev_summary
        hint = getattr(self, "_summary_hint", "")
        return str(hint)

    def set_summary_hint(self, hint: str) -> None:
        self._summary_hint = hint

    async def drain_text(self, max_chars: int) -> str:
        """取走并格式化为「说话人: 内容」，超出 max_chars 的部分留下次。"""
        items = await self.drain()
        lines = [f"{it.speaker or '发言人'}: {it.text}" for it in items]
        return "\n".join(lines)[:max_chars]

    async def __len__(self) -> int:  # type: ignore[override]
        async with self._lock:
            return len(self._items)
