"""服务端重放触发器：连 WS → start(replay_of) → 收听消息直到 ended。

重放音频按真实节奏喂入（0.1s 一块），全程约等于音频时长。
用法：.venv/bin/python scripts/replay_run.py <source_id> <title> [version_mode]
"""
import asyncio
import json
import sys

import websockets


async def main() -> None:
    src = sys.argv[1]
    title = sys.argv[2]
    vm = sys.argv[3] if len(sys.argv) > 3 else "both"
    async with websockets.connect("ws://127.0.0.1:8010/ws/meeting", max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "start", "title": title, "replay_of": src, "version_mode": vm,
        }))
        meeting_id = ""
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "started":
                meeting_id = msg["meeting_id"]
                print(f"[started] {meeting_id}", flush=True)
            elif t == "error":
                print(f"[error] {msg.get('message')}", flush=True)
            elif t == "ended":
                print(f"[ended] {meeting_id}", flush=True)
                break
        print(f"MEETING_ID={meeting_id}", flush=True)


asyncio.run(main())
