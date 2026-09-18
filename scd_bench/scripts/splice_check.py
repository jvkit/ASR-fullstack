"""拼接件跨缝合并检查：对每个拼接缝（12/24/36/48/60s），看系统输出是否有段
直接横跨缝隙（起点<缝<终点），以及缝左右最近 GT 说话人被系统给了什么标签。

用法: python scripts/splice_check.py <sys.rttm> <gt.rttm> <K_sec> <n_pieces>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from der_eval import load_rttm  # noqa: E402


def main():
    sys_rttm, gt_rttm, k, n = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
    sys_segs = load_rttm(sys_rttm)
    gt_segs = load_rttm(gt_rttm)
    boundaries = [k * i for i in range(1, n)]

    merges = 0
    for b in boundaries:
        left_gt = [s for s in gt_segs if s[1] <= b]
        right_gt = [s for s in gt_segs if s[0] >= b]
        lref = max(left_gt, key=lambda s: s[1]) if left_gt else None
        rref = min(right_gt, key=lambda s: s[0]) if right_gt else None
        spanning = [s for s in sys_segs if s[0] < b < s[1]]
        near = [s for s in sys_segs if abs(s[0] - b) < 2 or abs(s[1] - b) < 2]
        if spanning:
            merges += 1
        print(f"--- 缝 {b:5.1f}s ---")
        print(f"  左侧GT尾: [{lref[0]:6.2f}-{lref[1]:6.2f}] {lref[2] if lref else '-'} | "
              f"右侧GT头: [{rref[0]:6.2f}-{rref[1]:6.2f}] {rref[2] if rref else '-'}")
        for s in spanning:
            print(f"  ★ 跨缝系统段: [{s[0]:6.2f}-{s[1]:6.2f}] 标签{s[2]}  (跨 {s[1]-s[0]:.1f}s)")
        for s in near:
            tag = "跨缝" if s[0] < b < s[1] else "邻缝"
            print(f"    {tag}段: [{s[0]:6.2f}-{s[1]:6.2f}] 标签{s[2]}")
    print(f"\n跨缝合并次数: {merges}/{len(boundaries)} 条缝")
    print(f"系统总段数 {len(sys_segs)}，标签数 {len({s[2] for s in sys_segs})}；"
          f"GT 说话人 {len({s[2] for s in gt_segs})} 人")


if __name__ == "__main__":
    main()
