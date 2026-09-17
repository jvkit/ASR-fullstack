"""REST API：声纹库管理（唯一权威库在 GPU diarize_svc 的 speaker_enrolls/*.pcm）。

2026-09-15 单库改造：原 FunASR speaker_db.json 双库架构废弃（假删除根因——
DELETE 只删列表库、认人用的 pcm 段不动，且两库成员互相不一致）。
list/enroll/delete/identify 全部转发 diarize_svc。
"""

import logging

import httpx
from fastapi import APIRouter, Body, HTTPException, Query

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["speakers"])

MIN_ENROLL_BYTES = 16000 * 2 * 3  # 3 秒 @16kHz int16，与 gpu 端一致


@router.get("/speakers")
async def list_speakers() -> dict:
    """已注册声纹列表（pcm 目录为唯一权威库）。"""
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(f"{settings.diarize_http_url}/speaker_names")
            r.raise_for_status()
            return r.json()
    except Exception:
        logger.exception("speaker_names failed")
        raise HTTPException(502, "声纹服务不可用")


@router.post("/speakers/enroll")
async def enroll_speaker(
    name: str = Query(..., min_length=1, max_length=32),
    audio: bytes = Body(...),
) -> dict:
    """智能注册：raw PCM(int16/16kHz/mono) body。

    GPU 端做 VAD 切段 + 质量过滤（太短/太轻/爆音/声纹不一致丢弃）+
    多段质心留存，返回 kept/rejected 明细；不合格 ok=False 不落库。
    """
    if len(audio) < MIN_ENROLL_BYTES:
        raise HTTPException(400, f"音频太短：{len(audio) / 32000:.1f}s < 3s，请自然说 20~60 秒")
    from app.core.preprocess import denoise_pcm
    audio = denoise_pcm(audio)  # 与会议推理同信号处理，防向量漂移
    params = {
        "min_total_sec": settings.enroll_min_total_sec,
        "min_seg_sec": settings.enroll_min_seg_sec,
        "min_segments": settings.enroll_min_segments,
        "min_effective_sec": settings.enroll_min_effective_sec,
        "max_keep": settings.enroll_max_keep,
        "consistency": settings.enroll_consistency,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.enroll_timeout_sec) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/speaker_enroll",
                params={"name": name.strip(), **params},
                content=audio,
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_enroll failed")
        raise HTTPException(502, "注册服务不可用，请稍后重试")


@router.post("/speakers/identify")
async def identify_speaker(audio: bytes = Body(...)) -> dict:
    """录入检测：几秒语音 -> 命中声纹库里的谁（返回全候选相似度降序）。

    兼作注册质量自查：注册完立刻测，能稳定认出自己=注册合格。
    """
    if len(audio) < 16000 * 2 * 1.5:
        raise HTTPException(400, f"音频太短：{len(audio) / 32000:.1f}s，请说 3~10 秒")
    from app.core.preprocess import denoise_pcm
    audio = denoise_pcm(audio)
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/speaker_identify",
                params={"match_threshold": str(settings.identify_match_threshold)},
                content=audio,
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_identify failed")
        raise HTTPException(502, "检测服务不可用，请稍后重试")


@router.delete("/speakers/{name}")
async def delete_speaker(name: str) -> dict:
    """真删除：删掉此人全部 pcm 注册段并失效缓存，立即生效。"""
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/speaker_delete",
                data={"name": name},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_delete failed")
        raise HTTPException(502, "删除服务不可用，请稍后重试")
