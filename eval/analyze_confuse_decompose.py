#!/usr/bin/env python3
"""把 v1 argmax 的 confusion 错误按两个维度拆开：
1. 该 sys 段是否落在 GT 重叠说话区（>=2 人同时说话）——结构性上限
2. 非重叠区的认错，按段时长分桶——验证"短段向量噪声"假设
"""
import sys
from collections import defaultdict

GT = "aishell4/test/TextGrid/M_R003S01C01.rttm"
SYS = "batch_3ds_M_R003S01C01_argmax_v1/batch_3ds_M_R003S01C01.rttm"
COLLAR = 0.25
STEP = 0.05

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

gt_end = max(e for _, e, _ in gt)
t = 0.0
total_speech = 0.0      # 有 >=1 个 GT 说话的帧
overlap_time = 0.0      # >=2 个 GT 同时说的帧
confuse_total = 0.0     # 所有认错帧
confuse_overlap = 0.0   # 认错且处于 GT 重叠区
confuse_single = 0.0    # 认错且 GT 只有一人（这部分才是"真认错"）

# 分桶统计：非重叠区认错帧，按所在 sys 段时长
buckets = [("<1s", 1.0), ("1-2s", 2.0), ("2-4s", 4.0), (">4s", 1e9)]
bucket_conf = defaultdict(float)
bucket_all = defaultdict(float)   # 该桶内"被计分"的帧（GT 单人说话且标签正确的帧 + 认错的帧）

def seg_of(segs, tt):
    for s, e, spk in segs:
        if s <= tt < e:
            return s, e, spk
    return None

while t < gt_end:
    tt = t + STEP / 2
    gt_active = [spk for s, e, spk in gt if s <= tt < e]
    if not gt_active:
        t += STEP
        continue
    total_speech += STEP
    if len(gt_active) >= 2:
        overlap_time += STEP
    # sys 标签（带 collar：跳过 sys 段两端各 0.25s，贴近官方评分）
    hit = seg_of(syss, tt)
    if hit is None:
        t += STEP
        continue
    s, e, label = hit
    if tt - s < COLLAR or e - tt < COLLAR:
        t += STEP
        continue
    if len(gt_active) >= 2:
        # 单标签系统在这种帧上最多只能对一个，结构性不认
        confuse_total += STEP
        confuse_overlap += STEP
        t += STEP
        continue
    gspk = gt_active[0]
    dur = e - s
    bname = next(n for n, lim in buckets if dur < lim)
    bucket_all[bname] += STEP
    if label != gspk:
        confuse_total += STEP
        confuse_single += STEP
        bucket_conf[bname] += STEP
    t += STEP

print(f"GT 总说话时间: {total_speech:.1f}s")
print(f"GT 重叠说话时间: {overlap_time:.1f}s ({overlap_time/total_speech*100:.2f}% of speech)")
print()
print(f"confusion 总时间: {confuse_total:.1f}s ({confuse_total/total_speech*100:.2f}% of speech)")
print(f"  其中 GT 重叠区(结构性): {confuse_overlap:.1f}s ({confuse_overlap/total_speech*100:.2f}%)")
print(f"  其中单人区(真认错):    {confuse_single:.1f}s ({confuse_single/total_speech*100:.2f}%)")
print()
print("单人区认错按 sys 段时长分桶:")
for n, _ in buckets:
    a, c = bucket_all[n], bucket_conf[n]
    if a > 0:
        print(f"  {n:6s}: 认错 {c:6.1f}s / 计分 {a:7.1f}s  ({c/a*100:5.2f}% of bucket)")
