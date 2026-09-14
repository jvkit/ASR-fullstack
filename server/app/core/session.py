"""会议会话：一次会议的完整生命周期编排（非实时批次架构）。

数据流：
    前端音频 → 累积缓冲 ──每 8~15s 一批──▶ GPU /meeting_chunk
        （VAD 切句 + SeACo Paraformer 转写 + ERes2NetV2 认人）
        → 带说话人的句子：落库 + 广播 → 批次增强（LLM 纠错 + 滚动纪要）
    会末：同一端点全场重跑 → 权威终版（utterances_re）

批次切点用 GPU /vad 找最后一个完整句尾，避免把句子从中间切断；
会末 flush 把尾巴音频（不足一批）也送出去，不丢结尾内容。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import WebSocket

from app.config import settings, load_hotwords
from app.core.enhancer import BatchEnhancer
from app.core.gpu_pipeline import meeting_chunk, vad_ends
from app.core.segment import UtteranceBuffer
from app.db.database import SessionLocal
from app.db.models import Meeting, Summary, Utterance
from app.llm import client as llm_client

logger = logging.getLogger(__name__)


class MeetingSession:
    def __init__(self, meeting_id: str, title: str, recorder: str, replay_of: str | None = None) -> None:
        self.meeting_id = meeting_id
        self.title = title
        self.recorder = recorder
        self.replay_of = replay_of  # 非空=重放测试：音频来自历史录音而非麦克风
        self.replay_done = asyncio.Event()  # 重放音频喂完置位（供 ws 循环感知自动结束）
        self.clients: set[WebSocket] = set()
        self.buffer = UtteranceBuffer()
        self.summary_seq = 0
        self._stopped = False
        self._lock = asyncio.Lock()

        self.enhancer = BatchEnhancer(self)

        self.paused = False  # 前端暂停：丢弃上行音频（连接保持）
        self.final_title = ""
        self.final_attributes: list[dict] = []  # 累积的事实性属性（key 去重合并）
        self._audio = bytearray()      # 累计上行 PCM（int16/16kHz/mono），结束时写 wav
        self._processed = 0            # 已送出批次的字节偏移
        self._chunk_failures = 0       # /meeting_chunk 连续失败次数
        self._batch_loop_task: asyncio.Task | None = None
        # 热词（FunASR 按空白切分；load_hotwords 逗号格式转空格）
        self._hotwords = load_hotwords().replace(",", " ")

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        async with SessionLocal() as db:
            db.add(Meeting(id=self.meeting_id, title=self.title, recorder=self.recorder))
            await db.commit()
        self._batch_loop_task = asyncio.create_task(self._batch_loop())
        logger.info("meeting %s started (nonrealtime batches: min=%ss max=%ss tick=%ss)",
                    self.meeting_id, settings.pipeline_batch_min_sec,
                    settings.pipeline_batch_max_sec, settings.pipeline_tick_sec)

    async def stop(self) -> None:
        """结束会议：立刻落库 ended 并广播，重活全部转后台。

        同步段（用户等待的部分）：存录音 → 落库 ended + 广播。
        后台段（_finalize）：flush 尾巴批次 → 等在飞增强收尾 → 会末总结/标签/属性
        → 会末全场重跑（权威终版）。LLM 网关抽风时这些步骤慢，但用户点结束立即看到结束。
        """
        async with self._lock:
            if self._stopped:
                return
            self._stopped = True

        if self._batch_loop_task:
            self._batch_loop_task.cancel()
            try:
                await self._batch_loop_task
            except asyncio.CancelledError:
                pass

        self._save_recording()

        async with SessionLocal() as db:
            meeting = await db.get(Meeting, self.meeting_id)
            if meeting:
                meeting.status = "ended"
                meeting.ended_at = datetime.now()
                await db.commit()
        await self.broadcast({"type": "ended", "meeting_id": self.meeting_id})
        logger.info("meeting %s ended (finalization continues in background)", self.meeting_id)

        asyncio.create_task(self._finalize())

    async def _finalize(self) -> None:
        """会末收尾（后台）：尾巴批次 → 最终总结/标题/标签/属性 + 全场重跑。失败仅记日志。"""
        session_id = self.meeting_id
        try:
            # 尾巴音频（不足一批的结尾）也走一批，保证全文完整
            await self._run_batch(force=True)

            rolling_summary = await self.enhancer.finish()

            # 会末总结：优先用滚动纪要（已纠错、信息密度高），全文太长时纪要兜底更稳
            full_text = await self.buffer.full_text()
            final_input = full_text
            if len(full_text) > 6000 and rolling_summary:
                final_input = rolling_summary
            result = await llm_client.extract_final_attributes(final_input)

            final_title = result["title"]
            # 重放测试会议：标题保留「重放测试」标记（LLM 生成标题后仍追加，便于区分）
            if self.replay_of and final_title and "重放测试" not in final_title:
                final_title = f"{final_title}（重放测试）"
            final_summary = result["summary"] or rolling_summary
            final_tags = result["tags"][:settings.final_tags_count]
            # 属性：滚动提取的 + 会末提取的合并（key 去重，滚动版优先）
            merged: dict[str, str] = {}
            for a in result["attributes"]:
                if isinstance(a, dict) and a.get("key"):
                    merged[str(a["key"])] = str(a.get("value", ""))
            for a in self.final_attributes:
                merged[a["key"]] = a["value"]

            async with SessionLocal() as db:
                meeting = await db.get(Meeting, session_id)
                if meeting:
                    if final_title:
                        meeting.title = final_title
                    if final_tags:
                        meeting.tags = json.dumps(final_tags, ensure_ascii=False)
                    if merged:
                        meeting.attributes = json.dumps(
                            [{"key": k, "value": v} for k, v in merged.items()],
                            ensure_ascii=False,
                        )
                    await db.commit()

            # 滚动纪要作为最终总结落一条 Summary（可审计、供报告导出）
            if final_summary:
                self.summary_seq += 1
                async with SessionLocal() as db:
                    db.add(Summary(
                        meeting_id=session_id,
                        seq=self.summary_seq,
                        window_text="",
                        content=final_summary,
                        action_items="[]",
                        attributes="[]",
                        title=final_title or "最终总结",
                    ))
                    await db.commit()
                await self.broadcast({
                    "type": "summary",
                    "seq": self.summary_seq,
                    "final": True,
                    "title": final_title or "最终总结",
                    "points": [p.lstrip("· ").strip() for p in final_summary.splitlines() if p.strip()],
                    "action_items": [],
                    "attributes": [{"key": k, "value": v} for k, v in merged.items()],
                })
            logger.info("meeting %s finalized", session_id)
        except Exception:
            logger.exception("meeting %s finalize failed", session_id)

        # 会末全场重跑（与会议中同一 /meeting_chunk 管线）→ utterances_re 权威终版
        from app.core.reannotate import reannotate

        pcm = bytes(self._audio)
        if await reannotate(session_id, pcm):
            await self.broadcast({"type": "reannotated", "meeting_id": session_id})

    # ---------- 音频上行与批次调度 ----------

    async def handle_audio(self, pcm: bytes) -> None:
        if self._stopped or self.paused:
            return
        self._audio.extend(pcm)

    async def _batch_loop(self) -> None:
        """定时成批：攒够 pipeline_batch_min_sec 后按 VAD 句尾切点送 /meeting_chunk。"""
        try:
            while not self._stopped:
                await asyncio.sleep(settings.pipeline_tick_sec)
                if self.paused:
                    continue
                await self._run_batch()
        except asyncio.CancelledError:
            raise

    async def _run_batch(self, force: bool = False) -> None:
        """切一批音频送 GPU 管线，句子落库 + 广播 + 驱动增强。

        force=True（会末 flush）：剩余音频全送，不管大小和切点。
        失败不推进 _processed（下轮重试）；连续失败 3 次丢弃这段防积压。
        """
        total = len(self._audio)
        if total <= self._processed:
            return
        pcm = bytes(self._audio[self._processed:])
        pending_ms = len(pcm) / 32

        if force:
            cut_ms = pending_ms
        else:
            if pending_ms < settings.pipeline_batch_min_sec * 1000:
                return
            ends = await vad_ends(pcm)
            if ends is None:
                return  # VAD 失败：下轮重试
            cut_ms = ends[-1] if ends else None
            if not cut_ms or cut_ms < 1000:
                if pending_ms < settings.pipeline_batch_max_sec * 1000:
                    return  # 还没有完整句子且未到强制上限：再等等
                cut_ms = settings.pipeline_batch_max_sec * 1000  # 长句/长静音：硬切
            cut_ms = min(cut_ms, pending_ms)

        chunk = pcm[:int(cut_ms * 32)]
        # shield：stop 取消批次循环时，在飞的 GPU 请求仍跑完（否则 flush 会重复送）
        segs = await asyncio.shield(meeting_chunk(chunk, self._hotwords))
        if segs is None:
            self._chunk_failures += 1
            if self._chunk_failures >= 3 and not force:
                logger.warning(
                    "meeting %s: chunk failed %d times, dropping %.1fs audio",
                    self.meeting_id, self._chunk_failures, pending_ms / 1000,
                )
                self._processed = total
                self._chunk_failures = 0
            return
        self._chunk_failures = 0
        base_ms = self._processed // 32
        self._processed += len(chunk)
        if not segs:
            return

        seqs: list[int] = []
        for s in segs:
            text = (s.get("text") or "").strip()
            if not text:
                continue  # 空段不展示（碎段已在 GPU 侧参与打标/修剪）
            start_ms = int(base_ms + float(s["start"]) * 1000)
            end_ms = int(base_ms + float(s["end"]) * 1000)
            item = await self.buffer.add(s.get("speaker") or "", text, start_ms, end_ms)
            async with SessionLocal() as db:
                db.add(Utterance(
                    meeting_id=self.meeting_id,
                    seq=item.seq,
                    speaker=item.speaker,
                    text=item.text,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    char_timestamps="[]",
                ))
                await db.commit()
            await self.broadcast({
                "type": "transcript",
                "seq": item.seq,
                "speaker": item.speaker,
                "text": item.text,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
            })
            logger.info(
                "meeting %s seq=%d spk=%r %d-%dms %s",
                self.meeting_id, item.seq, item.speaker, item.start_ms, item.end_ms, item.text,
            )
            seqs.append(item.seq)

        if seqs:
            self.enhancer.on_batch(seqs)

    # ---------- 重放测试（音频来自历史录音，按真实节奏喂入） ----------

    async def run_replay(self, wav_path: str) -> None:
        """把历史录音按 0.1s 节奏喂进会话，模拟真实开会。

        暂停时挂起（不丢节拍，恢复后继续），stop 后立即退出。
        喂完置位 replay_done，由 ws 循环触发正常结束流程。
        """
        import wave

        CHUNK_SEC = 0.1
        try:
            with wave.open(wav_path, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                    await self.broadcast({"type": "error", "message": "录音格式异常（需 16kHz/int16/mono）"})
                    self.replay_done.set()
                    return
                total = wf.getnframes()
                logger.info("meeting %s replay start: %.1f min", self.meeting_id, total / 16000 / 60)
                sent = 0
                while sent < total and not self._stopped:
                    if self.paused:
                        await asyncio.sleep(0.2)
                        continue
                    chunk = wf.readframes(1600)  # 0.1s
                    if not chunk:
                        break
                    await self.handle_audio(chunk)
                    sent += len(chunk) // 2
                    await asyncio.sleep(CHUNK_SEC)
                logger.info("meeting %s replay fed: %.1f/%.1f min", self.meeting_id,
                            sent / 16000 / 60, total / 16000 / 60)
        except FileNotFoundError:
            await self.broadcast({"type": "error", "message": f"录音文件不存在: {wav_path}"})
        finally:
            self.replay_done.set()

    # ---------- 总结 ----------

    def _save_recording(self) -> None:
        """把累计的 PCM 写成 wav（16kHz/int16/mono，与上行音频一致）。"""
        if not self._audio:
            return
        try:
            import wave
            from pathlib import Path

            out_dir = Path(settings.recordings_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{self.meeting_id}.wav"
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(self._audio))
            logger.info("meeting %s recording saved: %s (%.1f min)",
                        self.meeting_id, out_path, len(self._audio) / 32000 / 60)
        except Exception:
            logger.exception("meeting %s save recording failed", self.meeting_id)

    # ---------- 广播 ----------

    async def broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(msg, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class SessionManager:
    """meeting_id → MeetingSession 注册表。"""

    def __init__(self) -> None:
        self._sessions: dict[str, MeetingSession] = {}

    def create(self, title: str, recorder: str, replay_of: str | None = None) -> MeetingSession:
        meeting_id = uuid.uuid4().hex[:12]
        session = MeetingSession(meeting_id, title, recorder, replay_of=replay_of)
        self._sessions[meeting_id] = session
        return session

    def get(self, meeting_id: str) -> MeetingSession | None:
        return self._sessions.get(meeting_id)

    def remove(self, meeting_id: str) -> None:
        self._sessions.pop(meeting_id, None)


session_manager = SessionManager()
