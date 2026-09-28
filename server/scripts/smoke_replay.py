#!/usr/bin/env python3
"""P0 基线冒烟：服务端重放历史录音走完整实时管道（批次→波次→会末），验证全链路。

用法: .venv/bin/python server/scripts/smoke_replay.py <recording_id> [--url ws://127.0.0.1:8010/ws/meeting]
退出码 0 = 收到 ended 且 DB 有数据；1 = 失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording_id")
    ap.add_argument("--url", default="ws://127.0.0.1:8010/ws/meeting")
    ap.add_argument("--title", default="P0冒烟重放")
    args = ap.parse_args()

    counts: dict[str, int] = {}
    t0 = time.time()
    async with websockets.connect(args.url, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "title": args.title,
                                  "replay_of": args.recording_id}))
        meeting_id = None
        while True:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1500))
            except (TimeoutError, asyncio.TimeoutError):
                print("✗ 1500s 无消息，超时", flush=True)
                return 1
            t = msg.get("type")
            counts[t] = counts.get(t, 0) + 1
            if t == "started":
                meeting_id = msg.get("meeting_id")
                print(f"started meeting={meeting_id}", flush=True)
            elif t == "transcript":
                if counts["transcript"] == 1 or counts["transcript"] % 20 == 0:
                    print(f"  [{time.time()-t0:6.0f}s] transcript #{counts['transcript']}", flush=True)
            elif t == "summary":
                print(f"  [{time.time()-t0:6.0f}s] summary #{counts['summary']}", flush=True)
            elif t == "error":
                print(f"  !! ERROR: {msg}", flush=True)
            elif t == "reannotated":
                print(f"  [{time.time()-t0:6.0f}s] reannotated", flush=True)
            elif t == "ended":
                print(f"ended after {time.time()-t0:.0f}s, counts={counts}", flush=True)
                break

    if not meeting_id:
        print("✗ 无 meeting_id")
        return 1
    db = ROOT / "server" / "meeting.db"
    con = sqlite3.connect(db)
    u = con.execute("select count(*) from utterances where meeting_id=?", (meeting_id,)).fetchone()[0]
    s = con.execute("select count(*) from summaries where meeting_id=?", (meeting_id,)).fetchone()[0]
    re_ = con.execute("select count(*) from utterances_re where meeting_id=?", (meeting_id,)).fetchone()[0]
    print(f"DB: utterances={u} summaries={s} utterances_re={re_}")
    ok = u > 0 and s > 0

    # 等会末版收尾（final_pass 在 ended 后后台跑：重标注→会末波→会末总结）
    print("等待会末版 final_pass 收尾…", flush=True)
    for i in range(120):  # 最长 30min
        con = sqlite3.connect(db)
        fin = con.execute(
            "select count(*) from summaries where meeting_id=? and scope='final' and seq=0",
            (meeting_id,)).fetchone()[0]
        acts = con.execute(
            "select action_items from meetings where id=?", (meeting_id,)).fetchone()[0]
        con.close()
        if fin:
            print(f"会末终版已出（第 {i*15}s 轮询命中）；meeting.action_items={acts[:120]}")
            break
        await asyncio.sleep(15)
    else:
        print("✗ 30min 内会末终版未出（检查后端日志）")
        return 1

    con = sqlite3.connect(db)
    rows = con.execute(
        "select seq, scope, substr(title,1,26), length(action_items) from summaries "
        "where meeting_id=? order by scope, seq", (meeting_id,)).fetchall()
    print("summaries:", rows)
    print("✓ 冒烟通过（含会末版）" if ok else "✗ 数据为空")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
