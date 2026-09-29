"""波段增强：每 summary_every_n_batches 个 L1 批次为一「波」，一波一次 LLM 调用完成
纠错本波 + 订正上一波（文本与纪要）+ 出本波段纪要。

设计（2026-09-21 起，取代旧的「每批改写同一份滚动纪要」）：
- **波按批次计数驱动，不按墙上时钟**：L1 批次长度随 VAD 句尾切点浮动，用时钟会漂移。
- **段纪要 append 不覆盖**：每波独立成段，落一行 Summary（seq 递增）。旧设计里
  40min 与 6h 会议都只输出一份 3-8 条纪要，长会议信息必然丢失。
- **会末大总结只看段纪要**（不碰原始全文），故输入规模 = 波数 × 每波纪要，
  与会议原始文本量解耦；6h 会议约 94k token，在 chat 模型 256k 窗口内。
- **上下文滑动窗口**：每波只带前 summary_context_segments 波纪要，输入恒定 ≈4.2k
  token。防人名/术语漂移，同时避免 6h 会议把 360 段全带上。
- **失败不兜底**（用户决定）：某波 LLM 失败即记日志跳过——该波无段纪要、文字留
  草稿态；但下一波仍会拿到本波原文（2 波原文窗口），纠错自动延后一波补上。
  不做累积补做，避免「雪球」：网关排队 19~230s，累积越多输出越长越易超时。

串行约束：同一时间最多一个在飞波；在飞期间新批次继续累积进 _wave_seqs，
在飞结束后立刻补跑（若期间攒了超过一波的量，则合成一个更长的波跑掉——不丢不重）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.config import settings
from app.core.visual_ocr import visual_ocr
from app.core.visual_storage import save_visual_frames
from app.db import event_stages
from app.db.events import log_event

logger = logging.getLogger(__name__)


def _mmss(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


async def persist_visual_note(meeting_id: str, start_ms: int, end_ms: int,
                              text: str, frames: list[tuple[int, bytes]] | None = None) -> int | None:
    """视觉 OCR 文本 + 帧图落 VisualNote 存档。返回新记录 id，空文本返回 None。

    会末版按 [start_ms, end_ms] 时间范围关联复用，不重跑 OCR（2026-09-28 双版本
    架构：两版只复用视觉结果）。
    """
    if not text.strip():
        return None
    import json

    from app.db.database import SessionLocal
    from app.db.models import VisualNote

    frame_paths: list[str] = []
    if frames:
        frame_paths = save_visual_frames(meeting_id, 0, frames)

    async with SessionLocal() as db:
        note = VisualNote(
            meeting_id=meeting_id,
            start_ms=int(start_ms),
            end_ms=int(end_ms),
            text=text,
            frame_paths=json.dumps(frame_paths, ensure_ascii=False),
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        return note.id


class BatchEnhancer:
    """挂在 MeetingSession 上的波段增强器。所有回调非阻塞，失败仅记日志。"""

    def __init__(self, session) -> None:  # noqa: F821
        self._session = session
        self._task: asyncio.Task | None = None    # 在飞的一波（防重入）
        # 当前波累积（未满 summary_every_n_batches 批不触发）
        self._wave_seqs: list[int] = []
        self._wave_batches: int = 0
        # 上一波状态：供下一波订正（纠正文优先；该波失败时存原文，下一波补纠）
        self._prev_lines: list[tuple[str, str]] = []
        self._prev_seqs: list[int] = []
        # 已定稿段纪要（append-only）：{seq,title,content,start_ms,end_ms,window_text}
        self._segments: list[dict] = []
        self.summary_seq = 0     # 唯一 seq 计数器；会末终版经 next_seq() 取号，不撞号
        self._stopped = False

    # ---------- 触发（L1 批次回调） ----------

    def on_batch(self, seqs: list[int]) -> None:
        """一个 L1 批次的句子已进 buffer：累积，攒满一波才触发 LLM（后台，不阻塞采集）。"""
        if self._stopped or not seqs:
            return
        self._wave_seqs.extend(seqs)
        self._wave_batches += 1
        self._trigger()

    def _trigger(self) -> None:
        if not self._wave_seqs:
            return
        if self._wave_batches < settings.summary_every_n_batches:
            return                       # 还没攒够一波
        if self._task is not None and not self._task.done():
            return                       # 在飞：等 _run 的 finally 再检查
        seqs, self._wave_seqs = self._wave_seqs, []
        self._wave_batches = 0
        self._task = asyncio.get_running_loop().create_task(self._run(seqs))

    async def _run(self, seqs: list[int]) -> None:
        try:
            await self._enhance(seqs)
        except Exception:
            logger.exception("wave enhance failed for %s", self._session.meeting_id)
        finally:
            self._task = None
            self._trigger()              # 在飞期间攒够的波立刻补跑

    # ---------- 一波一次合并 LLM：纠错 + 订正上一波 + 段纪要 ----------

    async def _enhance(self, seqs: list[int]) -> None:
        """一次 LLM 调用完成本波全部工作。

        输入：前 N 波段纪要（上下文）/ 上一波段纪要（可订正）/ 上一波带行号原文
              （稀疏订正）/ 本波带行号原文（待纠错 + 出纪要）。
        输出 lines 与 prev_corrections 均按显式行号 i 对齐，不依赖位置：模型漏行
        只影响漏掉的那几行（留草稿态，下一波可补），不再整波失败。
        """
        items = await self._session.buffer.items_by_seqs(seqs)
        if not items:
            return
        cur_raw = [(it.speaker or "发言人", it.text) for it in items]

        # 视觉波次（协同模型：音频侧是节拍器，凑满一波时取走帧组送 OCR，
        # 见 docs/achit/视频侧接口与代码地图-20260922.md §1/§3.4）
        ocr_text = ""
        if settings.visual_enabled and self._session.visual_app:
            try:
                frames = await self._session.visual.take_group()
                if frames:
                    await log_event(self._session.meeting_id, event_stages.OCR_START,
                                    frames=len(frames), start_ms=frames[0][0],
                                    end_ms=frames[-1][0])
                    ocr_text = await visual_ocr(frames, app=self._session.visual_app)
                    if ocr_text:
                        # OCR 结果除进本波 prompt 外落 VisualNote 存档：会末版按时间范围
                        # 关联复用，不重跑 OCR（2026-09-28 双版本架构）
                        note_id = await persist_visual_note(self._session.meeting_id,
                                                            frames[0][0], frames[-1][0],
                                                            ocr_text, frames=frames)
                        await log_event(self._session.meeting_id, event_stages.OCR_END,
                                        note_id=note_id, chars=len(ocr_text),
                                        start_ms=frames[0][0], end_ms=frames[-1][0])
                    else:
                        await log_event(self._session.meeting_id, event_stages.OCR_END,
                                        empty=True, start_ms=frames[0][0],
                                        end_ms=frames[-1][0])
            except Exception:
                logger.exception("visual wave failed for %s（不阻塞纪要）",
                                 self._session.meeting_id)

        # 上下文窗口：前 N 波已定稿段纪要（排除最后一波——它单独给，因为要订正）
        n_ctx = settings.summary_context_segments
        ctx = self._segments[-(n_ctx + 1):-1] if len(self._segments) > 1 else []
        ctx_summaries = [self._fmt_segment(s) for s in ctx]
        prev_summary = self._segments[-1]["content"] if self._segments else ""

        result = await _llm_enhance(ctx_summaries, prev_summary, self._prev_lines, cur_raw,
                                    ocr_text=ocr_text, meeting_id=self._session.meeting_id,
                                    scope="live", seq=self.summary_seq + 1)
        if result is None:
            # 失败不兜底：记日志跳过。本波原文仍登记为「上一波」，
            # 下一波的 2 波原文窗口会带上它 → 纠错自动延后一波补做。
            logger.error(
                "enhancer %s: 本波 LLM 失败，跳过（文字留草稿态、本波无段纪要；"
                "下一波会带本波原文补纠）", self._session.meeting_id)
            self._prev_lines = cur_raw
            self._prev_seqs = [it.seq for it in items]
            return

        lines = result["lines"]          # [{i, speaker, text}]，按行号对齐（不依赖位置）
        n_cur = len(cur_raw)

        # 按显式行号 i 应用本波纠错：漏行不会错位，越界/非法行号直接忽略。
        # （旧契约要求「行数必须完全相同」并按位置对齐，实测模型会把两个碎句并成
        #   一行导致 got 68 want 69 → 整波被跳过、无段纪要、文字留草稿。按 i 对齐
        #   后这类偏差只影响漏掉的那几行，不再整波失败。）
        updates: list[tuple[int, str, str]] = []  # (seq, speaker, text)
        corrected_cur: list[tuple[str, str]] = list(cur_raw)   # 供下一波当「上一波」
        covered: set[int] = set()      # 模型确实回传过的本波行号
        n_applied = 0
        for d in lines:
            i = d.get("i")
            if not isinstance(i, int) or not (0 <= i < n_cur):
                continue
            spk, txt = str(d.get("speaker", "")).strip(), str(d.get("text", "")).strip()
            if not txt:
                continue
            covered.add(i)
            it = items[i]
            if txt != it.text or (spk and spk != it.speaker):
                it.text = txt
                if spk:
                    it.speaker = spk
                updates.append((it.seq, it.speaker, txt))
                n_applied += 1          # 实际发生改动的行（区别于「覆盖」）
            corrected_cur[i] = (it.speaker or "发言人", it.text)
        if len(covered) < n_cur * 0.5:
            logger.warning("enhancer %s: 本波只回传 %d/%d 行（模型可能漏行），"
                           "未覆盖的句子留草稿态，下一波可补",
                           self._session.meeting_id, len(covered), n_cur)

        # 上一波稀疏订正（按行号 i 指回上一波原文）
        prev_items = await self._session.buffer.items_by_seqs(self._prev_seqs) \
            if self._prev_seqs else []
        prev_by_seq = {it.seq: it for it in prev_items}
        n_prev_applied = 0
        for c in result["prev_corrections"]:
            i = c.get("i")
            if not isinstance(i, int) or not (0 <= i < len(self._prev_seqs)):
                continue                      # 越界/非法行号：忽略该条
            spk, txt = str(c.get("speaker", "")).strip(), str(c.get("text", "")).strip()
            if not txt:
                continue
            it = prev_by_seq.get(self._prev_seqs[i])
            if it is not None and (txt != it.text or spk != it.speaker):
                it.text = txt
                if spk:
                    it.speaker = spk
                updates.append((it.seq, it.speaker, txt))
                n_prev_applied += 1

        if updates:
            await self._persist_texts(updates)

        # 只对模型确实回传过的行解除草稿态：漏传的行仍留草稿，下一波的
        # prev_corrections 还能补（诚实反映「这句还没被 LLM 看过」）。
        revised_prev = [it for it in prev_items if any(u[0] == it.seq for u in updates)]
        for it in revised_prev + [items[i] for i in sorted(covered)]:
            await self._session.broadcast({
                "type": "transcript_update",
                "seq": it.seq,
                "speaker": it.speaker,
                "text": it.text,
                "start_ms": it.start_ms,
                "end_ms": it.end_ms,
                "corrected": True,
            })

        # 订正上一波段纪要（LLM 判断无需改动时原样返回，此时不落库不广播）
        prev_sum = (result.get("prev_summary") or "").strip()
        if prev_sum and self._segments and prev_sum != self._segments[-1]["content"]:
            self._segments[-1]["content"] = prev_sum
            await self._persist_segment(self._segments[-1], update=True)
            await self._broadcast_segment(self._segments[-1], final=False)

        # 本波段纪要：append 落库 + 广播
        summary = (result.get("summary") or "").strip()
        if summary:
            self.summary_seq += 1
            seg = {
                "seq": self.summary_seq,
                "title": (result.get("title") or "").strip() or f"第{self.summary_seq}段",
                "content": summary,
                "start_ms": items[0].start_ms,
                "end_ms": items[-1].end_ms,
                # 本波原文留档（可审计：这段纪要是基于什么原文生成的）
                "window_text": "\n".join(f"{s}: {t}" for s, t in cur_raw),
                # 结构化待办（2026-09-28）：LLM 按 task/assignee/due/quote 四字段输出，
                # 会末待办聚合 = 各波 action_items 拼接去重（quote 相同视为同一条）
                "action_items": result.get("action_items") or [],
            }
            self._segments.append(seg)
            await self._persist_segment(seg)
            await self._broadcast_segment(seg, final=False)

        # 本波登记为「上一波」，供下一波稀疏订正（用纠错后的文本，失败行保留原文）
        self._prev_lines = corrected_cur
        self._prev_seqs = [it.seq for it in items]
        logger.info("enhancer %s: 波完成（本波 %d 句/覆盖 %d/改动 %d，订正上一波 %d 句，"
                    "累计段纪要 %d 段）",
                    self._session.meeting_id, n_cur, len(covered), n_applied,
                    n_prev_applied, len(self._segments))

    # ---------- 段纪要格式化 / 落库 / 广播 ----------

    @staticmethod
    def _fmt_segment(s: dict) -> str:
        """段纪要的统一渲染（LLM 上下文与会末总结输入共用同一格式）。"""
        return (f"[{s['seq']}] ({_mmss(s['start_ms'])}-{_mmss(s['end_ms'])}) "
                f"{s['title']}\n{s['content']}")

    def segments_text(self) -> str:
        """全部段纪要拼接——会末大总结的唯一输入（不含原始转写全文）。"""
        return "\n\n".join(self._fmt_segment(s) for s in self._segments)

    async def _persist_segment(self, seg: dict, update: bool = False) -> None:
        from app.db.database import SessionLocal
        from app.db.models import Summary

        async with SessionLocal() as db:
            if update:
                await db.execute(
                    Summary.__table__.update()
                    .where(Summary.meeting_id == self._session.meeting_id)
                    .where(Summary.seq == seg["seq"])
                    .values(content=seg["content"], title=seg["title"],
                            action_items=json.dumps(seg.get("action_items") or [],
                                                    ensure_ascii=False))
                )
            else:
                db.add(Summary(
                    meeting_id=self._session.meeting_id,
                    seq=seg["seq"],
                    scope="live",   # 实时波段纪要（会末版段纪要 scope='final'，见 final_pass）
                    window_text=seg["window_text"],
                    content=seg["content"],
                    ocr_text=ocr_text or "",
                    action_items=json.dumps(seg.get("action_items") or [],
                                            ensure_ascii=False),
                    attributes="[]",
                    title=seg["title"],
                ))
            await db.commit()

    async def _broadcast_segment(self, seg: dict, final: bool) -> None:
        points = [ln.lstrip("· ").strip() for ln in seg["content"].splitlines() if ln.strip()]
        await self._session.broadcast({
            "type": "summary",
            "seq": seg["seq"],
            "scope": "live",            # 前端按 scope 分 tab（2026-09-28 双版本架构）
            "final": final,
            "title": seg["title"],
            "points": points,
            "action_items": seg.get("action_items") or [],
            "attributes": [],
        })

    async def _persist_texts(self, updates: list[tuple[int, str, str]]) -> None:
        from app.db.database import SessionLocal
        from app.db.models import Utterance

        async with SessionLocal() as db:
            for seq, spk, txt in updates:
                # 首次保存 corrected_text；text 保持最新展示态。
                await db.execute(
                    Utterance.__table__.update()
                    .where(Utterance.meeting_id == self._session.meeting_id)
                    .where(Utterance.seq == seq)
                    .values(speaker=spk, text=txt,
                            corrected_text=txt)
                )
            await db.commit()

    # ---------- 会末收尾 ----------

    async def finish(self) -> str:
        """会议结束：等在飞的一波 → 强制跑不足一波的尾巴 → 返回全部段纪要拼接。

        尾巴必须强制跑：flush 只送来 1~3 个批次时按「满 4 批」规则不会触发，
        否则最后 1-2 分钟的文字永不纠错、内容也不进任何纪要。
        """
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task),
                                       timeout=settings.roll_llm_timeout_sec + 10)
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("meeting %s: 波增强超时（finish）", self._session.meeting_id)
            except Exception:
                pass
        if settings.summary_flush_tail and self._wave_seqs:
            seqs, self._wave_seqs = self._wave_seqs, []
            self._wave_batches = 0
            logger.info("meeting %s: 强制跑尾巴波（%d 句，不足一波）",
                        self._session.meeting_id, len(seqs))
            try:
                await self._enhance(seqs)
            except Exception:
                logger.exception("meeting %s: 尾巴波失败", self._session.meeting_id)
        self._stopped = True
        return self.segments_text()


async def _llm_enhance(ctx_summaries: list[str], prev_summary: str,
                       prev_lines: list[tuple[str, str]],
                       cur_lines: list[tuple[str, str]],
                       ocr_text: str = "", *, meeting_id: str = "",
                       scope: str = "live", seq: int = 0) -> dict | None:
    """一波一次合并 LLM 调用。失败/超时返回 None（调用方记日志跳过，不重试不累积）。

    Langfuse 经 openai wrapper 自动埋点。超时 roll_llm_timeout_sec 秒即放弃。
    同时写 SummaryLlmLog：prompt、原始输出、耗时，供调试页看黑盒。
    """
    import time

    from app.db.database import SessionLocal
    from app.db.models import SummaryLlmLog
    from app.llm import client as llm_client

    messages = llm_client.build_enhance_messages(ctx_summaries, prev_summary,
                                                 prev_lines, cur_lines, ocr_text=ocr_text)
    t0 = time.monotonic()
    await log_event(meeting_id, event_stages.LLM_WAVE_START,
                    scope=scope, seq=seq, model=settings.llm_model)
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
        duration_ms = int((time.monotonic() - t0) * 1000)
        if meeting_id:
            try:
                async with SessionLocal() as db:
                    db.add(SummaryLlmLog(
                        meeting_id=meeting_id,
                        seq=seq,
                        scope=scope,
                        model=settings.llm_model,
                        prompt_json=json.dumps(messages, ensure_ascii=False),
                        raw_response=raw,
                        duration_ms=duration_ms,
                    ))
                    await db.commit()
            except Exception:
                logger.exception("SummaryLlmLog persist failed")
        await log_event(meeting_id, event_stages.LLM_WAVE_END,
                        scope=scope, seq=seq, duration_ms=duration_ms)
        data = json.loads(raw)
        # lines / prev_corrections 都按显式行号 i 对齐（不依赖位置），
        # 模型漏行只影响漏掉的那几行，不再整波失败
        def _idx_rows(key: str) -> list[dict]:
            rows = data.get(key) or []
            return [d for d in rows if isinstance(d, dict) and isinstance(d.get("i"), int)]

        return {
            "lines": _idx_rows("lines"),
            "prev_corrections": _idx_rows("prev_corrections"),
            "summary": str(data.get("summary", "")).strip(),
            "title": str(data.get("title", "")).strip(),
            "prev_summary": str(data.get("prev_summary", "")).strip(),
            # 结构化待办（2026-09-28）：task/assignee/due/quote 四字段，经 client 归一化
            "action_items": llm_client.parse_action_items(data.get("action_items")),
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        await log_event(meeting_id, event_stages.LLM_WAVE_FAIL,
                        scope=scope, seq=seq, duration_ms=duration_ms,
                        error=str(exc)[:500])
        logger.exception("llm enhance failed")
        return None
