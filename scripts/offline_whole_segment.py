"""OddASR 玩法对照：FunASR AutoModel 离线整段，一次调用出转写+分人。

与我们的实时管线对比：整段跑 vs 实时碎段，验证「整段分人更准」的调研结论。

用法：python scripts/offline_whole_segment.py <wav>  （在 gpu 上跑，模型已缓存）
"""
from __future__ import annotations

import sys
import time

from funasr import AutoModel

wav = sys.argv[1]
if len(sys.argv) > 2:
    preset = int(sys.argv[2])  # 可选 preset_spk_num（官方支持，指定人数）

t0 = time.time()
M = "/home/bo1/.cache/modelscope/models"
model = AutoModel(
    model=f"{M}/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master",
    vad_model=f"{M}/iic--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/master",
    punc_model=f"{M}/iic--punc_ct-transformer_zh-cn-common-vocab272727-pytorch/snapshots/master",
    spk_model="cam++",
    log_level="error",
    device="cuda:0",
    disable_update=True,
)
load_t = time.time() - t0
print(f"[model load {load_t:.1f}s]", flush=True)

t0 = time.time()
res = model.generate(input=wav, batch_size_s=300)
gen_t = time.time() - t0

if isinstance(res, list) and res and "sentence_info" in res[0]:
    sents = res[0]["sentence_info"]
    print(f"\n[generate {gen_t:.1f}s, {len(sents)} sentences]")
    for s in sents:
        print(f"  {s['spk']}: {s['text']}")
    spks = sorted(set(s["spk"] for s in sents))
    print(f"\n总说话人数: {len(spks)} ({spks})")
else:
    print("no sentence_info, raw:", str(res)[:300])
