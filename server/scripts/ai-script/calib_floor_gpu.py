"""聚类地板标定（GPU 版，减负用户机）：同人 p5 vs 跨人 p95，分位数中点。
路径与生产一致：GTCRN 降噪(本地副本) -> /vad(250ms) -> /embed，GT 标签来自 TextGrid。
每场会只取前 300s。"""
import re, sys, io, wave
from collections import defaultdict
from pathlib import Path
import numpy as np
import requests

SRC = Path.home() / "jvkit/asr/eval/calib_src"
CACHE = Path.home() / "jvkit/asr/eval/calib_cache"
CACHE.mkdir(exist_ok=True)
GPU = "http://127.0.0.1:10096"
MEETINGS = ["M_R003S01C01", "S_R003S01C01", "L_R004S01C01"]
MAX_SEC = 300

sys.path.insert(0, str(Path.home() / "jvkit/asr/ai-script"))
from gtcrn_net import GTCRN
import torch
_model = None


def denoise(pcm: np.ndarray) -> np.ndarray:
    global _model
    if _model is None:
        _model = GTCRN().eval()
        ckpt = torch.load(SRC / "gtcrn_dns3.tar", map_location=torch.device("cpu"))
        _model.load_state_dict(ckpt["model"])
    wav = pcm.astype(np.float32) / 32768.0
    win = torch.hann_window(512).pow(0.5)
    inp = torch.stft(torch.from_numpy(wav), 512, 256, 512, win, return_complex=False)
    with torch.no_grad():
        out = _model(inp[None])[0]
    enh = torch.istft(torch.view_as_complex(out.contiguous()), 512, 256, 512, win, return_complex=False)
    arr = enh.numpy()
    if len(arr) < len(wav):
        arr = np.concatenate([arr, np.zeros(len(wav) - len(arr), np.float32)])
    return np.clip(arr[:len(wav)] * 32768, -32768, 32767).astype(np.int16)


def gt_tiers(path):
    out = {}
    for b in re.split(r"item \[\d+]:", open(path, encoding="utf-8").read())[1:]:
        nm = re.search(r'name = "([^"]+)"', b)
        if not nm:
            continue
        out[nm.group(1)] = [(float(m.group(1)), float(m.group(2)))
                            for m in re.finditer(
                                r'xmin = ([\d.]+)\s*\n\s*xmax = ([\d.]+)\s*\n\s*text = "([^"]*)"', b)
                            if m.group(3).strip()]
    return out


def vad(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    r = requests.post(f"{GPU}/vad", files={"file": ("a.wav", buf.getvalue())},
                      data={"max_end_silence_ms": "250"}, timeout=300)
    r.raise_for_status()
    return r.json()["sents"]


def embed(a):
    if len(a) < 3200:
        return None
    r = requests.post(f"{GPU}/embed", files={"file": ("s.pcm", a.tobytes())}, timeout=120)
    r.raise_for_status()
    e = np.array(r.json()["embedding"], dtype=np.float32)
    return e / (np.linalg.norm(e) + 1e-9)


def g(spk):
    return "M" if spk.endswith("-M") else "F"


seg_emb = {m: defaultdict(list) for m in MEETINGS}
for m in MEETINGS:
    cache = CACHE / f"{m}.pcm"
    if cache.exists():
        clean = np.frombuffer(cache.read_bytes(), dtype=np.int16)
        print(f"{m}: 复用缓存", flush=True)
    else:
        import soundfile as sf
        raw, sr = sf.read(str(SRC / f"{m}.wav"), dtype="int16")
        assert sr == 16000, sr
        if raw.ndim > 1:
            raw = raw[:, 0]
        raw = np.asarray(raw)[:MAX_SEC * 16000]
        clean = denoise(raw)
        cache.write_bytes(clean.tobytes())
        print(f"{m}: 降噪完成(前{MAX_SEC}s)", flush=True)
    segs = vad(clean)
    gt = gt_tiers(SRC / f"{m}.TextGrid")
    done = 0
    for s, e in segs:
        if e - s < 0.5:
            continue
        best, ov = None, 0.0
        for spk, rows in gt.items():
            for gs, ge in rows:
                o = max(0.0, min(e, ge) - max(s, gs))
                if o > ov:
                    best, ov = spk, o
        if best is None or ov / (e - s) < 0.6:
            continue
        x = embed(clean[int(s * 16000):int(e * 16000)])
        if x is not None:
            seg_emb[m][best].append(x)
        done += 1
        if done % 100 == 0:
            print(f"{m}: 嵌入 {done}/{len(segs)}", flush=True)
    counts = ", ".join(f"{k}:{len(v)}" for k, v in sorted(seg_emb[m].items()))
    print(f"{m}: 标签计数 {{{counts}}}", flush=True)

intra, cross = defaultdict(list), defaultdict(list)
for m in MEETINGS:
    cents = {}
    for spk, embs in seg_emb[m].items():
        c = np.mean(embs, axis=0)
        cents[spk] = c / (np.linalg.norm(c) + 1e-9)
    for spk, embs in seg_emb[m].items():
        for x in embs:
            intra[g(spk)].append(float(np.dot(x, cents[spk])))
            for ospk, c in cents.items():
                if ospk != spk:
                    cross[f"{g(spk)}{g(ospk)}"].append(float(np.dot(x, c)))

q = lambda v, p: float(np.percentile(v, p))
print("\n== 同人（段vs自己质心） ==")
for gg in ("M", "F"):
    v = intra[gg]
    print(f"  {gg}: n={len(v)} p5={q(v,5):.3f} min={min(v):.3f} mean={np.mean(v):.3f}")
print("== 跨人（段vs他人质心） ==")
for gg in ("MM", "FF", "MF"):
    v = cross[gg]
    print(f"  {gg}: n={len(v)} p95={q(v,95):.3f} max={max(v):.3f} mean={np.mean(v):.3f}")
ai = intra["M"] + intra["F"]
ac = cross["MM"] + cross["FF"] + cross["MF"]
p5, p95 = q(ai, 5), q(ac, 95)
print(f"\n== 阈值建议: 同人p5={p5:.3f} 跨人p95={p95:.3f} -> 中点={(p5+p95)/2:.3f} ==")
print(f"  M-M 跨人 max={max(cross['MM']):.3f}（男男仅001-002一对，样本少重点盯）")
print(f"  地板候选: 0.45 / 0.50 / {(p5+p95)/2:.2f} / 0.60，DER 验证挑最优")
