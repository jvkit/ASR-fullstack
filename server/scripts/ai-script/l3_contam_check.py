"""交替测试污染诊断：错配段的 embedding 到底指向谁。
对照组=配对段。判定：sim(真人) 明显高于 sim(被判入的人)=程序bug；模糊不清=模型极限。
用法(GPU): cd ~/jvkit/asr && venv/bin/python ai-script/l3_contam_check.py
"""
import json
import numpy as np
import soundfile as sf
from funasr import AutoModel

BASE = "/home/bo1/jvkit/asr/eval/alias_test"
audio, sr = sf.read(f"{BASE}/l3_alt_130s.wav", dtype="float32")
segs = json.load(open(f"{BASE}/l3_segs.json"))
ere = AutoModel(
    model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
    init_param="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt",
    device="cpu", disable_pbar=True, disable_log=True)
SPKS = ["001-M", "005-F", "002-M"]


def emb(s, e):
    clip = audio[int(s * sr):int(e * sr)]
    r = float(np.sqrt(np.mean(clip ** 2)))
    if r > 1e-6:
        clip = np.clip(clip * (0.1 / r), -1, 1)
    v = np.asarray(ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"],
                   dtype=np.float32).ravel()
    return v / (np.linalg.norm(v) + 1e-9)


# 真人质心：L会全场的干净长段（2~6s 切）
def centroid(gt_name):
    spans = []
    for line in open(f"{BASE}/l3_alt_130s.rttm"):
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER" and p[7] == gt_name:
            spans.append((float(p[3]), float(p[3]) + float(p[4])))
    vecs = []
    for s, e in spans:
        t = s
        while t < e - 2:
            t1 = min(t + 6, e)
            if t1 - t >= 2:
                vecs.append(emb(t, t1))
            t = t1
    c = np.mean(vecs, axis=0)
    return c / (np.linalg.norm(c) + 1e-9)


cen = {g: centroid(g) for g in SPKS}

print("RESULT_BEGIN")
n_clear, n_amb = 0, 0
for d0 in segs:
    if not d0["assigned"]:
        continue
    v = emb(d0["start"], d0["end"])
    sims = {g: float(np.dot(v, cen[g])) for g in SPKS}
    pred = max(sims, key=sims.get)
    if d0["wrong"]:
        ok = "指向真人" if pred == d0["gt"] and sims[d0["gt"]] - sims[d0["assigned"]] > 0.05 else "模糊"
        if ok == "指向真人":
            n_clear += 1
        else:
            n_amb += 1
        print("  错 %5.2f-%5.2f %-4s 真=%s 判=%s sims=%s -> %s" % (
            d0["start"], d0["end"], d0["dur"], d0["gt"], d0["assigned"],
            {k: round(x, 2) for k, x in sims.items()}, ok))
    else:
        if pred != d0["gt"]:
            print("  !!配对段也指错 %5.2f 真=%s sims=%s" % (d0["start"], d0["gt"], {k: round(x, 2) for k, x in sims.items()}))
print("错配段中: 向量明确指向真人(程序bug) %d, 模糊(模型极限) %d" % (n_clear, n_amb))
print("RESULT_END")
