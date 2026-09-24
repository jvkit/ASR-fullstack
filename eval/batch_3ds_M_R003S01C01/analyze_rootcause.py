"""终极归因：每个错误段里到底含没有被错认者的声音？
分类：
  A 批次碎段   — 段头/段尾贴着 30s 批次线（人为切的）
  B 跨界段     — 段内被错认者的语音 >= 0.15s（切点漂移，标签被带偏）
  C 干净段     — 段内被错认者语音 < 0.15s 且远离批次线（真·认人错）
"""
from collections import defaultdict

SYS = "eval/batch_3ds_M_R003S01C01/batch_3ds_M_R003S01C01.rttm"
GT = "eval/aishell4/test/TextGrid/M_R003S01C01.rttm"
EDGE = 0.3          # 批次线判定余量
CONTAM = 0.15       # 跨界污染判定秒数


def load(path):
    segs = []
    for line in open(path):
        p = line.split()
        if len(p) < 8 or p[0] != "SPEAKER":
            continue
        s, d = float(p[3]), float(p[4])
        segs.append({"spk": p[7], "start": s, "end": s + d, "dur": d})
    return segs


sys_segs = load(SYS)
gt_segs = load(GT)


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


cats = {"A_batch": [], "B_straddle": [], "C_clean": []}
for s in sys_segs:
    ov = defaultdict(float)
    for g in gt_segs:
        o = overlap(s["start"], s["end"], g["start"], g["end"])
        if o > 0:
            ov[g["spk"]] += o
    if not ov:
        continue
    total = sum(ov.values())
    dom = max(ov, key=ov.get)
    purity = ov[dom] / total
    if s["spk"] == dom or purity < 0.5:
        continue  # 没错或太碎无法判定

    # 段内"被错认者"的语音量
    false_sec = ov.get(s["spk"], 0.0)
    # 批次线贴近度
    near_batch = (min(abs(s["start"] % 30), 30 - abs(s["start"] % 30)) < EDGE or
                  min(abs(s["end"] % 30), 30 - abs(s["end"] % 30)) < EDGE)

    row = (s["start"], s["dur"], s["spk"], dom, purity, false_sec)
    if false_sec >= CONTAM:
        cats["B_straddle"].append(row)
    elif near_batch:
        cats["A_batch"].append(row)
    else:
        cats["C_clean"].append(row)

print("=== 错误段三分类 ===")
tot_all = 0
for k, rows in cats.items():
    sec = sum(r[1] * r[4] for r in rows)
    tot_all += sec
    durs = [r[1] for r in rows]
    print(f"{k:10s}: {len(rows):3d} 段, {sec:6.1f}s, 平均段长={sum(durs)/max(len(durs),1):.1f}s")

print(f"\n=== C 类明细（真·干净段认人错，voiceprint 唯一没法甩锅给拆分的部分）===")
for r in sorted(cats["C_clean"], key=lambda x: -x[1]):
    print(f"  {r[0]:8.1f}s  段长{r[1]:5.2f}s  认成={r[2]:<6} 实际={r[3]:<6} 纯度{r[4]:.2f} 内含被错认者{r[5]:.2f}s")

print(f"\n=== B 类里'大头其实是被错认者'的段（切对了人但切错界，或段本就是人家的）===")
flip = [r for r in cats["B_straddle"] if r[5] > r[1] * r[4] * 0.5]
print(f"  {len(flip)} 段 —— 这类段主要是被错认者的语音，只是边界漂进/漂出，本质是切分问题")

# C 类的段长分布 vs 全体
import statistics
cd = [r[1] for r in cats["C_clean"]]
if cd:
    print(f"\nC 类段长: 中位={statistics.median(cd):.2f}s 最短={min(cd):.2f}s 最长={max(cd):.2f}s")
