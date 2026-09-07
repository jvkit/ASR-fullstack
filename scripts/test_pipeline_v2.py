"""新管线 e2e：真实双人录音流式推入，验证：

1. 有效语音 ≥8s 成窗（滚动轮次触发，打标回写 speaker）
2. 合并 LLM 调用（transcript_update 带 corrected=true + summary 广播）
3. stop 立即返回 ended（不等会末总结，finalize 后台完成）
4. 会末 tags/summary 落库

用法：python scripts/test_pipeline_v2.py [wav 相对 server/ 的路径] [推送秒数]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

import websockets

CHUNK = 3200  # 100ms @16kHz int16
BACKEND = "ws://localhost:8010"
WAV = sys.argv[1] if len(sys.argv) > 1 else "recordings/bf1c463a4da4.wav"
MAX_SEC = float(sys.argv[2]) if len(sys.argv) > 2 else 60


def load_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


async def main() -> None:
    pcm = load_pcm(Path(WAV))
    n_frames = min(len(pcm), int(MAX_SEC * 32000))
    pcm = pcm[:n_frames]
    updates: list[dict] = []
    summaries: list[dict] = []
    transcripts: list[dict] = []

    t0 = time.monotonic()
    async with websockets.connect(f"{BACKEND}/ws/meeting", max_queue=64) as ws:
        await ws.send(json.dumps({"type": "start", "title": "v2管线测试", "speaker_count": 2}))
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "started":
                meeting_id = msg["meeting_id"]
                print(f"started: {meeting_id}")
                break

        async def recv_loop() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg["type"]
                    if t == "transcript":
                        transcripts.append(msg)
                        print(f"  [{time.monotonic()-t0:5.1f}s] transcript [{msg['speaker']}] {msg['text'][:30]}")
                    elif t == "transcript_update":
                        updates.append(msg)
                        tag = "CORRECTED" if msg.get("corrected") else "spk-update"
                        print(f"  [{time.monotonic()-t0:5.1f}s] update({tag}) seq={msg['seq']} [{msg['speaker']}] {msg['text'][:30]}")
                    elif t == "summary":
                        summaries.append(msg)
                        print(f"  [{time.monotonic()-t0:5.1f}s] summary final={msg['final']} pts={len(msg['points'])}")
                    elif t == "ended":
                        print(f"  [{time.monotonic()-t0:5.1f}s] *** ended ***")
                    elif t == "error":
                        print(f"  [{time.monotonic()-t0:5.1f}s] ERROR: {msg['message'][:80]}")
            except websockets.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(recv_loop())
        for i in range(0, len(pcm), CHUNK):
            await ws.send(pcm[i:i + CHUNK])
            await asyncio.sleep(0.04)  # 2.5x 实速推流

        stop_t = time.monotonic()
        await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(recv_task, timeout=30)
        except asyncio.TimeoutError:
            pass
        stop_latency = time.monotonic() - stop_t

    print(f"\n=== 结果 ===")
    print(f"推流 {n_frames/32000:.0f}s, transcripts={len(transcripts)}, updates={len(updates)} "
          f"(corrected={sum(1 for u in updates if u.get('corrected'))}), summaries={len(summaries)}")
    print(f"stop→连接关闭延迟: {stop_latency:.1f}s")

    with urllib.request.urlopen(f"http://localhost:8010/api/meetings/{meeting_id}") as r:
        detail = json.loads(r.read())
    print(f"status={detail['status']} tags={detail.get('tags')} title={detail['title']}")
    # 等 finalize 后台跑完再查一次
    await asyncio.sleep(50)
    with urllib.request.urlopen(f"http://localhost:8010/api/meetings/{meeting_id}") as r:
        detail = json.loads(r.read())
    print(f"[finalize后] status={detail['status']} tags={detail.get('tags')} title={detail['title']}")
    with urllib.request.urlopen(f"http://localhost:8010/api/meetings/{meeting_id}/summaries") as r:
        sums = json.loads(r.read())
    print(f"summaries in db: {len(sums)}, final: {[s['title'] for s in sums]}")

    ok = (
        len(transcripts) > 0
        and any(u.get("corrected") for u in updates)
        and stop_latency < 20
        and detail["status"] == "ended"
    )
    print(f"\nstop 延迟判据: <20s (实际 {stop_latency:.1f}s)")
    print("\n" + ("OK" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
