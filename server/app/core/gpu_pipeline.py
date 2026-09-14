"""GPU 非实时一体化管线客户端（/meeting_chunk + /vad）。

/meeting_chunk: wav -> [{start,end,speaker,text}]（归一化+VAD+逐子段ASR+ER认人）
/vad:          wav -> [[start_s, end_s], ...]（批次切点用）
"""

from __future__ import annotations

import io
import json
import logging
import wave

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


async def vad_ends(pcm: bytes) -> list[float] | None:
    """VAD 句子结束点（相对输入音频的毫秒）。失败返回 None（调用方跳过本轮）。"""
    try:
        async with httpx.AsyncClient(timeout=settings.pipeline_vad_timeout_sec) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/vad",
                files={"file": ("chunk.wav", pcm_to_wav(pcm), "audio/wav")},
                data={"max_end_silence_ms": str(settings.pipeline_vad_max_end_silence_ms)},
            )
            r.raise_for_status()
            sents = r.json().get("sents", [])
        return [float(b) * 1000 for _, b in sents]
    except Exception:
        logger.exception("vad endpoints failed")
        return None


async def meeting_chunk(pcm: bytes, hotwords: str = "", timeout_sec: float | None = None) -> list[dict] | None:
    """一体化管线：音频 -> 带说话人的子段列表（时间相对本块音频，秒）。

    返回 [{start, end, speaker, text}]；失败返回 None。
    timeout_sec: 覆盖默认 pipeline_chunk_timeout_sec（会末全场重跑传大值）。
    """
    try:
        data: dict = {
            "name_threshold": str(settings.reannotate_name_threshold),
            "max_end_silence_ms": str(settings.pipeline_vad_max_end_silence_ms),
        }
        if hotwords:
            data["hotwords"] = hotwords
        async with httpx.AsyncClient(timeout=timeout_sec or settings.pipeline_chunk_timeout_sec) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/meeting_chunk",
                files={"file": ("chunk.wav", pcm_to_wav(pcm), "audio/wav")},
                data=data,
            )
            r.raise_for_status()
            return r.json().get("segs", [])
    except Exception:
        logger.exception("meeting_chunk failed")
        return None
