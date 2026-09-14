"""重放测试：任意音频文件 → 真实开会一样走完整管线 → 历史记录里看结果。

与前端"重放测试"按钮等价，但音频来源是任意 wav（如拉下来的 AISHELL-4 会议），
且喂入速度可自由加速。不注入任何数据：会中批次、LLM 纠错、会末终版全部由
管线真实产出。

用法（在项目根目录，.venv 下）:
    python server/scripts/replay_wav.py <wav路径或recordings下的会议id> [--speed 2] [--title xxx]

示例:
    python server/scripts/replay_wav.py ~/aishell4/S_R003S01C01.wav --speed 2
    python server/scripts/replay_wav.py de61a50cde94 --speed 4   # 重放历史会议录音

速度说明：GPU 管线处理速度约 1.8 倍实时。≤1.5x 时转写基本跟得上说话节奏；
更高倍速积压的部分会在结束后的 flush 阶段补出，最终记录不受影响。
喂入期间可打开历史会议详情页，刷新即可看批次转写实时增长（会中版）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import websockets

WS_URL = "ws://127.0.0.1:8010/ws/meeting"
API_URL = "http://127.0.0.1:8010"
FRONTEND = "https://192.168.30.81:5174"

CHUNK = 3200  # 0.1s @16kHz int16，与前端音频块一致


def load_wav_16k(path: str) -> bytes:
    """读 wav，统一转成 16kHz/mono/int16 PCM（非 16k 用线性插值重采样）。"""
    with wave.open(path, "rb") as w:
        ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise SystemExit(f"仅支持 16bit PCM wav（当前 {sw * 8}bit）: {path}")
    x = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != 16000:
        n = int(len(x) * 16000 / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)
    return x.tobytes()


def resolve_audio(name: str) -> Path:
    p = Path(name)
    if p.is_file():
        return p
    for cand in (Path("server/recordings") / f"{name}.wav",
                 Path("recordings") / f"{name}.wav"):
        if cand.is_file():
            return cand
    raise SystemExit(f"找不到音频: {name}（既不是文件，也不是 recordings/ 下的会议 id）")


async def poll_utterance_count(meeting_id: str) -> int:
    try:
        d = httpx.get(f"{API_URL}/api/meetings/{meeting_id}", timeout=5).json()
        return len(d.get("utterances") or [])
    except Exception:
        return -1


async def main() -> None:
    ap = argparse.ArgumentParser(description="重放测试：像真实开会一样驱动完整管线")
    ap.add_argument("audio", help="wav 路径，或 recordings/ 下的会议 id")
    ap.add_argument("--speed", type=float, default=1.0, help="喂入倍速（默认 1；GPU 处理约 1.8x 实时）")
    ap.add_argument("--title", default=None, help="会议标题（默认 <文件名> 重放）")
    ap.add_argument("--wait-min", type=float, default=15.0, help="等会末终版的最长时间（分钟）")
    args = ap.parse_args()

    path = resolve_audio(args.audio)
    pcm = load_wav_16k(str(path))
    minutes = len(pcm) / 32000 / 60
    title = args.title or f"{path.stem} 重放"
    print(f"音频 {minutes:.1f} 分钟 | 倍速 {args.speed}x | 预计喂入 {minutes / args.speed:.1f} 分钟", flush=True)
    print(f"喂入期间：{FRONTEND} → 历史会议 → 点进本场 → 刷新看批次转写实时增长", flush=True)

    async with websockets.connect(WS_URL, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "type": "start",
            "title": title,
            "client_replay": True,  # 服务端打「重放测试」标记；音频由本脚本喂，不用 replay_of
        }))
        meeting_id = None
        while True:  # 等 started
            msg = json.loads(await ws.recv())
            if msg.get("type") == "started":
                meeting_id = msg["meeting_id"]
                break
            if msg.get("type") == "error":
                print("ERROR:", msg.get("message"), flush=True)
                sys.exit(1)
        print(f"STARTED meeting_id={meeting_id}", flush=True)

        # 按倍速喂 PCM（与服务端 replay_of 无关，纯客户端上行，等同浏览器开麦）
        fed = 0
        total = len(pcm)
        t0 = time.monotonic()
        while fed < total:
            await ws.send(pcm[fed:fed + CHUNK])
            fed += CHUNK
            await asyncio.sleep(0.1 / args.speed)
            if fed % (CHUNK * 600) == 0:  # 每约 1 分钟音频报一次进度
                el = time.monotonic() - t0
                n = await poll_utterance_count(meeting_id)
                print(f"  fed {fed / 32000:.0f}s/{total / 32000:.0f}s "
                      f"(wall {el:.0f}s) | 已转写 {n} 句", flush=True)

        print("喂入完成 → 结束会议，后台 flush/总结/全场重跑中…", flush=True)
        await ws.send(json.dumps({"type": "stop"}))
        while True:  # 等 ended（服务端立刻广播，重活转后台）
            msg = json.loads(await ws.recv())
            if msg.get("type") == "ended":
                break
            if msg.get("type") == "error":
                print("ERROR:", msg.get("message"), flush=True)
                sys.exit(1)

    # 轮询会末终版（长会议全场重跑要几分钟）
    deadline = time.monotonic() + args.wait_min * 60
    detail: dict = {}
    while time.monotonic() < deadline:
        try:
            detail = httpx.get(f"{API_URL}/api/meetings/{meeting_id}", timeout=5).json()
            if detail.get("reannotated"):
                break
        except Exception:
            pass
        await asyncio.sleep(10)

    re = detail.get("utterances_re") or []
    utts = detail.get("utterances") or []
    print(f"\n=== 结果 meeting_id={meeting_id} ===", flush=True)
    print(f"标题: {detail.get('title')}", flush=True)
    print(f"标签: {detail.get('tags')}", flush=True)
    print(f"会中批次: {len(utts)} 句 | 会末终版: {len(re)} 句 | reannotated={detail.get('reannotated')}", flush=True)
    print(f"详情页: {FRONTEND}（历史会议 → 本场）", flush=True)
    if re:
        print("--- 终版前 10 行 ---", flush=True)
        for s in re[:10]:
            t0s, t1s = s["start_ms"] // 1000, s["end_ms"] // 1000
            print(f"  [{t0s:>3}s-{t1s:>3}s] {s['speaker'] or '未识别'}: {s['text'][:40]}", flush=True)
    if not detail.get("reannotated"):
        print("WARN: 终版未生成（可能重标注还在跑或失败，可在详情页点「重新标注」）", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
