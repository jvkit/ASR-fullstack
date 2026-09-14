"""批次增强：每个 /meeting_chunk 批次回来后，LLM 一次调用完成
修正上轮 + 纠错本轮 + 滚动改写纪要。

设计继承自原 RollingEnhancer 的「先出、后修」哲学：
- 批次的说话人标签由 GPU 管线（ER 逐句声纹）直接给出，这里不再打标
- 上轮因信息不足纠错的句子，本轮输入里仍带原文，LLM 有机会修正
- LLM 超时/失败：本轮滚入下批补纠，绝不重试排队

串行约束：同一时间最多一个在飞增强任务；在飞期间新来的批次 seq 攒进
_pending，在飞任务结束后立刻以合并 seq 触发下一批（不丢批、不并行炸网关）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class BatchEnhancer:
    """挂在 MeetingSession 上的批次增强器。所有回调非阻塞，失败仅记日志。"""

    def __init__(self, session) -> None:  # noqa: F821
        self._session = session
        self._task: asyncio.Task | None = None    # 在飞的一批（防重入）
        self._pending: list[int] = []             # 在飞期间攒下的句子 seq
        # 上一轮状态：LLM 输入上下文 + 失败补纠
        self._prev_raw: list[tuple[str, str]] = []      # 上轮原文 [(speaker, text)]
        self._prev_corrected: list[tuple[str, str]] = []  # 上轮纠正文（本轮可修正）
        self._prev_corrected_seqs: list[int] = []       # 上轮句子 seq（与纠正文行对应）
        self._pending_correction: bool = False          # 上轮纠错失败 → 本轮补纠
        self._stopped = False
        self.rolling_summary = ""   # 滚动纪要（最新一份，喂给下一轮和会末总结）
        self.summary_seq = 0

    # ---------- 触发（批次回调） ----------

    def on_batch(self, seqs: list[int]) -> None:
        """一批带说话人的句子已进 buffer：驱动一轮增强（后台，不阻塞采集）。"""
        if self._stopped or not seqs:
            return
        if self._task is not None and not self._task.done():
            self._pending.extend(seqs)
            return
        self._task = asyncio.get_running_loop().create_task(self._run(list(seqs)))

    async def _run(self, seqs: list[int]) -> None:
        try:
            await self._enhance(seqs)
        except Exception:
            logger.exception("batch enhance failed for %s", self._session.meeting_id)
        finally:
            self._task = None
            if self._pending:
                nxt, self._pending = self._pending, []
                self.on_batch(nxt)

    # ---------- 合并 LLM：纠错 + 滚动纪要 ----------

    async def _enhance(self, seqs: list[int]) -> None:
        """一次 LLM 调用完成：修正上轮 + 纠错本轮 + 滚动改写纪要。

        输入：上轮纪要 / 上轮原文+纠正文（可修正）/ 本轮原文（已带说话人标签）。
        输出 lines 行序 = [上轮行...] + [本轮行...]，行数不匹配整批回退。
        """
        items = await self._session.buffer.items_by_seqs(seqs)
        if not items:
            return
        cur_raw = [(it.speaker or "发言人", it.text) for it in items]

        # 上轮上下文：纠错成功带纠正文；失败带原文（本轮一并补纠）
        prev_lines: list[tuple[str, str]] = []
        if self._prev_corrected and not self._pending_correction:
            prev_lines = list(self._prev_corrected)
        elif self._prev_raw:
            prev_lines = list(self._prev_raw)

        result = await _llm_enhance(
            summary=self.rolling_summary,
            prev_lines=prev_lines,
            cur_lines=cur_raw,
        )
        if result is None:
            # LLM 失败：本轮滚入下批补纠（不丢原文）
            self._pending_correction = True
            self._prev_raw = cur_raw
            self._prev_corrected_seqs = [it.seq for it in items]
            return

        lines = result["lines"]
        n_prev, n_cur = len(prev_lines), len(cur_raw)
        if len(lines) != n_prev + n_cur:
            # 行数不匹配：整批回退（不纠错比错位好），滚入下批
            logger.warning(
                "enhancer %s: line count mismatch (got %d, want %d+%d), rollback",
                self._session.meeting_id, len(lines), n_prev, n_cur,
            )
            self._pending_correction = True
            self._prev_raw = cur_raw
            self._prev_corrected_seqs = [it.seq for it in items]
            return

        # 应用：先上轮修正，再本轮纠错
        updates: list[tuple[int, str, str]] = []  # (seq, speaker, text)
        prev_items = await self._session.buffer.items_by_seqs(self._prev_corrected_seqs) \
            if self._prev_corrected_seqs else []
        prev_by_seq = {it.seq: it for it in prev_items}
        for i, (spk, txt) in enumerate(lines[:n_prev]):
            seq = self._prev_corrected_seqs[i] if i < len(self._prev_corrected_seqs) else None
            it = prev_by_seq.get(seq) if seq else None
            if it is not None and txt and (txt != it.text or spk != it.speaker):
                it.text = txt
                it.speaker = spk
                updates.append((it.seq, spk, txt))
        for i, (spk, txt) in enumerate(lines[n_prev:]):
            it = items[i]
            if txt and (txt != it.text or spk != it.speaker):
                it.text = txt
                it.speaker = spk
                updates.append((it.seq, spk, txt))

        if updates:
            await self._persist_texts(updates)

        # 本轮批次内句子已全部定稿：即使文字未变也要广播 corrected=True，
        # 否则原样返回（无可纠）时前端草稿态（识别中）永远解除不了
        revised_prev = [it for it in prev_items if any(u[0] == it.seq for u in updates)]
        for it in revised_prev + items:
            await self._session.broadcast({
                "type": "transcript_update",
                "seq": it.seq,
                "speaker": it.speaker,
                "text": it.text,
                "start_ms": it.start_ms,
                "end_ms": it.end_ms,
                "corrected": True,
            })

        # 滚动纪要：改写式更新（先出、后修）+ 广播给前端
        if result["summary"]:
            self.rolling_summary = result["summary"]
            self._session.buffer.set_summary_hint(self.rolling_summary)
            self.summary_seq += 1
            points = [ln.lstrip("· ").strip() for ln in result["summary"].splitlines() if ln.strip()]
            await self._session.broadcast({
                "type": "summary",
                "seq": self.summary_seq,
                "final": False,
                "title": "滚动纪要",
                "points": points,
                "action_items": [],
                "attributes": [],
            })

        # 记录本轮为「上一轮」，供下批修正
        self._prev_raw = cur_raw
        self._prev_corrected = [(spk, txt) for spk, txt in lines[n_prev:]]
        self._prev_corrected_seqs = [it.seq for it in items]
        self._pending_correction = False

    async def _persist_texts(self, updates: list[tuple[int, str, str]]) -> None:
        from app.db.database import SessionLocal
        from app.db.models import Utterance

        async with SessionLocal() as db:
            for seq, spk, txt in updates:
                await db.execute(
                    Utterance.__table__.update()
                    .where(Utterance.meeting_id == self._session.meeting_id)
                    .where(Utterance.seq == seq)
                    .values(speaker=spk, text=txt)
                )
            await db.commit()

    # ---------- 会末收尾 ----------

    async def finish(self) -> str:
        """会议结束：等在飞一批跑完（有上限），返回滚动纪要供会末总结。"""
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=settings.roll_llm_timeout_sec + 10)
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("meeting %s: enhance timeout on finish", self._session.meeting_id)
            except Exception:
                pass
        self._stopped = True
        return self.rolling_summary


async def _llm_enhance(summary: str, prev_lines: list[tuple[str, str]],
                       cur_lines: list[tuple[str, str]]) -> dict | None:
    """合并 LLM 调用：纠错（含上轮修正）+ 滚动纪要改写。失败/超时返回 None。

    Langfuse 经 openai wrapper 自动埋点。超时 roll_llm_timeout_sec 秒即放弃，
    绝不重试（网关抽风时秒失败，批次滚入下批补纠）。
    """
    from app.llm import client as llm_client

    messages = llm_client.build_enhance_messages(summary, prev_lines, cur_lines)
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                llm_client.client.chat.completions.create,
                model=settings.llm_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.0,
                response_format={"type": "json_object"},
            ),
            timeout=settings.roll_llm_timeout_sec,
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        lines = data.get("lines", [])
        out = [(str(d.get("speaker", "")).strip(), str(d.get("text", "")).strip())
               for d in lines if isinstance(d, dict)]
        return {"lines": out, "summary": str(data.get("summary", "")).strip()}
    except Exception:
        logger.exception("llm enhance failed")
        return None
