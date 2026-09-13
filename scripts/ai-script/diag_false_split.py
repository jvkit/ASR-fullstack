"""诊断：纯 001-M 段(4.08-6.65)滑窗为何误切换成 003-F/002-M。
看每窗的 top1/margin——误切一定是低 margin 的窗在抖动。"""
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

print("=== 整句 4.08-6.65（真值：纯 001-M）合并打分 ===")
clip = a[int(4.08 * sr):int(6.65 * sr)]
e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
e = np.asarray(e, dtype=np.float32).ravel()
e /= np.linalg.norm(e) + 1e-9
sims = sorted(((n, float(np.dot(e, refs[n]))) for n in names), key=lambda kv: -kv[1])
print({n: round(s, 3) for n, s in sims}, "margin=%.3f" % (sims[0][1] - sims[1][1]))

print()
print("=== 0.6s/0.3s 滑窗 4.0-7.3 ===")
t = 4.0
while t + 0.6 < 7.4:
    clip = a[int(t * sr):int((t + 0.6) * sr)]
    e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
    e = np.asarray(e, dtype=np.float32).ravel()
    e /= np.linalg.norm(e) + 1e-9
    sims = sorted(((n, float(np.dot(e, refs[n]))) for n in names), key=lambda kv: -kv[1])
    flips = "" if sims[0][0] == "001-M" else "  <-- 误判"
    print("%.2f-%.2f top1=%s %.3f margin=%.3f%s" % (t, t + 0.6, sims[0][0], sims[0][1],
                                                    sims[0][1] - sims[1][1], flips))
    t += 0.3
