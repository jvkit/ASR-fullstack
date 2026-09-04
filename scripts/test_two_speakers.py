"""双说话人验证：同一条 ws 连接里依次喂 3 段音频（spk1_a / spk1_b / spk2_a）。

预期：spk1_a、spk1_b → 说话人1（同一人不同语句，聚类应命中同一质心）
      spk2_a       → 说话人2（新人，超过阈值差 → 新建质心）
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import wave
from pathlib import Path

import websockets

CHUNK = 3200
EX = Path(__file__).parent / "wavs"


def load_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


async def send_utterance(ws, pcm: Path, name: str, results: list) -> None:
    """一次发言 = 配置 is_speaking=true → 推流 → is_speaking=false → 等 offline。"""
    await ws.send(json.dumps({
        "mode": "2pass", "chunk_size": [5, 10, 5], "chunk_interval": 10,
        "wav_name": name, "is_speaking": True, "itn": True,
    }))
    data = load_pcm(pcm)
    for i in range(0, len(data), CHUNK):
        await ws.send(data[i:i + CHUNK])
        await asyncio.sleep(0.1)
    await ws.send(json.dumps({"is_speaking": False}))
    # 等到本段的 offline 结果
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        msg = json.loads(raw)
        if msg.get("mode") == "2pass-offline":
            text = msg.get("text", "")
            spk = msg.get("spk_name")
            if text:
                results.append((name, spk, text))
                print(f"[{name}] spk={spk}  {text}", flush=True)
                return


async def main() -> None:
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    results: list = []
    async with websockets.connect(
        "wss://192.168.30.87:10095", subprotocols=["binary"], ssl=ssl_ctx, max_queue=64,
    ) as ws:
        # 注意 server 只对 wss 严格，ws 本机也通（前面测试过）；若拒则换 wss
        await send_utterance(ws, EX / "speaker1_a_cn_16k.wav", "A1", results)
        await send_utterance(ws, EX / "speaker1_b_cn_16k.wav", "A2", results)
        await send_utterance(ws, EX / "speaker2_a_cn_16k.wav", "B1", results)
    print("=== summary ===")
    for name, spk, _ in results:
        print(f"{name} -> {spk}")


if __name__ == "__main__":
    asyncio.run(main())
