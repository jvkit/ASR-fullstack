#!/usr/bin/env python3
"""非重叠区认错：谁被认成谁 + 最长的认错段清单"""
from collections import defaultdict

GT = "aishell4/test/TextGrid/M_R003S01C01.rttm"
SYS = "batch_3ds_M_R003S01C01_argmax_v1/batch_3ds_M_R003S01C01.rttm"

def load(path):
    segs = []
    for line in open(path):
        p = line.split()
        if len(p) < 8 or p[0] != "SPEAKER":
            continue
        segs.append((float(p[3]), float(p[3]) + float(p[4]), p[7]))
    return segs

gt = load(GT)
syss = load(SYS)
names = {"001-M", "002-M", "003-F", "004-F", "005-F", "006-F"}

# 逐 sys 段统计：其时间范围内 GT 单人说话的部分，标签是否一致
pair_time = defaultdict(float)   # (gt, sys_label) -> 秒
wrong_segs = []                  # (dur_of_wrong_part, t_start, gt, sys_label, seg_dur)
for s, e, label in syss:
    if label not in names:
        continue
    # 该段与 GT 单人段的交集
    for gs, ge, gspk in gt:
        ov_s, ov_e = max(s, gs), min(e, ge)
        if ov_e - ov_s <= 0.5:  # 忽略零星交界
            continue
        # 排除 GT 重叠：该交集内是否还有第三人
        others = [(s2, e2) for s2, e2, sp2 in gt if sp2 != gspk and max(ov_s, s2) < min(ov_e, e2) - 0.2]
        if others:
            continue
        dur = ov_e - ov_s
        if label != gspk:
            pair_time[(gspk, label)] += dur
            wrong_segs.append((dur, ov_s, gspk, label, e - s))

print("=== 非重叠区 认错 (GT -> 系统标签) 按秒数排序 ===")
for (g, l), t in sorted(pair_time.items(), key=lambda x: -x[1]):
    print(f"  {g} 被认成 {l}: {t:6.1f}s")

print()
print("=== 最长的 15 个认错段 ===")
for dur, ts, g, l, segdur in sorted(wrong_segs, reverse=True)[:15]:
    print(f"  t={ts:7.1f}s  {g} -> {l}  认错时长 {dur:5.1f}s  (所在sys段长 {segdur:.1f}s)")

tot = sum(pair_time.values())
print(f"\n非重叠区认错合计: {tot:.1f}s")
