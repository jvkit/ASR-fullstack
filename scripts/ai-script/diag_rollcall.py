"""诊断报名字段（6.88-14.45s，五人极速轮换）：滑窗看声纹能否分开。"""
import numpy as np
import soundfile as sf
from funasr import AutoModel
from pathlib import Path

ere = AutoModel(
    model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
    init_param="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt",
    device="cpu", disable_pbar=True, disable_log=True,
)
refs = {}
for p in Path("/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/speaker_enrolls").glob("0*.pcm"):
    pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
    e = ere.generate(input=pcm, fs=16000, extract_embedding=True)[0]["spk_embedding"]
    e = np.asarray(e, dtype=np.float32).ravel()
    refs[p.stem] = e / (np.linalg.norm(e) + 1e-9)
names = sorted(refs)

a, sr = sf.read("/home/bo1/jvkit/asr/eval/audio/aishell4_5min.wav")
if a.ndim > 1:
    a = a[:, 0]

# 标注：6.88-8.58 002-M / 8.63-9.95 003-F / 10.05-11.48 004-F / 11.52-12.80 005-F / 12.82-14.45 006-F
truth = [(6.88, 8.58, "002-M"), (8.63, 9.95, "003-F"), (10.05, 11.48, "004-F"),
         (11.52, 12.80, "005-F"), (12.82, 14.45, "006-F")]

print("=== 整段 6.88-14.45 一次打分（模拟 VAD 合并段）===")
clip = a[int(6.88 * sr):int(14.45 * sr)]
e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
e = np.asarray(e, dtype=np.float32).ravel()
e /= np.linalg.norm(e) + 1e-9
sims = {n: round(float(np.dot(e, refs[n])), 3) for n in names}
print("merged:", sims)

print()
print("=== 0.6s 窗 / 0.3s 步长 滑窗 ===")
win, shift = 0.6, 0.3
t = 6.6
while t + win < 14.6:
    clip = a[int(t * sr):int((t + win) * sr)]
    e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
    e = np.asarray(e, dtype=np.float32).ravel()
    e /= np.linalg.norm(e) + 1e-9
    sims = {n: float(np.dot(e, refs[n])) for n in names}
    best = max(sims, key=sims.get)
    ranked = sorted(sims.items(), key=lambda kv: -kv[1])
    # 该窗真实说话人
    true = [x[2] for x in truth if x[0] < t + win and x[1] > t]
    mark = "OK " if best in true else "?? "
    print("%s %.2f-%.2f best=%s %.3f margin=%.3f true=%s" % (
        mark, t, t + win, best, sims[best],
        ranked[0][1] - ranked[1][1], "/".join(true)))
    t += shift
