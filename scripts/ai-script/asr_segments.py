"""GPU 上跑：对子段逐个做离线 ASR（paraformer），产出真实文字。

用法: venv/bin/python eval/asr_segments.py <wav> <segs.json> <out_texts.json>
segs.json: [{"speaker","start_ms","end_ms"},...] (chunk_eval 产出)
输出: ["段0文字", "段1文字", ...] 与 segs 等长
"""
import json
import sys

import numpy as np
import soundfile as sf
from funasr import AutoModel

wav_path, segs_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

asr = AutoModel(
    model="/home/bo1/.cache/modelscope/models/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master",
    device="cpu", disable_pbar=True, disable_log=True,
)
audio, sr = sf.read(wav_path)
if audio.ndim > 1:
    audio = audio[:, 0]
segs = json.load(open(segs_path))
texts = []
for s in segs:
    a, b = int(s["start_ms"] / 1000 * sr), int(s["end_ms"] / 1000 * sr)
    clip = audio[a:b]
    if len(clip) < sr * 0.1:
        texts.append("")
        continue
    r = asr.generate(input=clip, fs=sr)
    txt = r[0]["text"] if isinstance(r, list) else r["text"]
    texts.append(txt.strip())
    print("%d-%ds %s" % (s["start_ms"] // 1000, s["end_ms"] // 1000, txt.strip()[:30]), flush=True)
json.dump(texts, open(out_path, "w"), ensure_ascii=False)
print("done ->", out_path, flush=True)
