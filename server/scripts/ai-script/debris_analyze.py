"""碎段归因：会议里碎段簇的段 vs 真实主人质心 的相似度清单。
用法(GPU): cd ~/jvkit/asr && venv/bin/python ai-script/debris_analyze.py
"""
import json
import numpy as np
import soundfile as sf
from funasr import AutoModel

BASE = "/home/bo1/jvkit/asr/eval/alias_test"
audio, sr = sf.read(f"{BASE}/bf11b9e31e4a.wav", dtype="float32")
segs = json.load(open(f"{BASE}/debris_segs.json"))
ere = AutoModel(
    model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
    init_param="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt",
    device="cpu", disable_pbar=True, disable_log=True)


def emb(s, e):
    clip = audio[int(s * sr):int(e * sr)]
    r = float(np.sqrt(np.mean(clip ** 2)))
    if r > 1e-6:
        clip = np.clip(clip * (0.1 / r), -1, 1)
    v = np.asarray(ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"],
                   dtype=np.float32).ravel()
    return v / (np.linalg.norm(v) + 1e-9), len(clip) / sr


def owner_centroid(gt_name):
    spans = []
    for line in open(f"{BASE}/M_R003S01C01.rttm"):
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER" and p[7] == gt_name:
            s, d = float(p[3]), float(p[4])
            if s < 300:
                spans.append((s, min(s + d, 300.0)))
    vecs = []
    for s, e in spans:
        t = s
        while t < e - 0.8:
            t1 = min(t + 4, e)
            if t1 - t >= 0.8:
                vv, _ = emb(t, t1)
                vecs.append(vv)
            t = t1
    c = np.mean(vecs, axis=0)
    return c / (np.linalg.norm(c) + 1e-9)


ALL = ["001-M", "002-M", "003-F", "004-F", "005-F", "006-F"]
cen = {g: owner_centroid(g) for g in ALL}

print("RESULT_BEGIN")
for d0 in segs:
    if not d0["gt"]:
        continue
    v, dur = emb(d0["start"], d0["end"])
    own = float(np.dot(v, cen[d0["gt"]]))
    best_other = max(ALL, key=lambda k: float(np.dot(v, cen[k])) if k != d0["gt"] else -9)
    bo = float(np.dot(v, cen[best_other]))
    flag = "PASS" if own >= 0.35 else "BLOCKED"
    print("  %6.2f-%6.2f %5.2fs %-7s gt=%s own=%.3f %s | 最像别人:%s=%.3f" % (
        d0["start"], d0["end"], dur, d0["text"], d0["gt"], own, flag, best_other, bo))
print("RESULT_END")
