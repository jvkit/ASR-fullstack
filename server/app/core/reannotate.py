"""会末重标注：会议录音全场重跑 /meeting_chunk 一体化管线 → utterances_re 权威终版。

非实时批次架构下，会议中批次（utterances 表）与终版用的是同一个 GPU 端点：
    归一化 → fsmn VAD 切句 → SeACo Paraformer 逐子段转写 → ERes2NetV2 逐句声纹命名
区别只在输入范围：批次喂 8~15s 增量，终版喂全场录音，结果天然更准
（边界完整、说话人上下文充分）。

不再依赖会议中的句子边界/字级时间戳（实时版已砍掉，paraformer 离线转写
不给字级时间戳；逐子段 ASR 让文字与子段时间天然一一对应）。
"""

from __future__ import annotations

import contextlib
import io
import logging
import wave
from pathlib import Path

from sqlalchemy import delete, select

from app.config import settings, load_hotwords
from app.core.gpu_pipeline import meeting_chunk
from app.db.database import SessionLocal
from app.db.models import Meeting, ReAnnotateUtterance

logger = logging.getLogger(__name__)


async def reannotate(
    meeting_id: str,
    pcm: bytes,
    name_threshold: float | None = None,
) -> bool:
    """会末重标注主入口（session.stop 里 asyncio.create_task 调用）。失败仅记日志。

    name_threshold: 声纹匹配阈值（None=用 config 默认 reannotate_name_threshold，
    与会议中批次共用一个旋钮）。
    返回是否产出终版（太短/失败返回 False）。
    """
    try:
        if len(pcm) / 32000 < settings.reannotate_min_sec:
            logger.info("reannotate skip %s: too short", meeting_id)
            return False

        thr = name_threshold if name_threshold and 0 < name_threshold < 1 \
            else settings.reannotate_name_threshold
        hotwords = load_hotwords().replace(",", " ")
        segs = await meeting_chunk(pcm, hotwords)
        if segs is None:
            logger.error("reannotate %s: meeting_chunk failed", meeting_id)
            return False
        logger.info("reannotate %s: %d segs", meeting_id, len(segs))

        async with SessionLocal() as db:
            await db.execute(
                delete(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == meeting_id)
            )
            for i, s in enumerate(segs):
                db.add(ReAnnotateUtterance(
                    meeting_id=meeting_id,
                    seq=i,
                    speaker=s.get("speaker") or "",
                    text=(s.get("text") or "").strip(),
                    start_ms=int(float(s["start"]) * 1000),
                    end_ms=int(float(s["end"]) * 1000),
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
