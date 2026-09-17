"""开场白段落声纹相似度诊断（降噪前 vs 降噪后，生产同路径）。

背景：49385009f6fd（M会重放，西工大降噪后）开场 7 人轮流自我介绍被过度合并为
陌生人1。本脚本回答：降噪是否把跨说话人相似度推过了聚类地板(0.35)。

路径与生产一致：本地 denoise_pcm → GPU /vad(800ms) → GPU /embed。
同时算一遍未降噪 embedding 作对照（旧行为）。

用法:
  cd server && ../.venv/bin/python scripts/ai-script/diag_opening_sim.py \
      ../recordings/49385009f6fd.wav [--win-sec 20]

产出（打印）：段落表(t, dur, GT说话人)、降噪后/前 两两相似度矩阵、
同人/跨人均值与最大跨人相似度 vs 聚类参数。
"""
import argparse
import io
import sys
import wave
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # server/

GPU = "http://192.168.30.87:10096"
GT_TEXTGRID = Path(__file__).resolve().parents[3] / "eval/aishell4/test/TextGrid/M_R003S01C01.TextGrid"


def load_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def load_gt(path: Path, win: float) -> list[tuple[float, float, str]]:
    """TextGrid 逐段 -> (s,e,speaker)，截 win 秒内、有文本的。"""
    segs, xmin, xmax, text = [], None, None, None

    def flush():
        if xmin is not None and text and float(xmax or 0) <= win and text.strip():
            segs.append((float(xmin), float(xmax), text.strip()))
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if line.startswith("xmin"):
            xmin = line.split("=")[1].strip().strip('"')
        elif line.startswith("xmax"):
            xmax = line.split("=")[1].strip().strip('"')
        elif line.startswith("text"):
            text = line.split("=", 1)[1].strip().strip('"')
            flush()
    return segs


def gt_speaker(segs, s, e) -> str:
    best, ov = "?", 0.0
    for gs, ge, spk in segs:
        o = max(0.0, min(e, ge) - max(s, gs))
        if o > ov:
            best, ov = spk, o
    return best


def post_pcm(endpoint: str, pcm_bytes: bytes, wav_container: bool = False, **forms):
    payload = pcm_bytes
    if wav_container:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm_bytes)
        payload = buf.getvalue()
    for _ in range(3):
        r = requests.post(f"{GPU}{endpoint}", files={"file": ("seg.wav" if wav_container else "seg.pcm", payload)},
                          data=forms, timeout=120)
        if r.status_code == 200:
            return r.json()
        if r.status_code != 307:
            r.raise_for_status()
    r.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--win-sec", type=float, default=20.0)
    args = ap.parse_args()

    raw = load_wav(args.wav)
    from app.core.preprocess import denoise_pcm
    clean = np.frombuffer(denoise_pcm(raw.tobytes()), dtype=np.int16)
    print(f"wav {len(raw)/16000:.1f}s，降噪后 rms {np.sqrt((clean.astype(np.float32)**2).mean()):.0f} "
          f"(raw {np.sqrt((raw.astype(np.float32)**2).mean()):.0f})")

    vad = post_pcm("/vad", clean.tobytes(), wav_container=True, max_end_silence_ms="800")
    segs = [s for s in vad["sents"] if s[1] <= args.win_sec]
    print(f"VAD(800ms) 前 {args.win_sec}s 共 {len(segs)} 段")

    gt = load_gt(GT_TEXTGRID, args.win_sec + 2)
    embs = {"raw": [], "denoise": []}
    rows = []
    for i, (s, e) in enumerate(segs):
        a, b = int(s * 16000), int(e * 16000)
        er = np.array(post_pcm("/embed", raw[a:b].tobytes())["embedding"], dtype=np.float32)
        ed = np.array(post_pcm("/embed", clean[a:b].tobytes())["embedding"], dtype=np.float32)
        embs["raw"].append(er)
        embs["denoise"].append(ed)
        rows.append((s, e, gt_speaker(gt, s, e)))

    for k in rows:
        print(f"  seg {k[0]:5.2f}-{k[1]:5.2f} dur={k[1]-k[0]:4.2f}s GT={k[2]}")

    for mode, em in embs.items():
        print(f"\n== {mode} 相似度矩阵（行×列=上表段序） ==")
        n = len(em)
        mat = np.array([[float(np.dot(em[i], em[j])) for j in range(n)] for i in range(n)])
        for i in range(n):
            print("  " + " ".join(f"{mat[i,j]:.2f}" for j in range(n)))
        same, cross = [], []
        for i in range(n):
            for j in range(i + 1, n):
                (same if rows[i][2] == rows[j][2] and rows[i][2] != "?" else cross).append(mat[i, j])
        print(f"  同人均值={np.mean(same) if same else float('nan'):.3f} "
              f"跨人均值={np.mean(cross):.3f} 跨人max={np.max(cross):.3f} 跨人min={np.min(cross):.3f}")
        print("  （聚类地板 anon_cluster_threshold=0.35，簇内切簇落差 anon_split_gap=0.20）")


if __name__ == "__main__":
    main()
