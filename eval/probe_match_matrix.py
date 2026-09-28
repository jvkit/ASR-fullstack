#!/usr/bin/env python3
"""决定性探针：干净 GT 段直送 /speaker_identify，绕开 3D 聚类。

回答：差错到底在"匹配"还是"聚类"。
- 从降噪版 wav 取 t<180s 的干净单人 GT 段（2.0s，避开注册区），逐段送 GPU
  /speaker_identify（段 embedding → 与全部注册声纹比余弦，返回全候选）
- 输出 6×6 平均相似度矩阵（行=GT 说话人，列=声纹库参照）+ argmax 混淆 + margin
- 两个条件：注册参照分别来自 生肉 / 降噪版（复现 6.52 跑法的域差 vs 域对齐）

前置：后端 8010 起（注册代理），GPU 10096 在线。
"""
from __future__ import annotations

import io
import json
import sys
import wave
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]          # meeting-asr/
sys.path.insert(0, str(ROOT / "server"))
from scripts.batch_3ds_test import enroll_speakers, load_rttm  # noqa: E402

GPU = "http://172.16.16.120:10096"
RAW = ROOT / "eval/aishell4/M_R003S01C01_ch0.wav"
DENOISED = ROOT / "eval/batch_3ds_M_R003S01C01/M_R003S01C01_denoised.wav"
RTTM = ROOT / "eval/aishell4/test/TextGrid/M_R003S01C01.rttm"
GT_NAMES = ["001-M", "002-M", "003-F", "004-F", "005-F", "006-F"]
EVAL_BEFORE = 180.0     # 探针段取注册区之前（避免与注册音频重叠）
PROBE_DUR = 2.0
PER_SPK = 8


def read_clip(wav: Path, start: float, dur: float) -> bytes:
    with wave.open(str(wav), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        w.setpos(int(start * 16000))
        return w.readframes(int(dur * 16000))


def wav_dur(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


def enrollment_windows(gt: dict[str, list[tuple[float, float]]], spk: str) -> list[tuple[float, float]]:
    """复刻 enroll_speakers 的选段：t>=180 最长 3 段，pick_clip 取中段 min(dur-0.2,8)s。"""
    cands = sorted([(s, d) for s, d in gt[spk] if s >= 180.0 and d >= 2],
                   key=lambda x: -x[1])[:3]
    out = []
    for s, d in cands:
        take = min(d - 0.2, 8)
        if take < 2:
            continue
        st = s + (d - take) / 2
        out.append((st - 1.0, st + take + 1.0))   # ±1s 余量
    return out


def pick_probe_clips(wav: Path) -> dict[str, list[tuple[float, bytes]]]:
    """每说话人取最多 PER_SPK 段：时长>=2.6s 的 GT 段取中段 2.0s，
    与注册窗不重叠，外人重叠 <=0.3s，全程均匀抽取。"""
    gt = load_rttm(RTTM)
    out: dict[str, list[tuple[float, bytes]]] = {}
    for spk in GT_NAMES:
        enroll_wins = enrollment_windows(gt, spk)
        cands = []
        for s, d in gt[spk]:
            if d < PROBE_DUR + 0.6:
                continue
            mid = s + d / 2
            a, b = mid - PROBE_DUR / 2, mid + PROBE_DUR / 2
            if any(not (b <= w1 or a >= w2) for w1, w2 in enroll_wins):
                continue
            ov = sum(max(0.0, min(b, s2 + d2) - max(a, s2))
                     for o, segs in gt.items() if o != spk for s2, d2 in segs)
            if ov > 0.3:
                continue
            cands.append((a, b))
        cands.sort()
        # 均匀抽 PER_SPK 个
        if len(cands) > PER_SPK:
            idx = [round(i * (len(cands) - 1) / (PER_SPK - 1)) for i in range(PER_SPK)]
            cands = [cands[i] for i in idx]
        clips = []
        for a, b in cands:
            pcm = read_clip(wav, a, b - a)
            if len(pcm) >= int(PROBE_DUR * 16000) * 2:
                clips.append((a, pcm))
        out[spk] = clips
    return out


def probe(clips: dict[str, list[tuple[float, bytes]]]) -> dict:
    sim_sum: dict[tuple[str, str], float] = defaultdict(float)
    sim_cnt: dict[tuple[str, str], int] = defaultdict(int)
    argmax_cnt: dict[tuple[str, str], int] = defaultdict(int)
    margins: list[float] = []
    fails = 0
    for gspk in GT_NAMES:
        for _, pcm in clips[gspk]:
            try:
                r = httpx.post(f"{GPU}/speaker_identify", content=pcm, timeout=60)
                js = r.json()
                ranked = js.get("ranked") or []
                if not ranked:
                    fails += 1
                    continue
                for item in ranked:
                    sim_sum[(gspk, item["name"])] += float(item["sim"])
                    sim_cnt[(gspk, item["name"])] += 1
                top, second = ranked[0], ranked[1] if len(ranked) > 1 else {"sim": 0}
                argmax_cnt[(gspk, top["name"])] += 1
                margins.append(float(top["sim"]) - float(second["sim"]))
            except Exception as e:
                fails += 1
                print(f"  ✗ probe 失败 {gspk}: {e}", flush=True)
    return {"sim_sum": dict(sim_sum), "sim_cnt": dict(sim_cnt),
            "argmax": dict(argmax_cnt), "margins": margins, "fails": fails}


def report(tag: str, res: dict):
    print(f"\n{'='*70}\n■ 条件 {tag}\n{'='*70}")
    n_probe = sum(res["sim_cnt"].values()) / max(1, len(GT_NAMES))
    print(f"探针段数/人 ≈ {n_probe:.0f}，失败 {res['fails']}")
    hdr = "GT\\参照  " + "".join(f"{n:>8s}" for n in GT_NAMES)
    print(hdr)
    for g in GT_NAMES:
        row = f"{g:>8s}  "
        for r in GT_NAMES:
            k = (g, r)
            v = res["sim_sum"].get(k, 0) / res["sim_cnt"].get(k, 1)
            row += f"{v:8.3f}"
        print(row)
    print("\nargmax 命中矩阵（行=GT，列=系统给的标签）:")
    for g in GT_NAMES:
        row = f"{g:>8s}  "
        for r in GT_NAMES:
            row += f"{res['argmax'].get((g, r), 0):8d}"
        print(row)
    ms = sorted(res["margins"])
    if ms:
        print(f"\nmargin(top1-top2): mean={sum(ms)/len(ms):.3f} "
              f"p10={ms[len(ms)//10]:.3f} p50={ms[len(ms)//2]:.3f}")


def main():
    print(f"raw 时长 {wav_dur(RAW):.1f}s，denoised 时长 {wav_dur(DENOISED):.1f}s")
    clips = pick_probe_clips(DENOISED)
    for s, cs in clips.items():
        print(f"  {s}: {len(cs)} 探针段")

    # 条件1：注册参照来自生肉（复现 6.52 跑法）
    print("\n── 条件1：注册=生肉 ──")
    m = enroll_speakers(str(RAW), RTTM, 180.0, 8, 3)
    print(f"注册 {len(m)} 人")
    report("注册=生肉（复现 batch 跑法）", probe(clips))

    # 条件2：注册参照来自降噪版（域对齐）
    print("\n── 条件2：注册=降噪版 ──")
    m = enroll_speakers(str(DENOISED), RTTM, 180.0, 8, 3)
    print(f"注册 {len(m)} 人")
    report("注册=降噪版（域对齐）", probe(clips))

    # 还原为跑法1的注册，保持库状态与既有评测一致
    print("\n── 收尾：注册还原为生肉版 ──")
    enroll_speakers(str(RAW), RTTM, 180.0, 8, 3)


if __name__ == "__main__":
    main()
