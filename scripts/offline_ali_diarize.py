"""阿里 campplus speaker-diarization pipeline 整段对照（gpu 上已有，无需联网）。

与 OddASR 的 spk_model 不同，阿里 pipeline 是独立的 diarization 模型，
我们 diarize_svc 服务就在用。这里直接整段跑同一段双人录音，输出分段。

用法：python scripts/offline_ali_diarize.py <wav> [oracle_num]
"""
from __future__ import annotations

import sys

import soundfile as sf
from modelscope.pipelines import pipeline

wav = sys.argv[1]
oracle = int(sys.argv[2]) if len(sys.argv) > 2 else None

sd = pipeline(task="speaker-diarization",
              model="/home/bo1/.cache/modelscope/models/iic--speech_campplus_speaker-diarization_common/snapshots/master")
kw = {"oracle_num": oracle} if oracle else {}
segs = sd(wav, **kw)["text"]
print(f"[{len(segs)} segs, oracle={oracle}]")
for a, b, s in segs:
    print(f"  {a:6.2f}-{b:6.2f}s  spk{s}")
spks = sorted(set(s for _, _, s in segs))
print(f"\n说话人数: {len(spks)} ({spks})")
