"""边界漂移方向分析：晚切还是早切？有多少是 30s 批次边界的人为切分？"""
import statistics

SYS = "eval/batch_3ds_M_R003S01C01/batch_3ds_M_R003S01C01.rttm"
GT = "eval/aishell4/test/TextGrid/M_R003S01C01.rttm"


def load(path):
    segs = []
    for line in open(path):
        p = line.split()
        if len(p) < 8 or p[0] != "SPEAKER":
            continue
        segs.append({"spk": p[7], "start": float(p[3]), "end": float(p[3]) + float(p[4])})
    return segs


sys_segs = load(SYS)
gt_segs = load(GT)
sys_bounds = sorted({round(s["start"], 2) for s in sys_segs} | {round(s["end"], 2) for s in sys_segs})

late, early, exact, batch_edge = [], [], 0, 0
for g in gt_segs:
    for b in (g["start"], g["end"]):
        near = min(sys_bounds, key=lambda x: abs(x - b))
        d = near - b
        if abs(d) <= 1.0:
            # 30s 批次边界的人为切分（±0.15s 内且是 30 的倍数）
            if abs(near % 30) < 0.15 or abs((near % 30) - 30) < 0.15:
                batch_edge += 1
                continue
            if d > 0.02:
                late.append(d)
            elif d < -0.02:
                early.append(-d)
            else:
                exact += 1

all_d = late + early
print(f"±1s 内找到系统边界的 GT 边界总数: {len(all_d)+exact+batch_edge}")
print(f"  其中是 30s 批次边缘人为切分: {batch_edge} ({batch_edge/(len(all_d)+exact+batch_edge)*100:.0f}%)")
print(f"  完全重合: {exact}")
print(f"  晚切 (系统边界在 GT 之后): {len(late)}  中位={statistics.median(late) if late else 0:.2f}s 均值={statistics.mean(late) if late else 0:.2f}s")
print(f"  早切 (系统边界在 GT 之前): {len(early)}  中位={statistics.median(early) if early else 0:.2f}s 均值={statistics.mean(early) if early else 0:.2f}s")
if all_d:
    print(f"  合并后 |d| 中位: {statistics.median(all_d):.2f}s")

# 看看 30s 边界上有多少系统边界（人为切段的规模）
at30 = [b for b in sys_bounds if abs(b % 30) < 0.15 or abs((b % 30) - 30) < 0.15]
print(f"\n系统边界总数={len(sys_bounds)}, 其中落在 30s 批次线上: {len(at30)} ({len(at30)/len(sys_bounds)*100:.0f}%)")

# 这些人造边界把一个说话人的段切成两段的规模：统计跨界批次的段
split_mid = 0
for s in sys_segs:
    if abs(s["start"] % 30) < 0.15 or abs((s["start"] % 30) - 30) < 0.15:
        if s["dur"] > 0.5:
            split_mid += 1
print(f"段头恰在批次线上的段数: {split_mid}（这些是被批次硬切的段）")
