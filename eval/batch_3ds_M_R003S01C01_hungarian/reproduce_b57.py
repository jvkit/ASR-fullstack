"""复现批次57(1710-1740s)：3D聚类结构 vs GT，揪出 1728.4s 段被染成006-F的机制。"""
import sys, wave, json, httpx
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/jvkit/workspace/meeting/meeting-asr/server")
sys.path.insert(0, str(ROOT))
from app.core.preprocess import denoise_pcm  # noqa: E402
from app.core.diarize3ds import diarize  # noqa: E402

WAV = "/home/jvkit/workspace/meeting/meeting-asr/eval/aishell4/M_R003S01C01_ch0.wav"
GT = "/home/jvkit/workspace/meeting/meeting-asr/eval/aishell4/test/TextGrid/M_R003S01C01.rttm"
GPU = "http://172.16.16.120:10096"
B0, B1 = 1710.0, 1740.0

with wave.open(WAV, "rb") as w:
    w.setpos(int(B0 * 16000))
    pcm = w.readframes(int((B1 - B0) * 16000))

pcm_dn = denoise_pcm(pcm)
segs = diarize(pcm_dn)  # [(start, end, cluster_id)] 相对批次
print(f"批次 1710-1740s 降噪后 {len(pcm_dn)/32000:.1f}s, 3D 切出 {len(segs)} 段")

gt = []
for line in open(GT):
    p = line.split()
    if len(p) >= 8 and p[0] == "SPEAKER":
        gt.append((float(p[3]), float(p[3]) + float(p[4]), p[7]))


def gt_at(t0, t1):
    ov = defaultdict(float)
    for g0, g1, spk in gt:
        o = max(0.0, min(t1, g1) - max(t0, g0))
        if o > 0:
            ov[spk] += o
    if not ov:
        return "-", 0.0
    dom = max(ov, key=ov.get)
    return dom, ov[dom] / (sum(ov.values()) or 1)


clusters = defaultdict(list)
for seg in segs:
    clusters[seg["cluster_id"]].append((seg["start"], seg["end"]))

print("\n=== 各簇构成（段数/总时长/GT构成）===")
for cid, ss in sorted(clusters.items()):
    total = sum(b - a for a, b in ss)
    comp = defaultdict(float)
    for a, b in ss:
        for g0, g1, spk in gt:
            o = max(0.0, min(B0 + b, g1) - max(B0 + a, g0))
            if o > 0:
                comp[spk] += o
    comp_s = ", ".join(f"{k}:{v:.1f}s" for k, v in sorted(comp.items(), key=lambda x: -x[1]))
    print(f"  簇{cid}: {len(ss)}段 {total:.1f}s | GT: {comp_s}")

print("\n=== 含 1728.4s(批内18.4s) 的簇，逐段 ===")
target_cid = None
for seg in segs:
    if seg["start"] <= 18.4 < seg["end"]:
        target_cid = seg["cluster_id"]
        break
print(f"目标簇 = 簇{target_cid}")
for a, b in sorted(clusters[target_cid]):
    dom, pur = gt_at(B0 + a, B0 + b)
    print(f"  批内[{a:6.2f},{b:6.2f}] 绝对[{B0+a:8.1f},{B0+b:8.1f}] 长{b-a:5.2f}s GT={dom} 纯度{pur:.2f}")

# 送 GPU identify_segments 看这个簇被判成谁
payload = {"segments": json.dumps([[round(s_["start"], 3), round(s_["end"], 3), int(s_["cluster_id"])] for s_ in segs])}
r = httpx.post(f"{GPU}/identify_segments", files={"file": ("b57.wav", pcm_dn)},
               data=payload, timeout=300)
j = r.json()
print("\n=== GPU /identify_segments 返回（每簇标签）===")
print(json.dumps({k: v for k, v in j.items() if k != "segments"}, ensure_ascii=False, indent=1)[:800])
