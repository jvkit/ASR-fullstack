"""双主角分离诊断：001 vs 006 各 GT 段的 embedding 与 陌生人1质心相似度。

背景：bb3cd75f14e8（VAD800）全场只出陌生人1，006 的 124s 独白全被吸进陌生人1。
本脚本回答：006 的段对 001 质心相似度是否 ≥ 地板 0.35（是→阈值/贪婪吸收问题；
否→聚类代码 bug）。

段 = TextGrid GT interval（最理想切分，排除 VAD 因素）。
路径与生产一致：本地 GTCRN 降噪 → GPU /embed。

用法:
  cd server && ../.venv/bin/python scripts/ai-script/diag_two_speaker.py ../recordings/49385009f6fd.wav
"""
import re
import sys
import wave
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

GPU = "http://192.168.30.87:10096"
GT_TEXTGRID = Path(__file__).resolve().parents[3] / "eval/aishell4/test/TextGrid/M_R003S01C01.TextGrid"


def load_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def gt_tier_intervals(path):
    """TextGrid 分 tier 解析 -> {speaker: [(s,e,text)]}"""
    txt = open(path, encoding="utf-8").read()
    out = {}
    for b in re.split(r"item \[\d+\]:", txt)[1:]:
        nm = re.search(r'name = "([^"]+)"', b)
        if not nm:
            continue
        spk = nm.group(1)
        rows = [(float(m.group(1)), float(m.group(2)), m.group(3).strip())
                for m in re.finditer(
                    r'xmin = ([\d.]+)\s*\n\s*xmax = ([\d.]+)\s*\n\s*text = "([^"]*)"', b)
                if m.group(3).strip()]
        out[spk] = rows
    return out


def embed(pcm_i16: np.ndarray):
    if len(pcm_i16) < 3200:  # <0.2s，模型必然炸
        return None
    r = requests.post(f"{GPU}/embed",
                      files={"file": ("seg.pcm", pcm_i16.tobytes())}, timeout=120)
    r.raise_for_status()
    e = np.array(r.json()["embedding"], dtype=np.float32)
    return e / (np.linalg.norm(e) + 1e-9)


def embed_range(clean, s, e):
    a, b = min(int(s * 16000), len(clean) - 1), min(int(e * 16000), len(clean))
    return embed(clean[a:b])


def main():
    wav_path = sys.argv[1]
    raw = load_wav(wav_path)
    from app.core.preprocess import denoise_pcm
    clean = np.frombuffer(denoise_pcm(raw.tobytes()), dtype=np.int16)

    tiers = gt_tier_intervals(GT_TEXTGRID)
    s001 = [r for r in tiers.get("001-M", []) if r[1] - r[0] >= 2 and r[0] >= 14]
    s006 = [r for r in tiers.get("006-F", []) if r[1] - r[0] >= 2 and r[0] >= 168]

    e001 = [x for x in (embed_range(clean, s, e) for s, e, _ in s001) if x is not None]
    c001 = np.mean(e001, axis=0)
    c001 /= np.linalg.norm(c001) + 1e-9
    e006 = [x for x in (embed_range(clean, s, e) for s, e, _ in s006) if x is not None]
    c006 = np.mean(e006, axis=0)
    c006 /= np.linalg.norm(c006) + 1e-9

    print(f"001 独白基准段 {len(e001)} 个, 006 独白段 {len(e006)} 个")
    sims_to_001 = [float(np.dot(e, c001)) for e in e006]
    sims_to_006 = [float(np.dot(e, c006)) for e in e006]
    print("\n006 各段相似度（地板 anon_cluster_threshold=0.35）：")
    for (s, e, t), a, b in zip(s006, sims_to_001, sims_to_006):
        flag = "→会被陌生人1吸收!" if a >= 0.35 else ""
        print(f"  {s:6.1f}-{e:6.1f} vs001={a:.3f} vs006质心={b:.3f}  {t[:10]}  {flag}")
    print(f"\n006→001 质心相似度: min={min(sims_to_001):.3f} mean={np.mean(sims_to_001):.3f} max={max(sims_to_001):.3f}")
    print(f"006→006 质心相似度: min={min(sims_to_006):.3f} mean={np.mean(sims_to_006):.3f}")
    print(f"两质心互似: {float(np.dot(c001, c006)):.3f}")


if __name__ == "__main__":
    main()
