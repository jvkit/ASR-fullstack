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

from app.config import settings, load_hotwords
from app.core.asr_client import FunasrClient
from app.core.roller import RollingEnhancer
from app.core.segment import UtteranceBuffer
from app.db.database import SessionLocal
from app.db.models import Meeting, Summary, Utterance
from app.llm import client as llm_client

logger = logging.getLogger(__name__)


def _is_punct(ch: str) -> bool:
    """中英文标点（FunASR timestamp 不含标点，对齐时跳过）。"""
    import unicodedata

    return unicodedata.category(ch).startswith("P") or ch.isspace()


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
        self._asr_closed = asyncio.Event()
        self._stopped = False
        self._lock = asyncio.Lock()

        self.asr = FunasrClient(
            url=settings.funasr_ws_url,
            hotwords=load_hotwords(),
            on_online=self._on_asr_online,
            on_offline=self._on_asr_offline,
            on_error=self._on_asr_error,
            on_closed=self._on_asr_closed,
        )
        self.roller = RollingEnhancer(self)

        self.paused = False  # 前端暂停：丢弃上行音频（连接保持）
        self.final_title = ""
        self.final_attributes: list[dict] = []  # 累积的事实性属性（key 去重合并）
        self.spk_threshold: float = 0.55  # 说话人聚类阈值，前端可调
        self.spk_match_threshold: float = settings.spk_match_threshold  # 注册声纹匹配阈值，前端可调
        self.reannotate_name_threshold: float | None = None  # 会末重标注声纹阈值（None=用 config 默认），前端可调
        self.speaker_count: int = 0  # 参会人数（前端填写，0=自动估计），会末重标注 oracle
        # 高级调参（前端配置面板可覆盖；默认取 config.py）
        self.max_speakers: int = settings.spk_max_speakers          # 实时聚类人数上限（0=不限）
        self.vad_max_end_silence_ms: int = settings.vad_max_end_silence_ms
        self.vad_max_segment_sec: int = settings.vad_max_segment_sec
        self.spk_min_seg_sec: float = settings.spk_min_seg_sec
        self._audio = bytearray()  # 累计上行 PCM（int16/16kHz/mono），结束时写 wav

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        async with SessionLocal() as db:
            db.add(Meeting(id=self.meeting_id, title=self.title, recorder=self.recorder))
            await db.commit()
        await self.asr.start({
            "spk_threshold": self.spk_threshold,
            "speaker_match_threshold": self.spk_match_threshold,
            "vad_max_end_silence_ms": self.vad_max_end_silence_ms,
            "vad_max_segment_sec": self.vad_max_segment_sec,
            "spk_min_seg_sec": self.spk_min_seg_sec,
            "max_speakers": self.max_speakers,
        })
        logger.info("meeting %s started", self.meeting_id)

    async def stop(self) -> None:
        """结束会议：立刻收尾可落库的部分并广播 ended，重活全部转后台。

        同步段（用户等待的部分）：flush ASR → 等关闭 → 存录音 → 落库 ended + 广播。
        后台段（_finalize）：等在飞滚动轮次 → 会末总结/标签/属性提取 → 更新会议元信息
        → 会末重标注。LLM 网关抽风时这些步骤慢，但用户点结束立即看到结束。
        """
        async with self._lock:
            if self._stopped:
                return
            self._stopped = True

        await self.asr.finish()
        try:
            # 重放测试的「停止」是主动放弃：不干等 ASR flush 缓冲（最多 15s），
            # 已产出的转写保留，未转完的音频丢弃
            timeout = 2 if self.replay_of else 15
            await asyncio.wait_for(self._asr_closed.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("meeting %s: asr close timeout", self.meeting_id)

        self._save_recording()

        # 立刻落库 ended + 广播（用户视角：点结束立即结束）
        async with SessionLocal() as db:
            meeting = await db.get(Meeting, self.meeting_id)
            if meeting:
                meeting.status = "ended"
                meeting.ended_at = datetime.now()
                await db.commit()
        await self.broadcast({"type": "ended", "meeting_id": self.meeting_id})
        logger.info("meeting %s ended (finalization continues in background)", self.meeting_id)

        # 重活转后台：等滚动轮次收尾 → 会末总结 → 重标注
        asyncio.create_task(self._finalize())

    async def _finalize(self) -> None:
        """会末收尾（后台）：最终总结/标题/标签/属性 + 重标注。失败仅记日志。"""
        session_id = self.meeting_id
        try:
            rolling_summary = await self.roller.finish()

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

        # 会末重标注（阿里 pipeline 切分 + ERes2NetV2 标身份）
        from app.core.reannotate import reannotate

        pcm = bytes(self._audio)
        await reannotate(
            session_id, pcm,
            speaker_count=self.speaker_count,
            name_threshold=self.reannotate_name_threshold,
        )

    async def handle_audio(self, pcm: bytes) -> None:
        if self._stopped or self.paused:
            return
        self._audio.extend(pcm)
        await self.asr.send_audio(pcm)

    # ---------- 重放测试（音频来自历史录音，按真实节奏走完整实时管线） ----------

    async def run_replay(self, wav_path: str) -> None:
        """把历史录音按 0.1s 节奏喂进实时管线，模拟真实开会。

        暂停时挂起（不丢节拍，恢复后继续），stop 后立即退出。
        喂完置位 replay_done，由 ws 循环触发正常结束流程。
        """
        import time
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

    # ---------- ASR 回调 ----------

    async def _on_asr_online(self, text: str) -> None:
        """流式临时字幕：立即广播，前端标注「识别中」。"""
        await self.broadcast({"type": "transcript_online", "text": text})

    async def _on_asr_offline(self, text: str, timestamp: list, spk: str | None, char_ts: list) -> None:
        """句终确定结果：带说话人/时间戳，进缓冲 + 落库 + 广播。"""
        # FunASR 2pass-offline 返回 spk_name：注册声纹名 / 匿名"说话人1" / "unknown"
        speaker = "" if not spk or spk == "unknown" else spk
        start_ms = int(timestamp[0][0]) if timestamp else 0
        end_ms = int(timestamp[-1][1]) if timestamp else 0
        # 字级时间戳 [[start_ms, end_ms], ...]：与 text 的非标点字逐一对齐
        # （FunASR 的 timestamp 不含标点，标点继承前一字区间；ts 耗尽则用末条兜底）
        chars: list = []
        if char_ts:
            prev = [int(char_ts[0][0]), int(char_ts[0][1])] if char_ts else [0, 0]
            idx = 0
            for ch in text:
                if idx < len(char_ts) and not _is_punct(ch):
                    prev = [int(char_ts[idx][0]), int(char_ts[idx][1])]
                    idx += 1
                chars.append([prev[0], prev[1]])

        item = await self.buffer.add(speaker, text, start_ms, end_ms, char_ts=chars)

        async with SessionLocal() as db:
            db.add(Utterance(
                meeting_id=self.meeting_id,
                seq=item.seq,
                speaker=item.speaker,
                text=item.text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                char_timestamps=json.dumps(chars, ensure_ascii=False),
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

        # 滚动增强：句终驱动，攒够窗口后打标+纠错（后台执行不阻塞）
        if item.start_ms > 0 or item.end_ms > 0:
            self.roller.on_utterance(item.start_ms, item.end_ms, item.seq)

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
