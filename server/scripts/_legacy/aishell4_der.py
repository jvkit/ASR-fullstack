"""AISHELL-4 说话人日志基准测试：pipeline 输出 vs rttm 标注算 DER。

流程（对应生产会后版 pipeline）：
  会议 flac -> POST gpu /vad (250ms 切句) -> POST /diarize_sent (逐句 ER arg纹)
  -> 按时间轴与 rttm 对帧 -> DER = (漏检+误检+错标) / 总标注时长

用法（在本地，音频走 HTTP 上 GPU）：
  python scripts/aishell4_der.py [--meetings S_R003S01C01 ...] [--vad-silence 250]

前提：eval/aishell4/test/wav/*.flac + test/TextGrid/*.rttm 已解压；
测试声纹已注册到 GPU ~/jvkit/asr/eval/aishell4_enrolls/<spk>/*.pcm（见 aishell4_prep.py）。
"""

import argparse
import io
import json
import sys
import wave
from collections import defaultdict
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval" / "aishell4"
GPU = "http://192.168.30.87:10096"
ENROLL_BASE = "/home/bo1/jvkit/asr/eval/aishell4_enrolls"  # 每场会议一个子目录（aishell4_prep.py 生成）
FRAME = 0.01  # 10ms 帧


def load_rttm(path: Path) -> list[tuple[str, float, float]]:
    """rttm -> [(spk_id, start_s, dur_s)]"""
    out = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            out.append((p[7], float(p[3]), float(p[4])))
    return out


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def frame_speaker(segs: list[tuple[str, float, float]], total: float) -> np.ndarray:
    """按 10ms 帧铺说话人。重叠段取后者（rttm 极少重叠，取先者亦可）。"""
    n = int(total / FRAME) + 1
    lab = np.full(n, "", dtype=object)
    for spk, st, dur in segs:
        a, b = int(st / FRAME), int((st + dur) / FRAME) + 1
        lab[a:min(b, n)] = spk
    return lab


def der(ref: list, hyp: list, total: float) -> dict:
    """DER 三件套 + 最优人名映射（按重叠时长贪心）。"""
    rl = frame_speaker(ref, total)
    hl = frame_speaker(hyp, total)
    n = min(len(rl), len(hl))
    rl, hl = rl[:n], hl[:n]

    # 人名映射：ref_spk -> hyp_spk，按共现帧数贪心
    co = defaultdict(int)
    for r, h in zip(rl, hl):
        if r and h:
            co[(r, h)] += 1
    mapping = {}
    used_h = set()
    for (r, h), c in sorted(co.items(), key=lambda kv: -kv[1]):
        if r not in mapping and h not in used_h:
            mapping[r] = h
            used_h.add(h)

    miss = fa = conf = ref_tot = 0
    for r, h in zip(rl, hl):
        if r:
            ref_tot += 1
            if not h:
                miss += 1
            elif mapping.get(r) != h:
                conf += 1
        elif h:
            fa += 1
    denom = max(ref_tot, 1)
    return {
        "DER": round((miss + fa + conf) / denom, 4),
        "miss": round(miss / denom, 4),
        "fa": round(fa / denom, 4),
        "conf": round(conf / denom, 4),
        "ref_frames": ref_tot,
    }


async def run_meeting(client: httpx.AsyncClient, mid: str, vad_silence: int) -> dict:
    wav = EVAL_DIR / "test" / "wav" / f"{mid}.flac"
    rttm = EVAL_DIR / "test" / "TextGrid" / f"{mid}.rttm"
    audio, sr = sf.read(wav)
    if audio.ndim > 1:
        audio = audio[:, 0]
    wb = wav_bytes(audio, sr)

    r = await client.post(f"{GPU}/vad", files={"file": (mid + ".wav", wb, "audio/wav")},
                          data={"max_end_silence_ms": str(vad_silence)})
    r.raise_for_status()
    sents = r.json()["sents"]

    r = await client.post(
        f"{GPU}/diarize_sent",
        files={"file": (mid + ".wav", wb, "audio/wav")},
        data={"sents": json.dumps(sents), "name_threshold": "0.30",
              "enroll_dir": f"{ENROLL_BASE}/{mid}"},
        timeout=1800,
    )
    r.raise_for_status()
    segs = r.json()["segs"]
    hyp = [(s[3], float(s[0]), float(s[1]) - float(s[0])) for s in segs]

    ref = load_rttm(rttm)
    total = max([s + d for _, s, d in ref] + [s + d for _, s, d in hyp])
    return der(ref, hyp, total)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings", nargs="*", default=None)
    ap.add_argument("--vad-silence", type=int, default=250)
    args = ap.parse_args()

    mids = args.meetings or sorted(
        p.stem for p in (EVAL_DIR / "test" / "TextGrid").glob("*.rttm")
    )
    async with httpx.AsyncClient(timeout=300) as client:
        for mid in mids:
            if not (EVAL_DIR / "test" / "wav" / f"{mid}.flac").exists():
                print(f"{mid}: 无音频，跳过")
                continue
            try:
                res = await run_meeting(client, mid, args.vad_silence)
                print(f"{mid}: {json.dumps(res)}", flush=True)
            except Exception as e:
                print(f"{mid}: ERROR {e}", flush=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
