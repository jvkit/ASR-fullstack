"""会末重标注：3D-Speaker 分离 + GPU 认人/转写 → utterances_re 权威终版。

换芯一期 B（2026-09-20）起唯一生产会末路径：
    原始 PCM → preprocess.denoise_pcm（本地 GTCRN）→ diarize3ds.diarize（本地
    FSMN VAD + CAM++ 嵌入 + 谱聚类，出匿名分段）→ GPU /identify_segments
    （逐段 SeACo ASR[热词/标点/ITN] + 逐簇 ERes2NetV2 声纹库认人）
    → ReAnnotateUtterance 落库

旧路径（meeting_chunk 一体化自研 VAD+匿名聚类）不再被会末使用，代码保留于
gpu_pipeline.meeting_chunk 供会中批次与回滚（见 AGENTS.md §5）。任一环节失败
= 终版失败（返回 False），绝不静默回退旧聚类。

不再依赖会议中的句子边界/字级时间戳（paraformer 离线转写不给字级时间戳；
逐段 ASR 让文字与段边界天然一一对应）。
"""

from __future__ import annotations

import contextlib
import io
import logging
import wave
from pathlib import Path

from sqlalchemy import delete

from app.config import settings, load_hotwords
from app.core.audio_storage import save_denoised_recording, save_segment_audio
from app.db import event_stages
from app.db.database import SessionLocal
from app.db.events import log_event
from app.db.models import DiarSegment, Meeting, ReAnnotateUtterance

logger = logging.getLogger(__name__)


async def reannotate(
    meeting_id: str,
    pcm: bytes,
    name_threshold: float | None = None,
    timeout_sec: float | None = None,
) -> bool:
    """会末重标注主入口（session.stop 里 asyncio.create_task 调用）。失败仅记日志。

    name_threshold: 声纹匹配阈值（None=用 config 默认 reannotate_name_threshold）。
    timeout_sec: GPU /identify_segments 超时（None=用 config identify_segments_timeout_sec）。
    旧签名 anon_clusters/anon_next 已删（方案 B：会中匿名聚类已废，终版每场独立聚类）。
    返回是否产出终版（太短/失败返回 False）。
    """
    try:
        if len(pcm) / 32000 < settings.reannotate_min_sec:
            logger.info("reannotate skip %s: too short", meeting_id)
            return False

        from app.core.diarize3ds import diarize
        from app.core.gpu_pipeline import identify_segments
        from app.core.preprocess import denoise_pcm

        await log_event(meeting_id, event_stages.DENOISE_START)
        # 1) 本地 GTCRN 降噪（推理副本；原始存档不受影响）
        pcm = denoise_pcm(pcm)
        await log_event(meeting_id, event_stages.DENOISE_END,
                        duration_ms=len(pcm) // 32)
        # 降噪后整轨落盘（调试用）
        denoised_path = save_denoised_recording(meeting_id, pcm)
        async with SessionLocal() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                meeting.audio_denoised_path = denoised_path
                await db.commit()

        await log_event(meeting_id, event_stages.DIAR_START)
        # 2) 本地 3D-Speaker 分离（CPU，长会议分钟级）
        segments = diarize(pcm, sr=16000)
        await log_event(meeting_id, event_stages.DIAR_END, segments=len(segments))
        logger.info("reannotate %s: 3ds diarize done, %d segs", meeting_id, len(segments))
        if not segments:
            logger.error("reannotate %s: 3ds produced no segments", meeting_id)
            return False

        # 3) GPU 认人 + 转写（一次 HTTP 批量：整段 wav + 全部边界）
        await log_event(meeting_id, event_stages.ASR_START, segments=len(segments))
        hotwords = load_hotwords().replace(",", " ")
        segs = await identify_segments(pcm, segments, hotwords, timeout_sec=timeout_sec,
                                       name_threshold=name_threshold)
        await log_event(meeting_id, event_stages.ASR_END, segments=len(segs) if segs else 0)
        if segs is None:
            logger.error("reannotate %s: identify_segments failed, final version aborted "
                         "(no fallback to legacy pipeline)", meeting_id)
            return False
        logger.info("reannotate %s: gpu identified %d segs", meeting_id, len(segs))

        async with SessionLocal() as db:
            from app.core.session import display_speaker  # 未命中保留"说话人N"区分度（方案 B）

            await db.execute(
                delete(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == meeting_id)
            )
            await db.execute(
                delete(DiarSegment).where(DiarSegment.meeting_id == meeting_id)
                .where(DiarSegment.source == "final")
            )
            pcm_samples = pcm  # int16 bytes
            for i, s in enumerate(segs):
                start_ms = int(float(s["start"]) * 1000)
                end_ms = int(float(s["end"]) * 1000)
                # 切出该段音频落盘
                seg_pcm = b""
                try:
                    s0 = int(float(s["start"]) * 16000) * 2
                    s1 = int(float(s["end"]) * 16000) * 2
                    seg_pcm = pcm_samples[s0:s1]
                    audio_path = save_segment_audio(meeting_id, "final", i, seg_pcm,
                                                    start_ms, end_ms)
                except Exception:
                    logger.exception("save final segment audio failed")
                    audio_path = None

                db.add(ReAnnotateUtterance(
                    meeting_id=meeting_id,
                    seq=i,
                    speaker=display_speaker(s.get("speaker") or "", keep_anon=True),
                    text=(s.get("text") or "").strip(),
                    raw_text=(s.get("text") or "").strip(),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    audio_path=audio_path,
                    diar_score=float(s.get("score")) if s.get("score") is not None else None,
                ))
                db.add(DiarSegment(
                    meeting_id=meeting_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    cluster_label=str(s.get("cluster_id") or ""),
                    matched_speaker=display_speaker(s.get("speaker") or "", keep_anon=True),
                    score=float(s.get("score")) if s.get("score") is not None else None,
                    audio_path=audio_path,
                    source="final",
                ))
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                meeting.reannotated = True
            await db.commit()

        logger.info("reannotate %s done", meeting_id)
        return True
    except Exception:
        logger.exception("reannotate failed for %s", meeting_id)
        return False


async def reannotate_for_meeting(
    meeting_id: str,
    name_threshold: float | None = None,
) -> bool:
    """按 meeting_id 从盘上读录音重跑重标注（手动触发 / 历史会议补跑）。

    name_threshold: 声纹匹配阈值（None=用 config 默认）。
    """
    wav = Path(settings.recordings_dir) / f"{meeting_id}.wav"
    if not wav.is_file():
        return False
    raw = wav.read_bytes()
    pcm = raw
    with contextlib.suppress(Exception):
        with wave.open(io.BytesIO(raw), "rb") as w:
            pcm = w.readframes(w.getnframes())
    return await reannotate(meeting_id, pcm, name_threshold=name_threshold)
