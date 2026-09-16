"""盲测聚类诊断：会议内同说话人「短段两两相似度」分布。

回答：匿名聚类地板 anon_threshold=0.45 对会议短段是否过高。
按 rttm 把每人语音切成段（对齐打散脚本的 0.8~8s），逐段算 ERes2NetV2
embedding，输出每人段间相似度的 p10/p25/中位数，以及"有多少比例段对 <0.45"
（低于地板=聚类必定拆散）。

用法(GPU):  cd ~/jvkit/asr && venv/bin/python ai-script/seg_sim_dist.py <wav> <rttm> [--max-sec 300]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_rttm(path: Path, max_sec: float | None) -> list[tuple[str, float, float]]:
    out = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            spk, s, d = p[7], float(p[3]), float(p[4])
            if max_sec and s >= max_sec:
                continue
            e = min(s + d, max_sec) if max_sec else s + d
            if e - s >= 0.8:
                out.append((spk, s, e))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("rttm")
    ap.add_argument("--max-seg", type=float, default=8.0)
    ap.add_argument("--max-sec", type=float, default=None)
    ap.add_argument("--floor", type=float, default=0.45)
    args = ap.parse_args()

    import soundfile as sf
    from funasr import AutoModel

    audio, sr = sf.read(args.wav, dtype="float32")
    ere = AutoModel(
        model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
        init_param="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt",
        device="cpu", disable_pbar=True, disable_log=True,
    )

    segs = load_rttm(Path(args.rttm), args.max_sec)
    by_spk: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for spk, s, e in sorted(segs, key=lambda x: x[1]):
        if by_spk[spk] and s - by_spk[spk][-1][1] < 0.5:
            by_spk[spk][-1] = (by_spk[spk][-1][0], e)
        else:
            by_spk[spk].append((s, e))

    print(f"说话人 {len(by_spk)} 个，按 {args.max_seg}s 切段：")
    all_below = all_n = 0
    for spk in sorted(by_spk):
        vecs = []
        for s, e in by_spk[spk]:
            t = s
            while t < e - 0.5:
                t1 = min(t + args.max_seg, e)
                clip = audio[int(t * sr):int(t1 * sr)]
                if len(clip) < sr * 0.8:
                    break
                r = float(np.sqrt(np.mean(clip ** 2)))
                if r > 1e-6:
                    clip = np.clip(clip * (0.1 / r), -1.0, 1.0)
                v = np.asarray(ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"],
                               dtype=np.float32).ravel()
                vecs.append(v / (np.linalg.norm(v) + 1e-9))
                t = t1
        if len(vecs) < 2:
            print(f"  {spk}: {len(vecs)} 段（不足两对）")
            continue
        pairs = [float(np.dot(a, b)) for i, a in enumerate(vecs) for b in vecs[i + 1:]]
        below = sum(1 for x in pairs if x < args.floor)
        all_below += below
        all_n += len(pairs)
        q = np.percentile(pairs, [10, 25, 50])
        print(f"  {spk}: {len(vecs)} 段 {len(pairs)} 对 | p10={q[0]:.3f} p25={q[1]:.3f} 中位={q[2]:.3f} | <{args.floor}: {below}/{len(pairs)} ({below/len(pairs)*100:.0f}%)")
    if all_n:
        print(f"\n总计：{all_below}/{all_n} 对 ({all_below/all_n*100:.0f}%) 低于地板 {args.floor}")


if __name__ == "__main__":
    main()
