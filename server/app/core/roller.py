"""滚动增强：实时会议进行中，按有效语音成窗，一轮内完成打标 + 合并 LLM（纠错+滚动纪要）。

窗口规则（用户拍板方案）：
- 有效语音 = 窗口内各句 (end_ms - start_ms) 累加（≈ gpu pipeline VAD 后看到的量）
- 有效语音 >= roll_min_speech_sec (8s) → 成窗触发；不足则攒着并入下轮（不浪费调用）
- 墙钟 >= roll_max_sec (15s) → 强制成窗（防长静音期纪要断流）

一轮：
1. 打标：窗口音频（含回看 roll_lookback_sec）→ gpu /diarize_labeled(oracle=参会人数)
   → 段落按时间重叠贴到窗口内句子 → 立即落库 + 广播
2. 合并 LLM：输入 = 上轮纪要 + 上轮原文 + 上轮纠正文 + 本轮原文
   → 输出 { lines: [上轮修正+本轮纠错], summary: 最新滚动纪要 }
   → 落库 + 广播 transcript_update / summary（Langfuse 记录）

容错（LLM 网关抽风治理）：
- LLM 调用超时 roll_llm_timeout_sec 秒失败，绝不重试排队；
- 纠错失败的窗口滚入下轮输入（_pending_corrected），LLM 恢复后自动补纠；
- 打标失败不影响纠错继续（两者独立）。

滚动纪要哲学：先出、后修。纪要不是每轮从零总结，而是「上轮纪要 + 本轮内容」
锚定改写；上轮因话没说完产生的错误要点，本轮改写时自动修正。
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
    seqs: list[int] = field(default_factory=list)  # 覆盖的句子 seq


class RollingEnhancer:
    """挂在 MeetingSession 上的滚动增强器。所有回调非阻塞，失败仅记日志。"""

    def __init__(self, session) -> None:  # noqa: F821
        self._session = session
        self._pending: RollWindow | None = None   # 攒中的窗口（未达有效语音阈值）
        self._task: asyncio.Task | None = None    # 在飞的一轮（防重入）
        self._last_end_ms = 0                     # 上一轮窗口终点（本轮起点）
        # 上一轮状态：LLM 输入上下文 + 失败补纠
        self._prev_raw: list[tuple[str, str]] = []      # 上轮原文 [(speaker, text)]
        self._prev_corrected: list[tuple[str, str]] = []  # 上轮纠正文（本轮可修正）
        self._prev_corrected_seqs: list[int] = []       # 上轮句子 seq（与纠正文行对应）
        self._pending_correction: bool = False          # 上轮纠错失败 → 本轮补纠
        self._stopped = False
        self.rolling_summary = ""   # 滚动纪要（最新一份，喂给下一轮和会末总结）
        self.summary_seq = 0

    # ---------- 触发判断（句终回调） ----------

    def on_utterance(self, start_ms: int, end_ms: int, seq: int) -> None:
        """句终事件：累积窗口，有效语音达标则在后台触发一轮。

        时间基准用句子绝对时间戳（服务端段偏移后的会议绝对毫秒），
        不用 _audio 长度（上行缓冲在句终时未必积累到句尾）。
        """
        if self._stopped or end_ms <= 0:
            return
        if self._pending is None:
            self._pending = RollWindow(
                start_ms=min(self._last_end_ms, max(0, start_ms)),
                end_ms=end_ms,
            )
        self._pending.end_ms = max(self._pending.end_ms, end_ms)
        if seq not in self._pending.seqs:
            self._pending.seqs.append(seq)

        # 有效语音 = 窗口内句时长累加（approximate VAD 后的量）
        speech_ms = self._pending_speech_ms()
        wall_sec = (self._pending.end_ms - self._pending.start_ms) / 1000
        need = speech_ms >= settings.roll_min_speech_sec * 1000 or wall_sec >= settings.roll_max_sec
        if need and self._task is None:
            win = RollWindow(
                start_ms=self._pending.start_ms,
                end_ms=self._pending.end_ms,
                seqs=list(self._pending.seqs),
            )
            self._pending = None
            self._task = asyncio.get_running_loop().create_task(self._run_round(win))

    def _pending_speech_ms(self) -> int:
        """攒中窗口内句子的有效语音毫秒（查 buffer，句长累加）。"""
        seqs = self._pending.seqs if self._pending else []
        if not seqs:
            return 0
        # buffer 是 append-only，_all 保序；同步小查询直接遍历
        items = self._session.buffer.items_by_seqs_nowait(seqs)
        return sum(max(0, it.end_ms - it.start_ms) for it in items)

    # ---------- 一轮执行 ----------

    async def _run_round(self, win: RollWindow) -> None:
        try:
            if win.end_ms <= win.start_ms:
                return
            audio_ms = len(self._session._audio) // 32
            if audio_ms > 0:
                win.end_ms = min(win.end_ms, audio_ms)
            self._last_end_ms = win.end_ms

            # 1. 打标（diarize 段 → 贴到窗口内句子；失败不阻断纠错）
            segs = await self._diarize(win)
            if segs is not None:
                await self._apply_segments(win, segs)

            # 2. 合并 LLM：纠错（含上轮修正）+ 滚动纪要
            await self._enhance_window(win)
        except Exception:
            logger.exception("rolling round failed for %s", self._session.meeting_id)
        finally:
            self._task = None
            # 上一轮在飞期间攒下的新窗口，句终会再触发；这里无需补触发
            # （若 stop 前再无句终，残留句子由会末收尾兜底）

    # ---------- 打标 ----------

    async def _diarize(self, win: RollWindow) -> list | None:
        """窗口音频 → 阿里 diarize 段落（绝对毫秒坐标）。失败返回 None。

        回看 roll_lookback_sec：短窗口聚类不稳（同一段人声在不同窗口被反复改名），
        多带一点历史音频让 pipeline 看到更完整的说话人特征；返回时平移回绝对坐标。
        """
        lookback_ms = min(int(settings.roll_lookback_sec * 1000), win.start_ms)
        pcm = bytes(self._session._audio[(win.start_ms - lookback_ms) * 32: win.end_ms * 32])
        if len(pcm) / 32000 < 3:
            return None
        buf = _wav_bytes(pcm)
        oracle = self._session.speaker_count or None
        try:
            async with httpx.AsyncClient(timeout=settings.roll_diarize_timeout_sec) as cli:
                r = await cli.post(
                    f"{settings.diarize_http_url}/diarize_labeled",
                    files={"file": ("win.wav", buf, "audio/wav")},
                    data={"oracle_num": str(oracle) if oracle else ""},
                )
                r.raise_for_status()
                data = r.json()
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
        for it in items:
            if it.seq in updates:
                await self._session.broadcast({
                    "type": "transcript_update",
                    "seq": it.seq,
                    "speaker": it.speaker,
                    "text": it.text,
                    "start_ms": it.start_ms,
                    "end_ms": it.end_ms,
                    "corrected": False,
                })

    # ---------- 合并 LLM：纠错 + 滚动纪要 ----------

    async def _enhance_window(self, win: RollWindow) -> None:
        """一次 LLM 调用完成：修正上轮 + 纠错本轮 + 滚动改写纪要。

        输入：上轮纪要 / 上轮原文+纠正文（可修正，仅当上轮纠错失败时带纠正文缺失逻辑见下）
        / 本轮原文（已带刚打好的说话人标签）。
        输出 lines 行序 = [上轮行...] + [本轮行...]，行数不匹配整窗回退。
        """
        items = await self._session.buffer.items_by_seqs(win.seqs)
        if not items:
            return
        cur_raw = [(it.speaker or "发言人", it.text) for it in items]

        # 上轮上下文：仅当上轮纠错成功（有纠正文）或失败（带原文，本轮一并补纠）都给 LLM
        # 上轮失败补纠：_pending_correction=True 时上轮行也要求 LLM 重新输出并修正
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
            # LLM 失败：本轮滚入下轮补纠（不丢原文）
            self._pending_correction = True
            self._prev_raw = cur_raw
            self._prev_corrected_seqs = [it.seq for it in items]
            return

        lines = result["lines"]
        n_prev, n_cur = len(prev_lines), len(cur_raw)
        if len(lines) != n_prev + n_cur:
            # 行数不匹配：整窗回退（不纠错比错位好），滚入下轮
            logger.warning(
                "roller %s: line count mismatch (got %d, want %d+%d), rollback",
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
            for seq, spk, txt in updates:
                it = prev_by_seq.get(seq) or next((x for x in items if x.seq == seq), None)
                if it is not None:
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

        # 记录本轮为「上一轮」，供下轮修正
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

    async def finish(self) -> None:
        """会议结束：等在飞一轮跑完（有上限），返回供会末总结用的滚动纪要。"""
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=settings.roll_llm_timeout_sec + 10)
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("meeting %s: rolling round timeout on finish", self._session.meeting_id)
            except Exception:
                pass
        self._stopped = True
        return self.rolling_summary


async def _llm_enhance(summary: str, prev_lines: list[tuple[str, str]],
                       cur_lines: list[tuple[str, str]]) -> dict | None:
    """合并 LLM 调用：纠错（含上轮修正）+ 滚动纪要改写。失败/超时返回 None。

    Langfuse 经 openai wrapper 自动埋点。超时 roll_llm_timeout_sec 秒即放弃，
    绝不重试（网关抽风时秒失败，窗口滚入下轮补纠）。
    """
    import json as _json

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
        data = _json.loads(raw)
        lines = data.get("lines", [])
        out = [(str(d.get("speaker", "")).strip(), str(d.get("text", "")).strip())
               for d in lines if isinstance(d, dict)]
        return {"lines": out, "summary": str(data.get("summary", "")).strip()}
    except Exception:
        logger.exception("llm enhance failed")
        return None


def _wav_bytes(pcm: bytes) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()
