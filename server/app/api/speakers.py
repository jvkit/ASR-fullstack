"""REST API：声纹注册管理（转发到 gpu FunASR 服务端的 speaker 控制连接）。"""

import logging

from fastapi import APIRouter, Body, HTTPException, Query

from app.core.speaker_client import speaker_control

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["speakers"])

MIN_ENROLL_BYTES = 16000 * 2 * 3  # 3 秒 @16kHz int16，与 gpu 端一致


@router.get("/speakers")
async def list_speakers() -> dict:
    r = await speaker_control({"mode": "speaker_list"})
    if not r.get("speakers"):
        r["speakers"] = []
    return r


@router.post("/speakers/enroll")
async def enroll_speaker(
    name: str = Query(..., min_length=1, max_length=32),
    audio: bytes = Body(...),
) -> dict:
    """注册声纹：raw PCM(int16/16kHz/mono) body，至少 3 秒。"""
    if len(audio) < MIN_ENROLL_BYTES:
        raise HTTPException(400, f"音频太短：{len(audio) / 32000:.1f}s < 3s，请录 5 秒以上")
    r = await speaker_control({"mode": "enroll", "spk_name": name.strip()}, audio=audio)
    if not r.get("ok"):
        raise HTTPException(502, f"注册失败：{r.get('error', 'unknown')}")
    return r


@router.delete("/speakers/{name}")
async def delete_speaker(name: str) -> dict:
    r = await speaker_control({"mode": "speaker_delete", "spk_name": name})
    if not r.get("ok"):
        raise HTTPException(502, f"删除失败：{r.get('error', 'unknown')}")
    return r
