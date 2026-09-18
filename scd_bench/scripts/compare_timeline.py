"""系统输出 RTTM vs 标准答案 RTTM 并排时间线对照（限定时间窗）。

用法: python scripts/compare_timeline.py <sys.rttm> <gt.rttm> <win_end_sec> [win_start_sec]
每行: 时间段 | 系统标签 | 该段内按重叠占比排序的 GT 说话人 | 边界差(系统起点-GT起点)
"""
import sys
from collections import defaultdict


def load_rttm(path, win_start, win_end):
    segs = []
    with open(path) as f:
        for line in f:
            p = line.split()
            if len(p) < 8 or p[0] != "SPEAKER":
                continue
            st, dur = float(p[3]), float(p[4])
            ed = st + dur
            spk = p[7]
            # 与窗口相交即纳入（段可超出窗口，便于看边界）
            if ed > win_start and st < win_end:
                segs.append((st, ed, spk))
    return sorted(segs)


def main():
    sys_rttm, gt_rttm = sys.argv[1], sys.argv[2]
    win_end = float(sys.argv[3])
    win_start = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    sys_segs = load_rttm(sys_rttm, win_start, win_end)
    gt_segs = load_rttm(gt_rttm, win_start, win_end)

    print(f"=== 窗口 [{win_start:.0f}, {win_end:.0f}]s ===")
    print(f"系统段数: {len(sys_segs)}  系统说话人数: {len({s[2] for s in sys_segs})}")
    print(f"GT 段数(窗口内): {len(gt_segs)}  GT 说话人数: {len({s[2] for s in gt_segs})}")
    print()
    header = f"{'start':>8} {'end':>8} {'dur':>6}  {'sys':>4}  GT重叠(说话人:占比/秒)"
    print(header)
    print("-" * len(header) + "-" * 30)

    for st, ed, spk in sys_segs:
        overlaps = defaultdict(float)
        for gst, ged, gspk in gt_segs:
            ov = min(ed, ged) - max(st, gst)
            if ov > 0:
                overlaps[gspk] += ov
        total = ed - st
        detail = ", ".join(
            f"{g}:{v/total:.0%}/{v:.2f}s" for g, v in sorted(overlaps.items(), key=lambda x: -x[1])
        )
        # 边界差：对最大重叠的 GT 段
        best = max(overlaps.items(), key=lambda x: x[1])[0] if overlaps else None
        bdiff = ""
        if best:
            bgst = min((g for g in gt_segs if g[2] == best and min(ed, g[1]) - max(st, g[0]) > 0),
                       key=lambda g: abs(g[0] - st), default=None)
            if bgst:
                bdiff = f"Δst={st - bgst[0]:+.2f}s Δed={ed - bgst[1]:+.2f}s vs {best}"
        print(f"{st:8.3f} {ed:8.3f} {total:6.3f}  {spk:>4}  {detail}   {bdiff}")

    print()
    print("=== GT 全段（窗口内，供对照） ===")
    for gst, ged, gspk in gt_segs:
        print(f"{gst:8.3f} {ged:8.3f} {ged-gst:6.3f}  GT   {gspk}")


if __name__ == "__main__":
    main()
