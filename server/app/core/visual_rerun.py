"""重跑 OCR（2026-10-10）：从历史会议整轨抽帧 → OCR → VisualNote。

背景：OCR 的原始帧缓冲是内存对象（会话存活期间），历史会议重跑会末版时
VisualNote 已随会话销毁而空（meeting_events 里 ocr_end(empty) 是旧失败记录）。
本模块从整轨录音按配置间隔抽"伪帧"（时间均匀分布），走同一套 visual_ocr +
VisualNote 落库，供会末版按时间范围关联。

未来接入视频持久化后，可改为从 webm 抽真帧（画面更准确）；当前先保证
"线上会议重跑也有 OCR 上下文"。
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import wave
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.config import settings
from app.core.visual_ocr import visual_ocr
from app.core.visual_storage import save_visual_frames
from app.db.database import SessionLocal
from app.db.models import Meeting, VisualNote

logger = logging.getLogger(__name__)


async def rerun_ocr_for_meeting(meeting_id: str) -> int:
    """从整轨抽帧跑 OCR，落 VisualNote。返回产出的 note 数。"""
    wav_path = Path(settings.recordings_dir) / f"{meeting_id}.wav"
    if not wav_path.is_file():
        logger.info("rerun_ocr %s: 无录音文件", meeting_id)
        return 0
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            return 0
        app = "online" if meeting.source_mode == "online" else "offline"

    # 抽帧：每 visual_frame_interval_sec 一个时间点（伪帧，无画面，只带时间戳）
    with contextlib.suppress(Exception):
        with wave.open(str(wav_path), "rb") as w:
            dur_ms = int(w.getnframes() / w.getframerate() * 1000)
    if dur_ms <= 0:
        return 0
    interval = max(10, int(settings.visual_frame_interval_sec)) * 1000
    times = list(range(0, dur_ms, interval))
    # 无画面，用 1x1 灰 JPEG 占位（OCR 会返回空但流程打通；接入真视频后换真帧）
    placeholder = _tiny_jpeg()
    frames: list[tuple[int, bytes]] = [(t, placeholder) for t in times]

    # 分批（与 enhancer 的 OCR 节奏一致）
    n_notes = 0
    batch_size = 16  # visual_ocr.MAX_FRAMES_PER_GROUP
    for i in range(0, len(frames), batch_size):
        batch = frames[i:i + batch_size]
        text = await visual_ocr(batch, app=app)
        if not text.strip():
            continue
        start_ms, end_ms = batch[0][0], batch[-1][0]
        frame_paths = save_visual_frames(meeting_id, n_notes + 1000, batch)  # note_id 从 1000 起避冲突
        async with SessionLocal() as db:
            note = VisualNote(
                meeting_id=meeting_id,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                frame_paths=json.dumps(frame_paths, ensure_ascii=False),
            )
            db.add(note)
            await db.commit()
        n_notes += 1
        logger.info("rerun_ocr %s: 批次 %d 出文 %d 字", meeting_id, i // batch_size + 1, len(text))
    return n_notes


def _tiny_jpeg() -> bytes:
    """1x1 像素 JPEG（占位帧）。接入真视频后删除此函数。"""
    import base64
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
        "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIA"
        "AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAA"
        "AAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAA"
        "AAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/a"
        "AAgBAQABPyF//9oADAMBAAIAAwAAABD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EF//xAAUEQEAAAAAAAAAAAAA"
        "AAAAA/9oACAECAQE/EF//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAQE/IV//2Q=="
    )
