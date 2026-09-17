"""降噪对声纹分布的影响：同人内聚 vs 跨人分离，raw vs denoise 对照。

段 = GT interval；embedding 路径除降噪外完全一致（GPU /embed）。
"""
import re, sys, wave
from pathlib import Path
import numpy as np, requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
GPU = "http://192.168.30.87:10096"
GT = Path(__file__).resolve().parents[3] / "eval/aishell4/test/TextGrid/M_R003S01C01.TextGrid"

def load_wav(p):
    with wave.open(p) as w: return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

def tiers(path):
    out = {}
    for b in re.split(r"item \[\d+]:", open(path, encoding="utf-8").read())[1:]:
        nm = re.search(r'name = "([^"]+)"', b)
        if not nm: continue
        out[nm.group(1)] = [(float(m.group(1)), float(m.group(2)))
            for m in re.finditer(r'xmin = ([\d.]+)\s*\n\s*xmax = ([\d.]+)\s*\n\s*text = "([^"]*)"', b)
            if m.group(3).strip()]
    return out

def embed(a):
    a = np.asarray(a, dtype=np.int16)
    if len(a) < 3200: return None
    r = requests.post(f"{GPU}/embed", files={"file": ("s.pcm", a.tobytes())}, timeout=120)
    r.raise_for_status()
    e = np.array(r.json()["embedding"], dtype=np.float32)
    return e / (np.linalg.norm(e) + 1e-9)

def stats(audio, segs):
    embs = []
    for s, e in segs:
        x = embed(audio[min(int(s*16000), len(audio)-1):min(int(e*16000), len(audio))])
        if x is not None: embs.append(x)
    c = np.mean(embs, axis=0); c /= np.linalg.norm(c) + 1e-9
    intra = [float(np.dot(x, c)) for x in embs]
    return c, intra

raw = load_wav(sys.argv[1])
from app.core.preprocess import denoise_pcm
clean = np.frombuffer(denoise_pcm(raw.tobytes()), dtype=np.int16)
t = tiers(GT)
s001 = [r for r in t["001-M"] if r[1]-r[0] >= 2 and r[0] >= 14]
s006 = [r for r in t["006-F"] if r[1]-r[0] >= 2 and r[0] >= 168]

for label, audio in [("RAW 未降噪", raw), ("GTCRN 降噪", clean)]:
    c001, i001 = stats(audio, s001)
    c006, i006 = stats(audio, s006)
    cross = []
    for s, e in s006:
        x = embed(audio[min(int(s*16000), len(audio)-1):min(int(e*16000), len(audio))])
        if x is not None: cross.append(float(np.dot(x, c001)))
    print(f"\n== {label} ==")
    print(f"  001 同人: min={min(i001):.3f} mean={np.mean(i001):.3f}")
    print(f"  006 同人: min={min(i006):.3f} mean={np.mean(i006):.3f}")
    print(f"  006→001 跨人: max={max(cross):.3f} mean={np.mean(cross):.3f}")
    print(f"  → margin(同人min - 跨人max): {min(min(i001),min(i006)) - max(cross):.3f}")
