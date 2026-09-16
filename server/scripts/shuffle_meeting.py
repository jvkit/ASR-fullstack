"""打散测试：把"一人讲一大段"的会议切成短段随机交错，生成新 wav + rttm。

背景（2026-09-16 用户要求）：AISHELL-4 评测会议是长段单人连说，太简单，
与真实会议插话场景差得远。本脚本按 rttm 切段（可截长段），轮转/随机交错
拼接，seed 不同即全新测试集——防止"背答案"式评测。

用法（项目根目录）:
    python server/scripts/shuffle_meeting.py <wav> <rttm> --out eval/shuffled/<name> \
        [--max-seg 8] [--min-seg 2] [--gap 0.3] [--seed 42] [--mode roundrobin|random]

产物：
    eval/shuffled/<name>.wav     16k mono pcm16
    eval/shuffled/<name>.rttm    新时间轴下的 GT（说话人 id 沿用原 rttm）
"""
from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]


def load_rttm(path: Path, max_sec: float | None) -> list[tuple[str, float, float]]:
    out = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            spk, s, d = p[7], float(p[3]), float(p[4])
            if max_sec and s >= max_sec:
                continue
            e = min(s + d, max_sec) if max_sec else s + d
            if e - s >= 0.3:
                out.append((spk, s, e))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("rttm")
    ap.add_argument("--out", required=True, help="产物路径前缀（无扩展名）")
    ap.add_argument("--max-seg", type=float, default=8.0, help="长段截断上限（秒）")
    ap.add_argument("--min-seg", type=float, default=0.8, help="碎段丢弃下限（秒）")
    ap.add_argument("--gap", type=float, default=0.3, help="段间静音（秒）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--mode", choices=["roundrobin", "random"], default="roundrobin")
    ap.add_argument("--max-sec", type=float, default=None, help="只取原音频前 N 秒")
    args = ap.parse_args()

    audio, sr = sf.read(args.wav, dtype="float32")
    assert sr == 16000, f"需要 16k 音频，实际 {sr}"
    segs = load_rttm(Path(args.rttm), args.max_sec)

    # 切段：同一人相邻段合并后再按 max_seg 切
    by_spk: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for spk, s, e in sorted(segs, key=lambda x: x[1]):
        if by_spk[spk] and s - by_spk[spk][-1][1] < 0.5:
            by_spk[spk][-1] = (by_spk[spk][-1][0], e)
        else:
            by_spk[spk].append((s, e))

    units = []  # (spk, s, e)
    for spk, spans in by_spk.items():
        for s, e in spans:
            t = s
            while t < e - args.min_seg:
                t1 = min(t + args.max_seg, e)
                if t1 - t >= args.min_seg:
                    units.append((spk, t, t1))
                t = t1

    rng = random.Random(args.seed)
    if args.mode == "random":
        rng.shuffle(units)
    else:  # roundrobin：各说话人轮转出场，每人每轮出一段（顺序随机）
        buckets: dict[str, list] = defaultdict(list)
        for u in units:
            buckets[u[0]].append(u)
        for b in buckets.values():
            rng.shuffle(b)
        order = list(buckets)
        rng.shuffle(order)
        units = []
        while any(buckets.values()):
            for spk in order:
                if buckets[spk]:
                    units.append(buckets[spk].pop())

    # 拼接
    gap_n = int(args.gap * sr)
    out_audio: list[np.ndarray] = []
    rttm_lines = []
    cursor = 0.0
    for spk, s, e in units:
        clip = audio[int(s * sr):int(e * sr)]
        out_audio.append(clip)
        rttm_lines.append(
            f"SPEAKER shuffled 1 {cursor:.3f} {e - s:.3f} <NA> <NA> {spk} <NA> <NA>")
        cursor += (e - s) + args.gap

    wav = np.concatenate(out_audio + [np.zeros(gap_n, dtype="float32")])
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out) + ".wav", wav, sr, subtype="PCM_16")
    Path(str(out) + ".rttm").write_text("\n".join(rttm_lines) + "\n", encoding="utf-8")
    print(f"说话人 {len(by_spk)} 个，段 {len(units)} 条，总长 {cursor:.1f}s -> {out}.wav/.rttm")


if __name__ == "__main__":
    main()
