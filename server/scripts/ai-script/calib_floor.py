"""聚类地板标定：同人下限 vs 跨人上限，按性别分组，分位数中点定阈值。

方法（贴合生产运行点）：
- 音频过本地 GTCRN 降噪 → GPU /vad(250ms) → 每段按 GT(TextGrid)重叠度打标签
- 同人样本 = 段 embedding vs 本说话人质心；跨人样本 = 段 vs 其他说话人质心
- 组：M-M（001-002）、F-F（6 对）、M-F（8 对）
- 阈值建议 = (同人 p5 + 跨人 p95) / 2，分位数抗离群；同时报 min/max 版供对照

用法:
  cd server && ../.venv/bin/python scripts/ai-script/calib_floor.py
"""
import re
import sys
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
GPU = "http://192.168.30.87:10096"
ROOT = Path(__file__).resolve().parents[3]  # meeting-asr/
AISHELL = ROOT / "eval/aishell4/test"
MEETINGS = ["M_R003S01C01", "M_R003S02C01", "M_R003S04C01"]


def find_wav(stem: str) -> Path:
    for d in (AISHELL, AISHELL / "wav"):
        for ext in (".wav", ".flac"):
            p = d / f"{stem}{ext}"
            if p.exists():
                return p
    raise FileNotFoundError(stem)


def load_audio(path: Path) -> np.ndarray:
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="int16")
    assert sr == 16000
    return np.asarray(data)


def gt_tiers(path: Path) -> dict:
    out = {}
    for b in re.split(r"item \[\d+]:", open(path, encoding="utf-8").read())[1:]:
        nm = re.search(r'name = "([^"]+)"', b)
        if not nm:
            continue
        rows = [(float(m.group(1)), float(m.group(2)))
                for m in re.finditer(
                    r'xmin = ([\d.]+)\s*\n\s*xmax = ([\d.]+)\s*\n\s*text = "([^"]*)"', b)
                if m.group(3).strip()]
        out[nm.group(1)] = rows
    return out


def post(endpoint, payload, **forms):
    r = requests.post(f"{GPU}{endpoint}", files={"file": ("a.wav", payload)},
                      data=forms, timeout=300)
    r.raise_for_status()
    return r.json()


def embed(audio: np.ndarray):
    if len(audio) < 3200:
        return None
    r = requests.post(f"{GPU}/embed", files={"file": ("s.pcm", audio.tobytes())}, timeout=120)
    r.raise_for_status()
    e = np.array(r.json()["embedding"], dtype=np.float32)
    return e / (np.linalg.norm(e) + 1e-9)


def gender(spk: str) -> str:
    return "M" if spk.endswith("-M") else "F"


def main():
    from app.core.preprocess import denoise_pcm

    seg_emb: dict[str, dict[str, list]] = {m: defaultdict(list) for m in MEETINGS}
    cache_dir = ROOT / "eval/calib_cache"
    cache_dir.mkdir(exist_ok=True)
    for m in MEETINGS:
        cache = cache_dir / f"{m}.pcm"
        if cache.exists():
            clean = np.frombuffer(cache.read_bytes(), dtype=np.int16)
            print(f"{m}: 复用降噪缓存", flush=True)
        else:
            raw = load_audio(find_wav(m))
            clean = np.frombuffer(denoise_pcm(raw.tobytes()), dtype=np.int16)
            cache.write_bytes(clean.tobytes())
            del raw
            print(f"{m}: 降噪完成并已缓存", flush=True)
        import io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(clean.tobytes())
        vad = post("/vad", buf.getvalue(), max_end_silence_ms="250")
        gt = gt_tiers(AISHELL / "TextGrid" / f"{m}.TextGrid")
        n_drop = 0
        done = 0
        for s, e in vad["sents"]:
            if e - s < 0.5:
                continue
            done += 1
            if done % 50 == 0:
                print(f"{m}: 嵌入 {done}/{len(vad['sents'])}", flush=True)
            best, ov = None, 0.0
            for spk, rows in gt.items():
                for gs, ge in rows:
                    o = max(0.0, min(e, ge) - max(s, gs))
                    if o > ov:
                        best, ov = spk, o
            if best is None or ov / (e - s) < 0.6:
                n_drop += 1
                continue
            x = embed(clean[int(s * 16000):int(e * 16000)])
            if x is not None:
                seg_emb[m][best].append(x)
        counts = {k: len(v) for k, v in seg_emb[m].items()}
        print(f"{m}: VAD段标签 {counts}，丢弃(重叠不足) {n_drop}", flush=True)

    # 汇总：同人/跨人样本（按性别对分组；质心用该会该说话人全部段）
    intra, cross = defaultdict(list), defaultdict(list)
    for m in MEETINGS:
        cents = {}
        for spk, embs in seg_emb[m].items():
            c = np.mean(embs, axis=0)
            cents[spk] = c / (np.linalg.norm(c) + 1e-9)
        for spk, embs in seg_emb[m].items():
            for x in embs:
                intra[gender(spk)].append(float(np.dot(x, cents[spk])))
                for ospk, c in cents.items():
                    if ospk == spk:
                        continue
                    cross[f"{gender(spk)}{gender(ospk)}"].append(float(np.dot(x, c)))

    def q(vals, p):
        return float(np.percentile(vals, p))

    print("\n== 同人（段 vs 自己质心） ==")
    for g in ("M", "F"):
        v = intra[g]
        print(f"  {g}: n={len(v)} p5={q(v,5):.3f} min={min(v):.3f} mean={np.mean(v):.3f}")
    print("\n== 跨人（段 vs 他人质心） ==")
    for g in ("MM", "FF", "MF"):
        v = cross[g]
        print(f"  {g}: n={len(v)} p95={q(v,95):.3f} max={max(v):.3f} mean={np.mean(v):.3f}")

    all_intra = intra["M"] + intra["F"]
    all_cross = cross["MM"] + cross["FF"] + cross["MF"]
    p5, p95 = q(all_intra, 5), q(all_cross, 95)
    mid = (p5 + p95) / 2
    print(f"\n== 阈值建议 ==")
    print(f"  同人 p5={p5:.3f}  跨人 p95={p95:.3f}  → 中点 = {mid:.3f}")
    mm_max = max(cross['MM']) if cross['MM'] else float('nan')
    print(f"  注意 M-M 跨人 max={mm_max:.3f}（男男样本少，只有 001-002 一对，重点盯）")
    print(f"  建议地板: {mid:.2f}（另报 0.45 / 0.50 / {mid:.2f} / 0.60 四档 DER 验证挑最优）")


if __name__ == "__main__":
    main()
