"""终极判定：argmax 版残留的 18 个'干净段认人错'，逐个送 /speaker_identify。
若段单独送就能认对 → 锅在簇质心被兄弟段拖歪（聚合层算错）
若段单独送也认不对 → 短段嵌入本身模糊（模型层算错）
 Vad 切对切错由 analyze_rootcause 的纯度判定。
"""
import wave, httpx
from collections import defaultdict

SYS = "eval/batch_3ds_M_R003S01C01/batch_3ds_M_R003S01C01.rttm"
GT = "eval/aishell4/test/TextGrid/M_R003S01C01.rttm"
DN = "eval/batch_3ds_M_R003S01C01/M_R003S01C01_denoised.wav"
GPU = "http://172.16.16.120:10096"
CONTAM, EDGE = 0.15, 0.3


def load(path):
    out = []
    for line in open(path):
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            s, d = float(p[3]), float(p[4])
            out.append({"spk": p[7], "start": s, "end": s + d, "dur": d})
    return out


sys_segs, gt_segs = load(SYS), load(GT)
with wave.open(DN) as w:
    dn = w.readframes(w.getnframes())


def ov(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


targets = []
for s in sys_segs:
    o = defaultdict(float)
    for g in gt_segs:
        x = ov(s["start"], s["end"], g["start"], g["end"])
        if x > 0:
            o[g["spk"]] += x
    if not o:
        continue
    dom = max(o, key=o.get)
    pur = o[dom] / sum(o.values())
    if s["spk"] == dom or pur < 0.5:
        continue
    near_batch = (min(abs(s["start"] % 30), 30 - abs(s["start"] % 30)) < EDGE or
                  min(abs(s["end"] % 30), 30 - abs(s["end"] % 30)) < EDGE)
    if o.get(s["spk"], 0.0) < CONTAM and not near_batch:
        targets.append((s, dom, pur))

print(f"C_clean 段数: {len(targets)}\n")
n_self_top1, n_self_ok, n_self_bad = 0, 0, 0
for s, dom, pur in sorted(targets, key=lambda x: -x[0]["dur"]):
    a, b = int(s["start"] * 16000), int(s["end"] * 16000)
    clip = dn[a * 2:b * 2]
    # 补到 >=2s 让 /speaker_identify 走整段兜底
    need = 2.0 * 32000 - len(clip)
    if need > 0:
        clip = clip + b"\x00\x00" * int(need // 2)
    r = httpx.post(f"{GPU}/speaker_identify", content=clip, timeout=60)
    j = r.json()
    if not j.get("ok"):
        print(f"  {s['start']:8.1f}s {s['dur']:4.2f}s GT={dom} 系统={s['spk']}  [probe失败: {j.get('error')}]")
        continue
    ranked = {c["name"]: c["sim"] for c in j["ranked"]}
    self_sim = ranked.get(dom, 0)
    self_rank = sorted(ranked, key=lambda n: -ranked[n]).index(dom) + 1
    top1 = max(ranked, key=lambda n: ranked[n])
    ok = top1 == dom and ranked[top1] >= 0.40
    if ok:
        n_self_top1 += 1
    if self_rank <= 2 and self_sim >= 0.40:
        n_self_ok += 1
    else:
        n_self_bad += 1
    tag = "段单独送✓能对" if ok else ("段单独送也错" if self_rank > 1 else "段单独送第二名")
    print(f"  {s['start']:8.1f}s {s['dur']:4.2f}s GT={dom} 系统={s['spk']} | probe: top1={top1}({ranked[top1]:.3f}) {dom}排第{self_rank}位({self_sim:.3f}) → {tag}")

print(f"\n=== 汇总 ===")
print(f"段单独送即认对(top1&过阈): {n_self_top1}/{len(targets)}")
print(f"段单独送至少前二且过阈(接近可对): {n_self_ok}/{len(targets)}")
print(f"段单独送也确实模糊: {n_self_bad}/{len(targets)}")
