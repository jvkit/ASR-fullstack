"""驱动 WS 重放 7min 音频，走完整真实管线（FunASR 实时->roller->LLM->会后重标注）。

不注入任何数据：实时版句子、会后版子段全部由 pipeline 产出。
用法: python scripts/replay_drive.py <wav_name> [title]
"""
import asyncio
import json
import sys

import websockets

WS_URL = "ws://127.0.0.1:8010/ws/meeting"


async def main() -> None:
    wav_name = sys.argv[1] if len(sys.argv) > 1 else "aishell4_7min"
    title = sys.argv[2] if len(sys.argv) > 2 else "AISHELL-4 7分钟重放"
    async with websockets.connect(WS_URL, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "type": "start",
            "title": title,
            "replay_of": wav_name,
            "speaker_count": 6,
            "reannotate_name_threshold": 0.30,
        }))
        meeting_id = None
        n_transcript = 0
        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")
            if t == "started":
                meeting_id = msg.get("meeting_id")
                print("STARTED meeting_id=%s" % meeting_id, flush=True)
            elif t == "transcript":
                n_transcript += 1
                if n_transcript % 20 == 0:
                    print("... %d transcripts" % n_transcript, flush=True)
            elif t == "ended":
                print("ENDED meeting_id=%s transcripts=%d" % (meeting_id, n_transcript), flush=True)
                break
            elif t == "error":
                print("ERROR:", msg.get("message"), flush=True)
                sys.exit(1)
        # ended 后等会后重标注（异步任务）落库
        await asyncio.sleep(5)
    print("DONE %s" % meeting_id, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
