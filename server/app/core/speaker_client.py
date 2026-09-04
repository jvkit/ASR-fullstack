"""声纹管理客户端：与 gpu FunASR 服务端的 speaker 控制短连接通信。

协议（首条 JSON 消息按 mode 分流，不走 ASR 管线）：
    {"mode": "speaker_list"}                          → {"mode": "speaker_list_result", "speakers": [...]}
    {"mode": "speaker_delete", "spk_name": "张三"}     → {"mode": "speaker_delete_result", ...}
    {"mode": "enroll", "spk_name": "张三"} + PCM 流 + {"is_speaking": false}
                                                      → {"mode": "enroll_result", ok/speakers/error}
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl

import websockets

from app.config import settings

logger = logging.getLogger(__name__)

CHUNK = 3200  # 3200 bytes = 0.1s @16kHz int16，与 ASR 上行同节拍
CTRL_TIMEOUT_SEC = 60  # enroll 要收完音频再算 embedding，给足时间


def _ssl_ctx() -> ssl.SSLContext | None:
    if settings.funasr_ws_url.startswith("wss://"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


async def speaker_control(payload: dict, audio: bytes | None = None) -> dict:
    """发一条控制消息；enroll 时附带 PCM 流。返回服务端 result JSON。"""
    async with websockets.connect(
        settings.funasr_ws_url,
        subprotocols=["binary"],
        ssl=_ssl_ctx(),
        max_queue=256,
        open_timeout=15,
    ) as ws:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        if audio is not None:
            for i in range(0, len(audio), CHUNK):
                await ws.send(audio[i:i + CHUNK])
            await ws.send(json.dumps({"is_speaking": False}))
        raw = await asyncio.wait_for(ws.recv(), timeout=CTRL_TIMEOUT_SEC)
        return json.loads(raw)
