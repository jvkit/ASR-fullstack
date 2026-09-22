"""ASR 字错率(CER)评测：热词/标点等文字侧改动的量化工具。

对比"直接打 GPU /meeting_chunk"的转写文本 vs AISHELL-4 TextGrid 人工转写。
按 GT 说话人分别算 CER（系统段经多数重叠投票归属 GT 说话人），整体 CER=总编辑数/总参考字数。

用法（项目根目录）:
    python server/scripts/eval_cer.py <wav> <textgrid> [--hotwords-file F] [--max-sec 300]
产物仅打印（不进库，不重放完整管线——热词只影响文字，分人结果不受影响的口径足够对比）。

注意：会末终版(final=true)走整场一次调用，长音频请给足 --timeout。
"""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

import requests

PUNCT = re.compile(r"[<sil>，。！？、；：""''（）《》〈〉—…·\s]+")

# GPU 机器 IP 会变（2026-09-22 由 192.168.30.87 迁到 172.16.16.120）；
# 与 server/app/config.py 的 DIARIZE_HTTP_URL 同源，改 IP 只需动环境变量。
GPU_URL = os.environ.get("DIARIZE_HTTP_URL", "http://172.16.16.120:10096")


def norm(t: str) -> str:
    return PUNCT.sub("", t).lower()


def parse_textgrid(path: Path) -> dict[str, list[tuple[float, float, str]]]:
    """-> {说话人: [(xmin,xmax,text)]} 仅非空句"""
    tiers: dict[str, list] = {}
    cur = None
    xmin = xmax = 0.0
    pending = False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        m = re.match(r'name = "(.+?)"', s)
        if m:
            cur = m.group(1)
            tiers.setdefault(cur, [])
            continue
        m = re.match(r"xmin = ([\d.]+)", s)
        if m:
            xmin = float(m.group(1))
            continue
        m = re.match(r"xmax = ([\d.]+)", s)
        if m:
            xmax = float(m.group(1))
            continue
        m = re.match(r'text = "(.*?)"\s*$', s)
        if m and cur:
            t = m.group(1)
            if t.strip():
                tiers[cur].append((xmin, xmax, t))
    return tiers


def cer(ref: str, hyp: str) -> tuple[int, int]:
    """(编辑数, 参考长度) 字符级 Levenshtein（滚动数组）"""
    n, m = len(ref), len(hyp)
    if not n:
        return 0, 0
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        rc = ref[i - 1]
        for j in range(1, m + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hyp[j - 1]))
        prev = cur
    return prev[m], n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("textgrid")
    ap.add_argument("--hotwords-file", default="")
    ap.add_argument("--max-sec", type=float, default=None)
    ap.add_argument("--url", default=f"{GPU_URL.rstrip('/')}/meeting_chunk")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--weight", type=float, default=1.0, help="SeACo 偏置强度 λ(0~1, 越小越温和)")
    args = ap.parse_args()

    import soundfile as sf
    audio, sr = sf.read(args.wav, dtype="float32")
    if args.max_sec:
        audio = audio[: int(args.max_sec * sr)]

    hotwords = ""
    if args.hotwords_file:
        words = [l.strip() for l in Path(args.hotwords_file).read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        hotwords = " ".join(words)
        print(f"热词: {args.hotwords_file} ({len(words)} 词)")

    import io
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    t0 = time.time()
    r = requests.post(args.url, files={"file": ("eval.wav", buf.getvalue())},
                      data={"final": "true", "hotwords": hotwords,
                            "hw_weight": str(args.weight)}, timeout=args.timeout)
    elapse = time.time() - t0
    segs = r.json().get("segs", [])
    print(f"推理: {elapse:.1f}s / {len(audio)/sr:.0f}s 音频, 出 {len(segs)} 段")

    tiers = parse_textgrid(Path(args.textgrid))
    if args.max_sec:
        # 丢弃起点超出范围的句（只裁 end 会把全文算进参考，分母虚高 ~7 倍）
        tiers = {k: [(a, min(b, args.max_sec), t) for a, b, t in v if a < args.max_sec]
                 for k, v in tiers.items()}
    units = [(a, b, t) for v in tiers.values() for a, b, t in v]  # GT 句
    ref_by_spk: dict[str, list[str]] = {k: [] for k in tiers}
    hyp_by_spk: dict[str, list[str]] = {k: [] for k in tiers}

    for s in segs:
        ss, ee = float(s["start"]), float(s["end"])
        best, bn = None, 0
        for a, b, t in units:
            ov = min(ee, b) - max(ss, a)
            if ov > bn:
                bn, best = ov, t
        if best is not None and bn > 0.15:
            # 找该 GT 句属于谁
            owner = next(k for k, v in tiers.items() if any(x[2] is best for x in v))
            hyp_by_spk[owner].append(norm(s["text"]))

    for k, v in tiers.items():
        ref_by_spk[k] = [norm(t) for _, _, t in v]

    tot_e = tot_n = 0
    print("说话人 CER:")
    for k in sorted(tiers):
        ref = "".join(ref_by_spk[k])
        hyp = "".join(hyp_by_spk[k])
        if not ref:
            continue
        e, n = cer(ref, hyp)
        tot_e += e
        tot_n += n
        print(f"  {k}: {e/n*100:.2f}%  (参考{n}字 系统{len(hyp)}字)")
    if tot_n:
        print(f"整体 CER: {tot_e/tot_n*100:.2f}%  ({tot_e}/{tot_n})  延迟 {elapse:.1f}s")


if __name__ == "__main__":
    main()
