"""换芯一期 B 验证脚本：本地新流程（denoise → diarize3ds → /identify_segments）实战。

不入 git 仓库约定目录之外的产物；输出 eval/verify_3ds_b/。命令：
    cd server && ../.venv/bin/python scripts/verify_3ds_flow.py <wav> [--full]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_hotwords
from app.core.diarize3ds import diarize
from app.core.gpu_pipeline import identify_segments
from app.core.preprocess import denoise_pcm


def load_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, \
            f"需 16k/mono/int16: {path}"
        return w.readframes(w.getnframes())


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--full", action="store_true", help="也走 reannotate 完整入口（落库）")
    args = ap.parse_args()

    t0 = time.time()
    pcm = load_pcm(args.wav)
    print(f"[1] pcm {len(pcm)/32000:.1f}s")

    pcm = denoise_pcm(pcm)
    print(f"[2] denoise done ({time.time()-t0:.1f}s)")

    segments = diarize(pcm, sr=16000)
    n_clusters = len({s["cluster_id"] for s in segments})
    print(f"[3] 3ds diarize: {len(segments)} segs / {n_clusters} clusters ({time.time()-t0:.1f}s)")
    for s in segments[:12]:
        print(f"    {s['start']:7.2f}-{s['end']:7.2f} c{s['cluster_id']}")

    hotwords = load_hotwords().replace(",", " ")
    segs = await identify_segments(pcm, segments, hotwords)
    if segs is None:
        print("[4] identify_segments FAILED")
        return
    print(f"[4] gpu identify: {len(segs)} segs ({time.time()-t0:.1f}s)")
    for s in segs[:20]:
        print(f"    {s['start']:7.2f}-{s['end']:7.2f} c{s['cluster_id']} {s['speaker']}: {s['text'][:60]}")
    speakers = sorted({s["speaker"] for s in segs})
    texts = sum(1 for s in segs if s.get("text"))
    print(f"    speakers={speakers}")
    print(f"    segs_with_text={texts}/{len(segs)}")
    assert len(segs) >= 2, "期望至少多段"
    assert speakers, "期望有 speaker 标签"
    print("[OK] 新流程验证通过")

    if args.full:
        from app.core.reannotate import reannotate_for_meeting
        ok = await reannotate_for_meeting("verify3dsb")
        print(f"[5] reannotate_for_meeting ok={ok}")


if __name__ == "__main__":
    asyncio.run(main())
