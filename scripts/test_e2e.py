"""端到端冒烟：模拟浏览器走后端 /ws/meeting。

验证：
1. start 消息里的 spk_threshold 透传到 FunASR（影响聚类）
2. 结束后 server/recordings/{meeting_id}.wav 落盘
3. GET /api/meetings/{id}/audio 可下载、GET detail 带 audio_url

用 campplus 示例音频：sp1_a/sp1_b 同一人，sp2_a 另一人。
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
import wave
from pathlib import Path

import websockets

CHUNK = 3200
WAVS = Path(__file__).parent / "wavs"
BACKEND = "ws://localhost:8010"
THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.55


def load_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


async def main() -> None:
    transcripts: list[dict] = []
    async with websockets.connect(f"{BACKEND}/ws/meeting", max_queue=64) as ws:
        await ws.send(json.dumps({
            "type": "start", "title": "冒烟测试", "spk_threshold": THRESHOLD,
        }))

        # 收 started
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "started":
                meeting_id = msg["meeting_id"]
                print(f"started: {meeting_id}")
                break

        # 推流（后台收消息）
        async def recv_loop() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg["type"]
                    if t == "transcript":
                        transcripts.append(msg)
                        print(f"  [{msg['speaker']}] {msg['text'][:40]}")
                    else:
                        print(f"  (msg: {t})")
            except websockets.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(recv_loop())
        silence = b"\x00" * CHUNK
        for name in ["speaker1_a_cn_16k.wav", "speaker1_b_cn_16k.wav", "speaker2_a_cn_16k.wav"]:
            data = load_pcm(WAVS / name)
            for i in range(0, len(data), CHUNK):
                await ws.send(data[i:i + CHUNK])
                await asyncio.sleep(0.1)  # 实时节奏
            # 段间送 2s 静音（模拟真实会议换气停顿），否则 VAD 收不到静音无法断句
            for _ in range(20):
                await ws.send(silence)
                await asyncio.sleep(0.1)
            await asyncio.sleep(2)  # 留时间给 offline 出结果

        await ws.send(json.dumps({"type": "stop"}))
        await recv_task

    # 校验录音落盘 + API
    wav_path = Path(__file__).parent.parent / "server" / "recordings" / f"{meeting_id}.wav"
    print(f"\nwav exists: {wav_path.is_file()}")
    if wav_path.is_file():
        with wave.open(str(wav_path), "rb") as w:
            print(f"  duration: {w.getnframes() / 16000:.1f}s, ch={w.getnchannels()}, rate={w.getframerate()}")

    with urllib.request.urlopen(f"http://localhost:8010/api/meetings/{meeting_id}") as r:
        detail = json.loads(r.read())
        print(f"audio_url: {detail.get('audio_url')}")
    req = urllib.request.urlopen(f"http://localhost:8010/api/meetings/{meeting_id}/audio")
    print(f"audio api: {req.status}, {req.headers.get('Content-Type')}, {len(req.read())} bytes")

    speakers = sorted({t["speaker"] for t in transcripts})
    print(f"\nspeakers: {speakers}")
    print("OK" if len(speakers) >= 2 else "FAIL: 未区分出说话人")


if __name__ == "__main__":
    asyncio.run(main())
