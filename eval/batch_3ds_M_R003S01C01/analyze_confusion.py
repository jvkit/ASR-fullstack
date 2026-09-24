"""分析批次3D+声纹测试的混淆来源：认人错 vs 切错（边界不准导致混合嵌入）。"""
import sys
from collections import defaultdict

SYS = "eval/batch_3ds_M_R003S01C01/batch_3ds_M_R003S01C01.rttm"
GT = "eval/aishell4/test/TextGrid/M_R003S01C01.rttm"


def load(path):
    segs = []
    for line in open(path):
        p = line.split()
        if len(p) < 8 or p[0] != "SPEAKER":
            continue
        segs.append({"spk": p[7], "start": float(p[3]), "dur": float(p[4]),
                     "end": float(p[3]) + float(p[4])})
    return segs


def overlap(a, b):
    s = max(a["start"], b["start"])
    e = min(a["end"], b["end"])
    return max(0.0, e - s)


sys_segs = load(SYS)
gt_segs = load(GT)

# ---- 1. 每条的纯度与归属 ----
rows = []
for s in sys_segs:
    ov = defaultdict(float)
    for g in gt_segs:
        o = overlap(s, g)
        if o > 0:
            ov[g["spk"]] += o
    if not ov:
        continue
    total = sum(ov.values())
    dom_spk = max(ov, key=ov.get)
    purity = ov[dom_spk] / total
    rows.append({
        "sys": s["spk"], "gt": dom_spk, "dur": s["dur"],
        "purity": purity, "speech_cov": total / s["dur"],
        "start": s["start"], "end": s["end"],
    })

# ---- 2. 混淆矩阵（按时长）----
conf = defaultdict(float)   # (sys, gt) -> 秒
for r in rows:
    if r["sys"] != r["gt"] and r["purity"] >= 0.5:
        conf[(r["sys"], r["gt"])] += r["dur"] * r["purity"]

print("=== 混淆矩阵 (sys行 -> gt列, 秒, 纯度>=0.5的段) ===")
pairs = sorted(conf.items(), key=lambda x: -x[1])
for (sys_s, gt_s), sec in pairs[:15]:
    print(f"  系统={sys_s:<8} 实际={gt_s:<8} {sec:7.1f}s")

# ---- 3. 错段分类：纯段(认人错) vs 跨界段(切错/混合嵌入) ----
mis_pure = defaultdict(float)    # 段几乎全在一个人手里，但认成了别人
mis_mixed = defaultdict(float)   # 段跨过GT边界，嵌入混合
n_pure = n_mixed = 0
short_mis = 0.0
for r in rows:
    if r["sys"] == r["gt"] or r["purity"] < 0.5:
        continue
    if r["purity"] >= 0.8:
        mis_pure[(r["sys"], r["gt"])] += r["dur"]
        n_pure += 1
        if r["dur"] < 2.0:
            short_mis += r["dur"]
    else:
        mis_mixed[(r["sys"], r["gt"])] += r["dur"]
        n_mixed += 1

tp = sum(mis_pure.values())
tm = sum(mis_mixed.values())
print(f"\n=== 错段归因 ===")
print(f"纯段认人错 (纯度>=0.8): {n_pure:4d} 段, {tp:7.1f}s")
print(f"跨界混合错 (0.5~0.8) : {n_mixed:4d} 段, {tm:7.1f}s")
print(f"其中 <2s 短段的认人错 : {short_mis:7.1f}s ({short_mis/max(tp,1e-9)*100:.0f}% of 纯段错)")

print(f"\n=== 纯段认人错明细 (top) ===")
for (sys_s, gt_s), sec in sorted(mis_pure.items(), key=lambda x: -x[1])[:10]:
    print(f"  认成={sys_s:<8} 实际={gt_s:<8} {sec:7.1f}s")

# ---- 4. 段长 vs 错误率 ----
buckets = [(0, 1.5), (1.5, 3), (3, 6), (6, 12), (12, 1e9)]
print(f"\n=== 段长分桶错误率 ===")
for lo, hi in buckets:
    sel = [r for r in rows if lo <= r["dur"] < hi]
    err = sum(r["dur"] * r["purity"] for r in sel if r["sys"] != r["gt"] and r["purity"] >= 0.5)
    tot = sum(r["dur"] * r["purity"] for r in sel)
    n = len(sel)
    if tot > 0:
        print(f"  [{lo:4.1f},{hi if hi<1e8 else '∞':>4})s  段数={n:4d}  语音={tot:7.1f}s  错={err:6.1f}s ({err/tot*100:5.1f}%)")

# ---- 5. 边界偏移：GT 换人点附近，系统边界离它多远 ----
gt_bounds = sorted({round(g["start"], 2) for g in gt_segs} | {round(g["end"], 2) for g in gt_segs})
sys_bounds = sorted({round(s["start"], 2) for s in sys_segs} | {round(s["end"], 2) for s in sys_segs})
shifts = []
for b in gt_bounds:
    near = min(sys_bounds, key=lambda x: abs(x - b))
    d = abs(near - b)
    if d <= 2.0:
        shifts.append(d)
import statistics
if shifts:
    print(f"\n=== 边界偏移 (仅统计±2s内有系统边界的GT边界, n={len(shifts)}) ===")
    print(f"  中位={statistics.median(shifts):.2f}s  均值={statistics.mean(shifts):.2f}s  >0.5s占比={sum(1 for x in shifts if x>0.5)/len(shifts)*100:.0f}%")

# ---- 6. 002-M 多出来的 57s 从哪来 ----
print(f"\n=== 002-M 误收来源 ===")
for (sys_s, gt_s), sec in sorted(mis_pure.items(), key=lambda x: -x[1]):
    if sys_s == "002-M":
        print(f"  实际={gt_s:<8} 被认成002-M: {sec:6.1f}s")
for (sys_s, gt_s), sec in sorted(mis_mixed.items(), key=lambda x: -x[1]):
    if sys_s == "002-M":
        print(f"  [混合] 实际={gt_s:<8} 被认成002-M: {sec:6.1f}s")
print("\n=== 001-M 被谁拿走 ===")
for (sys_s, gt_s), sec in sorted(mis_pure.items(), key=lambda x: -x[1]):
    if gt_s == "001-M":
        print(f"  001-M 被认成 {sys_s:<8}: {sec:6.1f}s")
for (sys_s, gt_s), sec in sorted(mis_mixed.items(), key=lambda x: -x[1]):
    if gt_s == "001-M":
        print(f"  [混合] 001-M 被认成 {sys_s:<8}: {sec:6.1f}s")
