"""会末重标注：实时句子边界 + 逐句 ERes2NetV2 声纹命名 -> utterances_re 落库。

方案 v3（句子级声纹）：
1. 会议结束后异步触发（不阻塞 ended 广播）
2. 读实时版句子（utterances 表：时间边界 + 原文），录音整包 + 句子边界上传
   gpu:10096 /diarize_sent；gpu 端逐句 ERes2NetV2 embedding 对注册声纹库
   （多段质心）argmax，低置信度/碎句按相邻句平滑
3. 每句一行写 utterances_re（文字直接用实时原文，无任何跨段分配），
   meeting.reannotated = True

为什么不再用阿里 pipeline 切分（v2 /diarize_labeled）：
- 同一音频连跑 3 次切出 13/8/8 段（内部谱聚类非确定），重跑结果必抖
- oracle=2 直切会把连续对话并成一大段；过采样+层次聚类对切分结果高度敏感，
  簇质心相似度会在 0.69/0.24 之间跳，跳到 MATCH_FLOOR 以下整场坍缩成单说话人
- 而实时版 FunASR 2pass 的句子边界本身是准的，逐句声纹 argmax（GPU 上用
  de61a50cde94 验证过：复跑两次完全一致，ground truth 句全部分对）
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import wave
from pathlib import Path

import httpx
from sqlalchemy import delete, select

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, ReAnnotateUtterance, Utterance

logger = logging.getLogger(__name__)


async def reannotate(
    meeting_id: str,
    pcm: bytes,
    speaker_count: int = 0,
    name_threshold: float | None = None,
) -> None:
    """会末重标注主入口（session.stop 里 asyncio.create_task 调用）。失败仅记日志。

    speaker_count: 保留参数（v2 遗留，句子级方案不再需要人数 oracle，忽略）。
    name_threshold: 声纹匹配阈值（None=用 config 默认 reannotate_name_threshold）。
    """
    try:
        if len(pcm) / 32000 < settings.reannotate_min_sec:
            logger.info("reannotate skip %s: too short", meeting_id)
            return

        async with SessionLocal() as db:
            rows = (await db.execute(
                select(Utterance).where(Utterance.meeting_id == meeting_id).order_by(Utterance.seq)
            )).scalars().all()
            sents = [[max(0, u.start_ms) / 1000, u.end_ms / 1000] for u in rows]
            texts = [u.text for u in rows]
        if not sents:
            logger.info("reannotate skip %s: no realtime utterances", meeting_id)
            return

        wav_full = _pcm_to_wav(pcm)
        thr = name_threshold if name_threshold and 0 < name_threshold < 1 else settings.reannotate_name_threshold
        async with httpx.AsyncClient(timeout=600) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/diarize_sent",
                files={"file": (f"{meeting_id}.wav", wav_full, "audio/wav")},
                data={"sents": json.dumps(sents), "name_threshold": str(thr)},
            )
            r.raise_for_status()
            diar = r.json()

        segs: list[list] = diar["segs"]
        logger.info(
            "reannotate %s: %d sents, registered=%s",
            meeting_id, len(segs), diar.get("registered"),
        )

        async with SessionLocal() as db:
            await db.execute(
                delete(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == meeting_id)
            )
            for i, seg in enumerate(segs):
                # segs 与 sents 一一对应：时间取句子边界，文字取实时原文
                name = seg[3] if len(seg) > 3 else ""
                db.add(ReAnnotateUtterance(
                    meeting_id=meeting_id,
                    seq=i,
                    speaker=name,
                    text=texts[i],
                    start_ms=int(seg[0] * 1000),
                    end_ms=int(seg[1] * 1000),
                ))
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                meeting.reannotated = True
            await db.commit()

        logger.info("reannotate %s done", meeting_id)
    except Exception:
        logger.exception("reannotate failed for %s", meeting_id)


async def reannotate_for_meeting(
    meeting_id: str,
    speaker_count: int = 0,
    name_threshold: float | None = None,
) -> bool:
    """按 meeting_id 从盘上读录音重跑重标注（手动触发 / 历史会议补跑）。

    speaker_count: 保留参数（兼容 API 签名，已不使用）。
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
    await reannotate(meeting_id, pcm, speaker_count=speaker_count, name_threshold=name_threshold)
    return True


def _pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()
