"""FunASR websocket 连通性测试：把一段 wav 以 2pass 协议推给服务器并打印返回。

用法（在 gpu8 或任意能连到 funasr server 的机器）：
    venv/bin/python test_asr_client.py [wav_path] [ws_url]

依赖：pip install websockets
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import websockets

DEFAULT_WS = "ws://127.0.0.1:10095"
CHUNK = 3200  # 0.1s @16k int16


async def run(wav_path: str, url: str) -> None:
    with wave.open(wav_path, "rb") as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2, "需要 16kHz 16bit 单声道 wav"
        pcm = w.readframes(w.getnframes())

    # FunASR server 要求子协议 binary（serve 端 subprotocols=["binary"]），不传握手会被拒
    import ssl as _ssl
    ssl_ctx = None
    if url.startswith("wss://"):
        ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
    async with websockets.connect(url, max_queue=64, subprotocols=["binary"], ssl=ssl_ctx) as ws:
        await ws.send(json.dumps({
            "mode": "2pass",
            "chunk_size": [5, 10, 5],
            "chunk_interval": 10,
            "wav_name": Path(wav_path).name,
            "is_speaking": True,
            "itn": True,
        }))

        recv_task = asyncio.create_task(recv_loop(ws))

        for i in range(0, len(pcm), CHUNK):
            await ws.send(pcm[i:i + CHUNK])
            await asyncio.sleep(0.1)  # 模拟实时
        await ws.send(json.dumps({"is_speaking": False}))

        await asyncio.sleep(5)  # 等最终 offline 结果
        recv_task.cancel()


async def recv_loop(ws) -> None:
    try:
        async for raw in ws:
            msg = json.loads(raw)
            mode = msg.get("mode")
            text = msg.get("text", "")
            if mode == "2pass-online" and text:
                print(f"[online ] {text}", flush=True)
            elif mode == "2pass-offline":
                print(f"[offline] {text}  spk={msg.get('spk')}  ts={msg.get('timestamp')}", flush=True)
    except websockets.ConnectionClosed:
        print("[closed]")


if __name__ == "__main__":
    wav = sys.argv[1] if len(sys.argv) > 1 else "test.wav"
    url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WS
    asyncio.run(run(wav, url))
