"""探针：异常段直接送 GPU /speaker_identify，看完整相似度排名。
1) 1728.4s 5.25s 纯001-M被判006-F 的段（降噪版+生肉版对照）
2) 一个被判对的 001-M 长段作对照
"""
import sys, wave, httpx
from pathlib import Path

ROOT = Path("/home/jvkit/workspace/meeting/meeting-asr/server")
sys.path.insert(0, str(ROOT))
from app.core.preprocess import denoise_pcm  # noqa: E402

GPU = "http://172.16.16.120:10096"
WAV = "/home/jvkit/workspace/meeting/meeting-asr/eval/aishell4/M_R003S01C01_ch0.wav"


def cut(start: float, dur: float) -> bytes:
    with wave.open(WAV, "rb") as w:
        w.setpos(int(start * 16000))
        return w.readframes(int(dur * 16000))


def probe(pcm: bytes, tag: str):
    r = httpx.post(f"{GPU}/speaker_identify", content=pcm, timeout=60)
    j = r.json()
    print(f"\n── {tag} ──")
    if not j.get("ok"):
        print("  ERROR:", j.get("error"))
        return
    print(f"  verdict={j['verdict']}  best={j['best']}({j['best_sim']})  margin={j['margin']}  段数={j['segments']}")
    for c in j["ranked"]:
        mark = " ← 正确应为001-M" if c["name"] == "001-M" else ""
        print(f"    {c['name']:<8} {c['sim']:.3f}{mark}")


# 1) 异常段 1728.4-1733.65（5.25s，GT=001-M，系统判006-F）
raw = cut(1728.4, 5.25)
probe(denoise_pcm(raw), "异常段 1728.4s/5.25s 【降噪版】 GT=001-M 系统判006-F")
probe(raw, "异常段 1728.4s/5.25s 【生肉版】")

# 2) 对照：找一个系统判对的长 001-M 段（从系统RTTM里挑）
sys_rttm = "/home/jvkit/workspace/meeting/meeting-asr/eval/batch_3ds_M_R003S01C01/batch_3ds_M_R003S01C01.rttm"
cands = []
for line in open(sys_rttm):
    p = line.split()
    if len(p) >= 8 and p[7] == "001-M" and float(p[4]) >= 8:
        cands.append((float(p[3]), float(p[4])))
# 避开 1728 附近，取第一个
s0, d0 = cands[0]
print(f"\n(对照段: {s0}s 长{d0:.1f}s，系统判001-M)")
raw2 = cut(s0, d0)
probe(denoise_pcm(raw2), f"对照段 {s0}s/{d0:.1f}s 【降噪版】 系统判001-M(对)")
