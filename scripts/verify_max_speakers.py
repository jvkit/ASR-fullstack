"""验证 gpu FunASR max_speakers patch：发 max_speakers=2，看实时层是否仍冒出说话人3。

用法：python scripts/verify_max_speakers.py [wav] [sec]
"""
from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import websockets

CHUNK = 3200
WAV = sys.argv[1] if len(sys.argv) > 1 else "recordings/bf1c463a4da4.wav"
MAX_SEC = float(sys.argv[2]) if len(sys.argv) > 2 else 45


def load_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


async def main() -> None:
    pcm = load_pcm(Path(WAV))[:int(MAX_SEC * 32000)]
    speakers: list[str] = []  # 实时层原始标签（transcript 不带 corrected）

    async with websockets.connect("ws://localhost:8010/ws/meeting", max_queue=64) as ws:
        await ws.send(json.dumps({
            "type": "start", "title": "max_spk验证", "speaker_count": 2, "max_speakers": 2,
        }))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "started":
                print("started", msg["meeting_id"])
                break

        async def recv_loop() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg["type"]
                    if t == "transcript":
                        spk = msg.get("speaker") or "发言人"
                        speakers.append(spk)
                        print(f"  [{msg['text'][:25]}] <- {spk}")
                    elif t == "transcript_update" and not msg.get("corrected"):
                        pass  # 打标回写，忽略
                    elif t == "ended":
                        print("*** ended ***")
            except websockets.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(recv_loop())
        for i in range(0, len(pcm), CHUNK):
            await ws.send(pcm[i:i + CHUNK])
            await asyncio.sleep(0.04)
        await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(recv_task, timeout=25)
        except asyncio.TimeoutError:
            pass

    # 统计实时层出现的原始标签（去掉注册名和空）
    raw = [s for s in speakers if s and not s.startswith("说话人")]
    anon = sorted(set(s for s in speakers if s.startswith("说话人")))
    print(f"\n实时层匿名标签: {anon}")
    print(f"注册名: {sorted(set(raw))}")
    n_anon = len(anon)
    print(f"\n匿名说话人数: {n_anon}  (max_speakers=2, 期望 ≤2)" if n_anon <= 2 else f"\n匿名说话人数: {n_anon}  (max_speakers=2 未生效!)")


if __name__ == "__main__":
    asyncio.run(main())
