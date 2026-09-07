"""滚动增强：实时会议进行中，周期性用阿里 pipeline 打说话人标签 + LLM 纠正文字。

触发（句终驱动，见 session._on_asr_offline）：
- 距上一轮 >= roll_min_new_sec 且未处理音频足够 → 触发一轮
- 距上一轮 >= roll_max_sec → 下个句终强制触发

一轮（滚动窗口，对齐用户拍板方案）：
1. 打标：音频 [窗口起点, 当前) → gpu /diarize_labeled(oracle=参会人数)
   → 段落按时间重叠贴到窗口内句子 → 立即落库 + 广播
2. 纠错：LLM 输入 = 近 N 轮已纠文本（上下文）+ 本轮窗口原文 + 最新纪要要点
   → 输出修正后全文 → 覆盖窗口内句子文本 → 落库 + 广播（Langfuse 记录）

会末总结消费的是 buffer 中已被纠错的文本（drain 晚于纠错，天然衔接）。
"""

from __future__ import annotations

import asyncio
import logging
import wave
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RollWindow:
    """一轮滚动处理的范围（按会议内部音频毫秒计）。"""

    start_ms: int
    end_ms: int
    corrected: bool = False  # 是否已被纠错
    seqs: list[int] = field(default_factory=list)  # 覆盖的句子 seq


class RollingEnhancer:
    """挂在 MeetingSession 上的滚动增强器。所有回调非阻塞，失败仅记日志。"""

    def __init__(self, session) -> None:  # noqa: F821
        self._session = session
        self._last_end_ms = 0        # 上一轮窗口终点（也是本轮起点）
        self._last_trigger_ms = 0    # 上一轮触发时的会议音频时刻
        self._history: list[RollWindow] = []  # 已纠窗口（供下一轮上下文）
        self._task: asyncio.Task | None = None  # 在飞的一轮（防重入）
        self._pending: RollWindow | None = None
        self._stopped = False

    # ---------- 触发判断（句终回调） ----------

    def on_utterance(self, start_ms: int, end_ms: int, seq: int) -> None:
        """句终事件：累积窗口，满足条件则在后台触发一轮。

        时间基准：用句子的绝对时间戳（start_ms/end_ms），不用 _audio 长度——
        _audio 是上行缓冲，句终时未必已积累到句尾；时间戳是服务端段偏移后的
        会议绝对毫秒，才是可靠的窗口边界。
        """
        if self._stopped or end_ms <= 0:
            return
        if self._pending is None:
            # 窗口起点：从上一轮终点开始；句子可能早于起点（时间戳回跳）仍计入
            self._pending = RollWindow(
                start_ms=min(self._last_end_ms, max(0, start_ms)),
                end_ms=end_ms,
            )
        self._pending.end_ms = max(self._pending.end_ms, end_ms)
        if seq not in self._pending.seqs:
            self._pending.seqs.append(seq)

        win_sec = (self._pending.end_ms - self._pending.start_ms) / 1000
        since_last = (self._pending.end_ms - self._last_trigger_ms) / 1000
        need = (
            win_sec >= settings.roll_max_sec
            or (win_sec >= settings.roll_min_new_sec and since_last >= settings.roll_min_new_sec)
        )
        if need and self._task is None:
            # 拷贝窗口给本轮（避免 run_round 读取时被后续 on_utterance 改写）
            win = RollWindow(
                start_ms=self._pending.start_ms,
                end_ms=self._pending.end_ms,
                seqs=list(self._pending.seqs),
            )
            self._pending = None
            self._task = asyncio.get_running_loop().create_task(self._run_round(win))
        elif need:
            # 已有在飞一轮：窗口保留，等跑完再触发下一轮
            pass

    # ---------- 一轮执行 ----------

    async def _run_round(self, win: RollWindow) -> None:
        try:
            if win.end_ms <= win.start_ms:
                return
            # 窗口终点不超当前已积累音频
            audio_ms = len(self._session._audio) // 32
            if audio_ms > 0:
                win.end_ms = min(win.end_ms, audio_ms)
            self._last_trigger_ms = win.end_ms

            # 1. 打标（diarize 段 → 贴到窗口内句子）
            segs = await self._diarize(win)
            if segs is not None:
                await self._apply_segments(win, segs)

            # 2. 纠错（LLM 全文替换）
            await self._correct_window(win)

            self._last_end_ms = win.end_ms
            self._history.append(win)
            # 只保留近 N 个已纠窗口作上下文
            if len(self._history) > settings.roll_keep_windows:
                self._history = self._history[-settings.roll_keep_windows:]
        except Exception:
            logger.exception("rolling round failed for %s", self._session.meeting_id)
        finally:
            self._task = None

    async def _diarize(self, win: RollWindow) -> list | None:
        """窗口音频 → 阿里 diarize 段落（绝对毫秒坐标）。失败返回 None。

        回看 3s：短窗口聚类不稳（同一段人声在不同窗口会被反复改名），多带一点
        历史音频让 pipeline 看到更完整的说话人特征；返回时平移回会议绝对坐标。
        """
        lookback_ms = min(3000, win.start_ms)
        pcm = bytes(self._session._audio[(win.start_ms - lookback_ms) * 32: win.end_ms * 32])
        if len(pcm) / 32000 < 3:
            return None
        buf = _wav_bytes(pcm)
        oracle = self._session.speaker_count or None
        try:
            async with httpx.AsyncClient(timeout=120) as cli:
                r = await cli.post(
                    f"{settings.diarize_http_url}/diarize_labeled",
                    files={"file": ("win.wav", buf, "audio/wav")},
                    data={"oracle_num": str(oracle) if oracle else ""},
                )
                r.raise_for_status()
                data = r.json()
            # 段时间平移回会议绝对坐标
            base = (win.start_ms - lookback_ms) / 1000
            return [
                [base + s[0], base + s[1], s[3]]
                for s in data.get("segs", [])
            ]
        except Exception:
            logger.exception("rolling diarize failed %s", self._session.meeting_id)
            return None

    async def _apply_segments(self, win: RollWindow, segs: list) -> None:
        """段落按时间重叠贴到窗口内句子；说话人更新后落库+广播。"""
        from app.db.database import SessionLocal
        from app.db.models import Utterance

        items = await self._session.buffer.items_in(win.start_ms, win.end_ms)
        updates: dict[int, str] = {}
        for it in items:
            best, best_ov = "", 0
            for s0, s1, name in segs:
                ov = min(it.end_ms, int(s1 * 1000)) - max(it.start_ms, int(s0 * 1000))
                if ov > best_ov and name:
                    best, best_ov = name, ov
            if best and best != it.speaker:
                updates[it.seq] = best
                it.speaker = best
        if not updates:
            return
        async with SessionLocal() as db:
            for seq, spk in updates.items():
                await db.execute(
                    Utterance.__table__.update()
                    .where(Utterance.meeting_id == self._session.meeting_id)
                    .where(Utterance.seq == seq)
                    .values(speaker=spk)
                )
            await db.commit()
        # 广播更新（前端按 seq 替换标签）
        for it in items:
            if it.seq in updates:
                await self._session.broadcast({
                    "type": "transcript_update",
                    "seq": it.seq,
                    "speaker": it.speaker,
                    "text": it.text,
                    "start_ms": it.start_ms,
                    "end_ms": it.end_ms,
                })

    async def _correct_window(self, win: RollWindow) -> None:
        """LLM 纠错窗口内句子文本（全文替换），带前几轮已纠文本 + 最新纪要上下文。"""
        if not settings.llm_correction_enabled or not win.seqs:
            return
        from app.db.database import SessionLocal
        from app.db.models import Utterance

        items = await self._session.buffer.items_by_seqs(win.seqs)
        if not items:
            return
        window_text = "\n".join(f"{it.speaker or '发言人'}: {it.text}" for it in items)

        # 前文上下文：近 N 个已纠窗口的句子
        ctx_lines: list[str] = []
        for h in self._history:
            prev_items = await self._session.buffer.items_by_seqs(h.seqs)
            if prev_items:
                ctx_lines.append("【前文（已修正，仅参考）】")
                ctx_lines.extend(f"{it.speaker or '发言人'}: {it.text}" for it in prev_items)
        context = "\n".join(ctx_lines)
        summary = await self._session.buffer.recent_summary_hint()

        result = await _llm_correct(context, summary, window_text)
        if not result:
            return
        # 逐行替换（LLM 返回与输入同结构的「说话人: 内容」行）
        new_texts = _parse_corrected(result, len(items))
        if new_texts is None:
            return
        updated = []
        for it, (spk_hint, txt) in zip(items, new_texts):
            if txt and txt != it.text:
                it.text = txt
                updated.append((it.seq, txt))
        if not updated:
            return
        async with SessionLocal() as db:
            for seq, txt in updated:
                await db.execute(
                    Utterance.__table__.update()
                    .where(Utterance.meeting_id == self._session.meeting_id)
                    .where(Utterance.seq == seq)
                    .values(text=txt)
                )
            await db.commit()
        for it in items:
            await self._session.broadcast({
                "type": "transcript_update",
                "seq": it.seq,
                "speaker": it.speaker,
                "text": it.text,
                "start_ms": it.start_ms,
                "end_ms": it.end_ms,
            })
        win.corrected = True


async def _llm_correct(context: str, summary: str, window_text: str) -> str | None:
    """调 LLM 纠错，返回修正后全文（失败返回 None）。Langfuse 经 openai wrapper 自动埋点。"""
    from app.llm import client as llm_client

    messages = llm_client.build_correction_messages(context, summary, window_text)
    try:
        resp = await asyncio.to_thread(
            llm_client.client.chat.completions.create,
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        import json as _json

        data = _json.loads(raw)
        return str(data.get("corrected", "")).strip()
    except Exception:
        logger.exception("llm correct failed")
        return None


def _parse_corrected(result: str, n_items: int) -> list[tuple[str, str]] | None:
    """解析 LLM 输出：期望「说话人: 内容」每行一段，与输入行数一致（允许±）。"""
    lines = [ln for ln in result.splitlines() if ln.strip()]
    out: list[tuple[str, str]] = []
    for ln in lines:
        if "：" in ln:
            spk, txt = ln.split("：", 1)
        elif ":" in ln:
            spk, txt = ln.split(":", 1)
        else:
            spk, txt = "", ln
        out.append((spk.strip(), txt.strip()))
    if len(out) != n_items:
        # 行数不匹配：整窗回退（不纠错比错位好）
        if len(out) < n_items:
            return None
        out = out[:n_items]
    return out


def _wav_bytes(pcm: bytes) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()
