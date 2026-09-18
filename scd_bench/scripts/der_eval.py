"""DER 评测：系统输出（rttm/json） vs GT rttm。自包含，不依赖 server/ 代码。

口径（与 server/scripts/replay_der.py 同思路：最优映射剥离命名差异，只度量分人质量）：
- 帧采样 100ms；GT 允许重叠——参考单位为 (帧, GT说话人) 对，Nref = 参考单位总数。
- 说话人标签最优映射：系统标签 × GT说话人 的"双方均活跃"帧共现计数矩阵上，
  用 scipy 匈牙利算法（linear_sum_assignment）求最大权一对一匹配；
  未匹配上的系统标签保持未映射（其活跃帧全部计 FA/confusion）。
- collar 模式（--collar，秒）：GT 段起点后 collar 与终点前 collar 内的帧，
  对该 GT 说话人剔除（不计 Nref、不计 miss/confusion）；落在被剔除帧上的系统
  活跃也不计 FA——即被 collar 剔除的帧整体跳过（md-eval 标准做法，参考端挂 collar）。
- 误差（均只在 collar 外的帧上统计）：
    miss      = GT 说话人活跃且系统该帧无任何活跃标签的参考单位数
    confusion = GT 说话人活跃、系统有活跃标签但映射后 ≠ 该 GT 说话人的参考单位数
    FA        = 系统有活跃标签、该帧无任何 GT 说话人活跃的帧数
  DER = (miss + confusion + FA) / Nref

用法: python scripts/der_eval.py <sys.rttm|sys.json> <gt.rttm> [--sys-format rttm|json]
          [--collar 0.25] [--json-out result.json]
"""
import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

FRAME = 0.1  # 100ms 帧采样


def load_rttm(path):
    segs = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            st, dur = float(p[3]), float(p[4])
            segs.append((st, st + dur, p[7]))
    return segs


def load_json(path):
    """3D-Speaker --out_type json: {id: {start, stop, speaker}}"""
    segs = []
    for v in json.loads(Path(path).read_text()).values():
        segs.append((float(v["start"]), float(v["stop"]), str(v["speaker"])))
    return segs


def build_frames(segs, n):
    """每帧活跃标签集合（系统输出通常单标签；GT 可重叠）。"""
    frames = [set() for _ in range(n)]
    for st, ed, spk in segs:
        for i in range(max(0, int(st / FRAME)), min(n, int(np.ceil(ed / FRAME)))):
            frames[i].add(spk)
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sys_path")
    ap.add_argument("gt_rttm")
    ap.add_argument("--sys-format", choices=["rttm", "json"], default=None)
    ap.add_argument("--collar", type=float, default=0.25)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    fmt = args.sys_format
    if fmt is None:
        fmt = "json" if args.sys_path.endswith(".json") else "rttm"
    sys_segs = load_json(args.sys_path) if fmt == "json" else load_rttm(args.sys_path)
    gt_segs = load_rttm(args.gt_rttm)
    collar = args.collar

    t_max = max([e for _, e, _ in sys_segs] + [e for _, e, _ in gt_segs] + [0.0])
    n = int(np.ceil(t_max / FRAME))
    gt_frames = build_frames(gt_segs, n)
    sys_frames = build_frames(sys_segs, n)

    # 有效参考单位：(帧, gt_spk)，剔除 collar 区
    ref_units = set()
    for st, ed, spk in gt_segs:
        i0, i1 = max(0, int(np.ceil(st / FRAME))), min(n, int(np.ceil(ed / FRAME)))
        c0, c1 = int(np.ceil((st + collar) / FRAME)), int(np.floor((ed - collar) / FRAME))
        for i in range(i0, i1):
            if collar > 0 and not (c0 <= i < c1):
                continue  # 该 GT 段的 collar 区帧
            ref_units.add((i, spk))

    gt_spks = sorted({s for _, s in ref_units})
    sys_labels = sorted({s for fr in sys_frames for s in fr})

    # 混淆计数：只在有效参考单位上、系统也有活跃标签时
    conf = Counter()
    skip_frames = set()  # 被 collar 完全剔除的帧（无任何有效参考单位的帧跳过 FA）
    for i in range(n):
        if not any((i, g) in ref_units for g in gt_spks):
            skip_frames.add(i)
    for i, g in ref_units:
        if sys_frames[i]:
            for p in sys_frames[i]:
                conf[(p, g)] += 1

    from scipy.optimize import linear_sum_assignment
    w = np.zeros((len(sys_labels), len(gt_spks)))
    for i, p in enumerate(sys_labels):
        for j, g in enumerate(gt_spks):
            w[i, j] = conf.get((p, g), 0)
    if len(sys_labels) and len(gt_spks):
        ri, cj = linear_sum_assignment(-w)
        mapping = {sys_labels[i]: gt_spks[j] for i, j in zip(ri, cj) if w[i, j] > 0}
    else:
        mapping = {}

    miss = conf_m = fa = 0
    for i, g in ref_units:
        if not sys_frames[i]:
            miss += 1
        elif not any(mapping.get(p) == g for p in sys_frames[i]):
            conf_m += 1
    for i in range(n):
        if i in skip_frames:
            continue
        if sys_frames[i] and not gt_frames[i]:
            fa += 1

    nref = len(ref_units)
    der = (miss + conf_m + fa) / max(1, nref)
    res = {
        "sys": args.sys_path, "gt": args.gt_rttm, "collar": collar,
        "DER": round(der * 100, 2), "miss": round(miss / max(1, nref) * 100, 2),
        "FA": round(fa / max(1, nref) * 100, 2),
        "confusion": round(conf_m / max(1, nref) * 100, 2),
        "nref_frames": nref,
        "sys_segments": len(sys_segs), "sys_labels": sys_labels,
        "gt_speakers": gt_spks, "mapping": mapping,
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"-> {args.json_out}")


if __name__ == "__main__":
    main()
