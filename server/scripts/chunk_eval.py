"""5分钟块级评测：切一块 -> 注册 -> VAD -> diarize -> DER + 错误段清单。

用法: python scripts/chunk_eval.py <meeting_id> <start_min> [--dur 5]
输出: DER 三件套 + 逐段对比表（正确段 ✓ / 错段 ✗ 及真值）+ 错误段明细文件
      eval/aishell4/chunks/<meeting>_<start>m/errors.json
"""
import argparse
import io
import json
import subprocess
import wave
from collections import defaultdict
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval" / "aishell4"
GPU = "http://192.168.30.87:10096"
ENROLL_BASE = "/home/bo1/jvkit/asr/eval/aishell4_enrolls"
CH = 6  # 早前实测最优通道


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def load_rttm_chunk(path: Path, t0: float, t1: float) -> list[tuple[str, float, float]]:
    out = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            st, dur = float(p[3]), float(p[4])
            a, b = max(st, t0), min(st + dur, t1)
            if b > a:
                out.append((p[7], a - t0, b - t0))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("meeting")
    ap.add_argument("start_min", type=float)
    ap.add_argument("--dur", type=float, default=5.0)
    args = ap.parse_args()

    mid = args.meeting
    t0 = args.start_min * 60
    dur = args.dur * 60  # 分钟->秒
    base = EVAL_DIR / "test"
    audio, sr = sf.read(base / "wav" / f"{mid}.flac")
    seg = audio[int(t0 * sr):int((t0 + dur) * sr), CH].copy()
    rms = float(np.sqrt(np.mean(seg ** 2)))
    if rms > 1e-6:
        seg = seg * (0.1 / rms)
    wb = wav_bytes(seg, sr)
    dur_s = len(seg) / sr

    ref = load_rttm_chunk(base / "TextGrid" / f"{mid}.rttm", t0, t0 + dur)

    # 1) 注册（本块 rttm 每人 12s）
    by_spk: dict[str, list] = defaultdict(list)
    for spk, a, b in ref:
        by_spk[spk].append((a, b))
    rdir = f"{ENROLL_BASE}/{mid}_{int(args.start_min)}m"
    subprocess.run(["ssh", "gpu", f"mkdir -p {rdir}"], check=True)
    manifest = {}
    for spk, segs in sorted(by_spk.items()):
        segs.sort(key=lambda x: -(x[1] - x[0]))
        clips, total = [], 0.0
        for a, b in segs:
            if total >= 12:
                break
            clip = seg[int(a * sr):int(b * sr)]
            if len(clip) < sr:
                continue
            clips.append(clip)
            total += b - a
        if not clips:
            continue
        pcm = (np.concatenate(clips) * 32767).astype(np.int16)
        lp = Path(f"/tmp/_e_{spk}.pcm")
        lp.write_bytes(pcm.tobytes())
        subprocess.run(["scp", "-q", str(lp), f"gpu:{rdir}/{spk}.pcm"], check=True)
        lp.unlink()
        manifest[spk] = round(len(pcm) / 16000, 1)
    print(f"注册: {json.dumps(manifest, ensure_ascii=False)}", flush=True)

    client = httpx.Client(timeout=1800)
    # 2) VAD
    r = client.post(f"{GPU}/vad", files={"file": (mid + ".wav", wb, "audio/wav")},
                    data={"max_end_silence_ms": "250"})
    r.raise_for_status()
    sents = r.json()["sents"]
    # 3) diarize
    r = client.post(f"{GPU}/diarize_sent", files={"file": (mid + ".wav", wb, "audio/wav")},
                    data={"sents": json.dumps(sents), "name_threshold": "0.30", "enroll_dir": rdir})
    r.raise_for_status()
    segs = r.json()["segs"]
    hyp = [(s[3], float(s[0]), float(s[1])) for s in segs]

    # 4) 帧级 DER
    n = int(dur_s / 0.01) + 1
    rl = np.full(n, "", dtype=object)
    for spk, a, b in ref:
        rl[int(a / 0.01):int(b / 0.01) + 1] = spk
    hl = np.full(n, "", dtype=object)
    for spk, a, b in hyp:
        hl[int(a / 0.01):int(b / 0.01) + 1] = spk
    co = defaultdict(int)
    for r_, h_ in zip(rl, hl):
        if r_ and h_:
            co[(r_, h_)] += 1
    mapping = {}
    used = set()
    for (r_, h_), c in sorted(co.items(), key=lambda kv: -kv[1]):
        if r_ not in mapping and h_ not in used:
            mapping[r_] = h_
            used.add(h_)
    miss = fa = conf = ref_tot = 0
    for r_, h_ in zip(rl, hl):
        if r_:
            ref_tot += 1
            if not h_:
                miss += 1
            elif mapping.get(r_) != h_:
                conf += 1
        elif h_:
            fa += 1
    d = max(ref_tot, 1)
    print(f"\n== {mid} [{args.start_min}m-{args.start_min + args.dur}m] ==", flush=True)
    print(f"VAD {len(sents)} 句 -> {len(segs)} 子段", flush=True)
    print(f"DER {(miss + fa + conf) / d:.3f} = miss {miss / d:.3f} + fa {fa / d:.3f} + conf {conf / d:.3f}", flush=True)

    # 5) 错误段明细：ref 逐段看 hyp 标签
    errors = []
    print("\n段级对比（仅列错段）:", flush=True)
    for spk, a, b in ref:
        hlab = hl[int(a / 0.01):int(b / 0.01) + 1]
        if len(hlab) == 0:
            continue
        vals, cnts = np.unique(hlab, return_counts=True)
        top = sorted(zip(vals, cnts), key=lambda x: -x[1])[0]
        named_cov = sum(c for v, c in zip(vals, cnts) if v) / len(hlab)
        if top[0] != spk or named_cov < 0.5:
            err = {"ref": spk, "a": round(a, 2), "b": round(b, 2),
                   "hyp_top": top[0] or "(无名)", "named_cov": round(named_cov, 2)}
            errors.append(err)
            print(f"  ✗ {a:7.2f}-{b:7.2f} 真值={spk} 判={err['hyp_top']} 命名覆盖={named_cov:.0%}", flush=True)
    print(f"\n错段 {len(errors)}/{len(ref)}", flush=True)

    outdir = EVAL_DIR / "chunks" / f"{mid}_{int(args.start_min)}m"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=1))
    (outdir / "segs.json").write_text(json.dumps(
        [{"speaker": h[0], "start_ms": int(h[1] * 1000), "end_ms": int(h[2] * 1000)} for h in hyp],
        ensure_ascii=False))
    (outdir / "result.json").write_text(json.dumps({
        "der": (miss + fa + conf) / d, "miss": miss / d, "fa": fa / d, "conf": conf / d,
        "n_ref": len(ref), "n_err": len(errors), "n_sents": len(sents), "n_subs": len(segs),
    }, indent=1))
    # 6) 发布为历史会议：音频 + 会后版(我们的标签+真值文字) + 属性(DER+错段)
    #    文字来自数据集 TextGrid 标注（非系统 ASR），核对说话人用
    chunk_id = f"chunk_{mid}_{int(args.start_min)}m"
    import datetime
    rec_dir = Path(__file__).resolve().parents[1] / "recordings"
    rec_dir.mkdir(exist_ok=True)
    with wave.open(str(rec_dir / f"{chunk_id}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes())

    # 解析 TextGrid：[(speaker, xmin, xmax, text)]
    tg_path = base / "TextGrid" / f"{mid}.TextGrid"
    tg_words: list[tuple[str, float, float, str]] = []
    cur_tier = None
    lines = [l.strip() for l in tg_path.read_text(encoding="utf-8", errors="ignore").splitlines()]
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("name = "):
            cur_tier = l.split('"')[1]
        elif l.startswith("xmin = ") and i + 2 < len(lines) and "text = " in lines[i + 2]:
            try:
                x0 = float(l.split("=")[1])
                x1 = float(lines[i + 1].split("=")[1])
                txt = lines[i + 2].split('"')[1].strip()
                if txt and cur_tier:
                    tg_words.append((cur_tier, x0 - t0, x1 - t0, txt))
            except (ValueError, IndexError):
                pass
            i += 2
        i += 1

    # 每段真值文字只归重叠最多的子段，避免长标注被重复贴到多个子段
    seg_truth = [""] * len(hyp)
    for spk_t, x0, x1, t in tg_words:
        best_i, best_ov = -1, 0.0
        for i, h in enumerate(hyp):
            ov = min(x1, h[2]) - max(x0, h[1])
            if ov > best_ov:
                best_ov, best_i = ov, i
        if best_i >= 0 and best_ov > 0:
            seg_truth[best_i] = (seg_truth[best_i] + " " + t).strip()

    import asyncio
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy import delete as _delete
    from app.db.database import SessionLocal
    from app.db.models import Meeting, ReAnnotateUtterance, Utterance

    async def _publish() -> None:
        async with SessionLocal() as db:
            await db.execute(_delete(ReAnnotateUtterance).where(
                ReAnnotateUtterance.meeting_id == chunk_id))
            await db.execute(_delete(Utterance).where(Utterance.meeting_id == chunk_id))
            await db.execute(_delete(Meeting).where(Meeting.id == chunk_id))
            der = (miss + fa + conf) / d
            attrs = [
                {"key": "DER", "value": f"{der:.1%} = miss {miss/d:.1%} + fa {fa/d:.1%} + conf {conf/d:.1%}"},
                {"key": "标注段/错段", "value": f"{len(ref)} / {len(errors)}"},
                {"key": "文字来源", "value": "数据集 TextGrid 真值标注（非系统 ASR），专供核对说话人"},
            ]
            for e in errors[:15]:
                attrs.append({"key": f"✗ {e['a']:.1f}-{e['b']:.1f}s",
                              "value": f"真值 {e['ref']} → 判 {e['hyp_top']}"})
            db.add(Meeting(
                id=chunk_id,
                title=f"【基准】{mid} {int(args.start_min)}-{int(args.start_min + dur/60)}min DER {der:.1%}",
                recorder="benchmark", status="ended",
                started_at=datetime.datetime.now(),
                ended_at=datetime.datetime.now(),
                attributes=json.dumps(attrs, ensure_ascii=False),
                reannotated=True,
            ))
            for i, h in enumerate(hyp):
                txt = seg_truth[i]
                db.add(ReAnnotateUtterance(
                    meeting_id=chunk_id, seq=i, speaker=h[0],
                    text=txt, start_ms=int(h[1] * 1000), end_ms=int(h[2] * 1000)))
            await db.commit()

    asyncio.run(_publish())
    print(f"已发布到历史会议: {chunk_id}", flush=True)


if __name__ == "__main__":
    main()
