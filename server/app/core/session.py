"""会议会话：一次会议的完整生命周期编排。

数据流：
    前端音频 → FunASR(online 字幕即时广播 / offline 句子进缓冲)
    缓冲 ──定时/结束──▶ LLM 滚动总结 ──▶ 广播 + 落库
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import WebSocket

from app.config import settings
from app.core.asr_client import FunasrClient
from app.core.segment import UtteranceBuffer
from app.core.summarizer import SummaryScheduler
from app.db.database import SessionLocal
from app.db.models import Meeting, Summary, Utterance
from app.llm import client as llm_client

logger = logging.getLogger(__name__)


class MeetingSession:
    def __init__(self, meeting_id: str, title: str, recorder: str) -> None:
        self.meeting_id = meeting_id
        self.title = title
        self.recorder = recorder
        self.clients: set[WebSocket] = set()
        self.buffer = UtteranceBuffer()
        self.prev_summary = ""  # 滚动摘要上下文（截断保存）
        self.summary_seq = 0
        self._asr_closed = asyncio.Event()
        self._stopped = False
        self._lock = asyncio.Lock()

        self.asr = FunasrClient(
            url=settings.funasr_ws_url,
            hotwords=settings.funasr_hotwords,
            on_online=self._on_asr_online,
            on_offline=self._on_asr_offline,
            on_error=self._on_asr_error,
            on_closed=self._on_asr_closed,
        )
        self.scheduler = SummaryScheduler(self)

        self.paused = False  # 前端暂停：丢弃上行音频（连接保持）
        self.final_title = ""
        self.final_attributes: list[dict] = []  # 累积的事实性属性（key 去重合并）
        self.spk_threshold: float = 0.55  # 说话人聚类阈值，前端可调
        self._audio = bytearray()  # 累计上行 PCM（int16/16kHz/mono），结束时写 wav

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        async with SessionLocal() as db:
            db.add(Meeting(id=self.meeting_id, title=self.title, recorder=self.recorder))
            await db.commit()
        await self.asr.start({
            "spk_threshold": self.spk_threshold,
            "vad_max_end_silence_ms": settings.vad_max_end_silence_ms,
            "vad_max_segment_sec": settings.vad_max_segment_sec,
            "spk_min_seg_sec": settings.spk_min_seg_sec,
        })
        self.scheduler.start()
        logger.info("meeting %s started", self.meeting_id)

    async def stop(self) -> None:
        """结束会议：flush ASR 剩余音频 → 等最终 offline 结果 → 最终总结 → 收尾。"""
        async with self._lock:
            if self._stopped:
                return
            self._stopped = True

        await self.asr.finish()
        try:
            await asyncio.wait_for(self._asr_closed.wait(), timeout=15)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("meeting %s: asr close timeout", self.meeting_id)

        # 收尾：最终总结 + 剩余尾巴属性提取（scheduler.stop 里做了），最后落库 title/attributes
        await self.scheduler.stop(final_summary=True)

        # scheduler 收尾会触发属性提取，final_title/final_attributes 已填充
        self._save_recording()

        async with SessionLocal() as db:
            meeting = await db.get(Meeting, self.meeting_id)
            if meeting:
                meeting.status = "ended"
                meeting.ended_at = datetime.now()
                # AI 归纳的主题覆盖手填标题；空则保留原值
                if self.final_title:
                    meeting.title = self.final_title
                if self.final_attributes:
                    meeting.attributes = json.dumps(self.final_attributes, ensure_ascii=False)
                await db.commit()

        await self.broadcast({"type": "ended", "meeting_id": self.meeting_id})
        logger.info("meeting %s ended", self.meeting_id)

    async def handle_audio(self, pcm: bytes) -> None:
        if self._stopped or self.paused:
            return
        self._audio.extend(pcm)
        await self.asr.send_audio(pcm)

    # ---------- ASR 回调 ----------

    async def _on_asr_online(self, text: str) -> None:
        """流式临时字幕：立即广播，前端标注「识别中」。"""
        await self.broadcast({"type": "transcript_online", "text": text})

    async def _on_asr_offline(self, text: str, timestamp: list, spk: str | None) -> None:
        """句终确定结果：带说话人/时间戳，进缓冲 + 落库 + 广播。"""
        # FunASR 2pass-offline 返回 spk_name：注册声纹名 / 匿名"说话人1" / "unknown"
        speaker = "" if not spk or spk == "unknown" else spk
        start_ms = int(timestamp[0][0]) if timestamp else 0
        end_ms = int(timestamp[-1][1]) if timestamp else 0

        item = await self.buffer.add(speaker, text, start_ms, end_ms)

        async with SessionLocal() as db:
            db.add(Utterance(
                meeting_id=self.meeting_id,
                seq=item.seq,
                speaker=item.speaker,
                text=item.text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
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

    async def _on_asr_error(self, msg: str) -> None:
        await self.broadcast({"type": "error", "message": msg})

    async def _on_asr_closed(self) -> None:
        self._asr_closed.set()

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

    async def extract_attributes(self, text: str) -> None:
        """对新增转写文本提取事实性属性，合并进 final_attributes 并广播给前端。"""
        if not text.strip():
            return
        result = await llm_client.extract_final_attributes(text)
        if result["title"] and not self.final_title:
            self.final_title = result["title"]  # 第一次拿到主题就定住，后续覆盖需手动
        merged: dict[str, str] = {a["key"]: a["value"] for a in self.final_attributes}
        for a in result["attributes"]:
            if isinstance(a, dict) and a.get("key"):
                merged[str(a["key"])] = str(a.get("value", ""))
        self.final_attributes = [{"key": k, "value": v} for k, v in merged.items()]

        # 实时广播最新属性（前端会议信息区即时渲染）
        await self.broadcast({
            "type": "attributes",
            "title": self.final_title,
            "attributes": self.final_attributes,
        })

    async def run_summary(self, final: bool) -> None:
        # 非结束时句子太少就攒着，避免碎窗口浪费 LLM 调用
        if await self.buffer.pending_count() == 0:
            return
        if not final and await self.buffer.pending_count() < settings.summary_min_utterances:
            return
        window_text = await self.buffer.drain_text(settings.summary_max_chars)

        result = await llm_client.summarize_window(self.prev_summary, window_text)
        if not result["points"] and not result["action_items"]:
            return

        # 滚动上下文 = 旧摘要 + 本段要点，截断防膨胀
        combined = self.prev_summary + "\n" + "\n".join(f"· {p}" for p in result["points"])
        self.prev_summary = combined.strip()[-2000:]
        self.summary_seq += 1

        async with SessionLocal() as db:
            db.add(Summary(
                meeting_id=self.meeting_id,
                seq=self.summary_seq,
                window_text=window_text,
                content="\n".join(f"· {p}" for p in result["points"]),
                action_items=json.dumps(result["action_items"], ensure_ascii=False),
                attributes=json.dumps(result["attributes"], ensure_ascii=False),
                title=result["title"],
            ))
            await db.commit()

        await self.broadcast({
            "type": "summary",
            "seq": self.summary_seq,
            "final": final,
            "title": result["title"],
            "points": result["points"],
            "action_items": result["action_items"],
            "attributes": result["attributes"],
        })

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

    def create(self, title: str, recorder: str) -> MeetingSession:
        meeting_id = uuid.uuid4().hex[:12]
        session = MeetingSession(meeting_id, title, recorder)
        self._sessions[meeting_id] = session
        return session

    def get(self, meeting_id: str) -> MeetingSession | None:
        return self._sessions.get(meeting_id)

    def remove(self, meeting_id: str) -> None:
        self._sessions.pop(meeting_id, None)


session_manager = SessionManager()
