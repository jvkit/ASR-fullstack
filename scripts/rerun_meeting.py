"""把留存录音按实时节奏重新推一遍当前 FunASR 管线，并按说话人拆分音频供听辨。

用法: python scripts/rerun_meeting.py <wav_path> [输出目录]
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import wave
from datetime import datetime
from pathlib import Path

import websockets

CHUNK = 3200  # 100ms @16k16bit
BACKEND = "ws://localhost:8010"


async def stream(wav_path: Path) -> tuple[str, list[dict]]:
    transcripts: list[dict] = []
    async with websockets.connect(f"{BACKEND}/ws/meeting", max_queue=256) as ws:
        await ws.send(json.dumps({"type": "start", "title": "重跑测试"}))
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
                    if msg["type"] == "transcript":
                        transcripts.append(msg)
                        print(f"  [{msg.get('speaker','')}] {msg.get('text','')[:40]}")
            except websockets.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(recv_loop())
        with wave.open(str(wav_path), "rb") as w:
            assert w.getframerate() == 16000 and w.getsampwidth() == 2
            data = w.readframes(w.getnframes())
        for i in range(0, len(data), CHUNK):
            await ws.send(data[i:i + CHUNK])
            await asyncio.sleep(0.1)  # 实时节奏
        await ws.send(json.dumps({"type": "stop"}))
        await recv_task
    return meeting_id, transcripts


def split_by_speaker(meeting_id: str, out_dir: Path) -> None:
    import numpy as np
    import soundfile as sf

    db = Path(__file__).parent.parent / "server" / "meeting.db"
    con = sqlite3.connect(db)
    started = datetime.fromisoformat(
        con.execute("SELECT started_at FROM meetings WHERE id=?", (meeting_id,)).fetchone()[0]
    )
    uts = con.execute(
        "SELECT speaker, start_ms, end_ms, created_at FROM utterances WHERE meeting_id=? ORDER BY seq",
        (meeting_id,),
    ).fetchall()

    wav = Path(__file__).parent.parent / "server" / "recordings" / f"{meeting_id}.wav"
    audio, sr = sf.read(wav)
    if audio.ndim > 1:
        audio = audio[:, 0]
    total = len(audio) / sr

    gap = np.zeros(int(sr * 0.6), dtype=audio.dtype)
    groups: dict[str, list] = {}
    skipped = 0
    for spk, s0, s1, cat in uts:
        dur = (s1 - s0) / 1000
        t1 = (datetime.fromisoformat(cat) - started).total_seconds()
        t0 = max(0.0, t1 - dur)
        if t1 > total or dur < 0.2:
            skipped += 1
            continue
        groups.setdefault(spk or "未识别", []).append(audio[int(t0 * sr):int(t1 * sr)])

    out_dir.mkdir(parents=True, exist_ok=True)
    for k, clips in sorted(groups.items()):
        mixed: list = []
        for c in clips:
            mixed += [c, gap]
        p = out_dir / f"rerun_{k}_all.wav"
        sf.write(p, np.concatenate(mixed[:-1]), sr)
        print(f"{p.name}: {len(clips)}段 {sum(len(c) for c in clips)/sr:.1f}s")
    print(f"skipped: {skipped}")


def main() -> None:
    wav_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent.parent / "docs/eval/rerun"
    meeting_id, transcripts = asyncio.run(stream(wav_path))
    from collections import Counter
    print(f"\n=== {meeting_id}: {len(transcripts)} 句 ===")
    print(Counter(t.get("speaker", "") for t in transcripts))
    split_by_speaker(meeting_id, out_dir)


if __name__ == "__main__":
    main()
