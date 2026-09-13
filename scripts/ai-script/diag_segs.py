import sys
import numpy as np
import soundfile as sf
from funasr import AutoModel
from pathlib import Path

enroll_dir = sys.argv[1]
wav = sys.argv[2]
pairs = eval(sys.argv[3])  # [(a,b,truth),...]

ere = AutoModel(
    model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
    init_param="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt",
    device="cpu", disable_pbar=True, disable_log=True)
refs = {}
for p in Path(enroll_dir).glob("*.pcm"):
    pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
    e = ere.generate(input=pcm, fs=16000, extract_embedding=True)[0]["spk_embedding"]
    e = np.asarray(e, dtype=np.float32).ravel()
    refs[p.stem] = e / (np.linalg.norm(e) + 1e-9)
names = sorted(refs)
print("refs:", names, flush=True)

a, sr = sf.read(wav)
for a0, b0, truth in pairs:
    clip = a[int(a0 * sr):int(b0 * sr)]
    if len(clip) < sr * 0.25:
        print("%.2f-%.2f 太短(%.2fs) 真值=%s" % (a0, b0, len(clip) / sr, truth), flush=True)
        continue
    e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
    e = np.asarray(e, dtype=np.float32).ravel()
    e /= np.linalg.norm(e) + 1e-9
    sims = sorted(((n, float(np.dot(e, refs[n]))) for n in names), key=lambda kv: -kv[1])
    print("%.2f-%.2f(%.1fs) 真值=%s top=%s margin=%.3f" % (
        a0, b0, b0 - a0, truth,
        [(n, round(s, 3)) for n, s in sims[:3]], sims[0][1] - sims[1][1]), flush=True)
