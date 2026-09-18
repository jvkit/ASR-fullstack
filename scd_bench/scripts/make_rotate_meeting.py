"""裁判件生成器：多轮轮换会议（只造件+自检，不跑任何识别/分人）。

规格（用户 2026-09-18 定）：
- 总时长目标 ~2400s；6 人（3 男 3 女优先；主源 M_R003S01C01 仅 2 男，可用 L 会男声
  素材均不足 170s，故实际 2 男 4 女并在 recipe 记录）；尽量取 M 会，素材不足的人
  从 L_R004S01C01 补，跨会议者 GT ID 加会议名前缀（L_001-M），同会议保持原 ID
- 一轮 = 6 人各说一次，顺序每轮打乱（--seed）；每人每轮 1~3 句话（源 rttm 真实段，
  单段截断到 [1.5s,20s]），轮内句间停顿 0.25~0.4s
- 换人气口（轮内 A→B）0.8~2.0s 随机；轮间长静默 20~90s 随机撑总时长
- 素材不重复使用（每段只用一次）；长静默预算不够则告警并在 recipe 写明实际总时长，
  不许拉长换人气口凑数

用法（cwd=scd_bench/）: .venv/bin/python scripts/make_rotate_meeting.py
输出: eval/shuffled/rotate40_s3.{wav,rttm,recipe.json}
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]  # meeting-asr/
EVAL = ROOT / "eval" / "aishell4"
OUT_DIR = ROOT / "eval" / "shuffled"

# 阵容预设：mixed6 = rotate40 原版（M 原生+L 替补）；m6 = M 会原生 6 人（rotate_m30 用）
ROSTERS = {
    "mixed6": [
        {"src": "M_R003S01C01", "spk": "001-M", "gt": "001-M"},          # 男
        {"src": "L_R004S01C01", "spk": "001-M", "gt": "L_001-M"},        # 男（M_002-M 素材105s不足→L补）
        {"src": "L_R004S01C01", "spk": "005-F", "gt": "L_005-F"},        # 女（M_003-F 素材64s不足→L补）
        {"src": "M_R003S01C01", "spk": "004-F", "gt": "004-F"},
        {"src": "M_R003S01C01", "spk": "005-F", "gt": "005-F"},
        {"src": "M_R003S01C01", "spk": "006-F", "gt": "006-F"},
    ],
    "m6": [
        {"src": "M_R003S01C01", "spk": "001-M", "gt": "001-M"},
        {"src": "M_R003S01C01", "spk": "002-M", "gt": "002-M"},
        {"src": "M_R003S01C01", "spk": "003-F", "gt": "003-F"},
        {"src": "M_R003S01C01", "spk": "004-F", "gt": "004-F"},
        {"src": "M_R003S01C01", "spk": "005-F", "gt": "005-F"},
        {"src": "M_R003S01C01", "spk": "006-F", "gt": "006-F"},
    ],
}
SEG_MIN = 1.5


def load_pool(stem, spk, seg_min, seg_max):
    """源 rttm 中该说话人的可用段（截断到 [seg_min,seg_max]s），段 uid 唯一。"""
    segs = []
    for line in (EVAL / "test" / "TextGrid" / f"{stem}.rttm").read_text().splitlines():
        p = line.split()
        if p[0] != "SPEAKER" or p[7] != spk:
            continue
        st, ed = float(p[3]), float(p[3]) + float(p[4])
        dur = min(ed - st, seg_max)
        if dur >= seg_min:
            segs.append({"uid": f"{stem}:{spk}:{st:.4f}", "src_st": st, "dur": dur})
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--roster", choices=list(ROSTERS), default="mixed6")
    ap.add_argument("--speakers-per-round", type=int, default=6)
    ap.add_argument("--target-speech-sec", type=float, default=180.0)
    ap.add_argument("--speech-tol", type=float, default=10.0, help="每人目标容差 ±")
    ap.add_argument("--target-total-sec", type=float, default=2400.0)
    ap.add_argument("--rounds", type=int, default=15)
    ap.add_argument("--seg-min", type=float, default=SEG_MIN)
    ap.add_argument("--seg-max", type=float, default=20.0)
    ap.add_argument("--max-sents", type=int, default=3, help="每人每轮最多句数")
    ap.add_argument("--change-gap", nargs=2, type=float, default=[0.8, 2.0])
    ap.add_argument("--sent-pause", nargs=2, type=float, default=[0.25, 0.4])
    ap.add_argument("--round-pause", nargs=2, type=float, default=[20.0, 90.0])
    ap.add_argument("--out-prefix", default=None)
    args = ap.parse_args()
    roster = ROSTERS[args.roster]
    rng = random.Random(args.seed)
    chg_gap = tuple(args.change_gap)
    sent_pause = tuple(args.sent_pause)
    round_pause = tuple(args.round_pause)
    if args.out_prefix is None:
        args.out_prefix = str(OUT_DIR / f"rotate{int(args.target_speech_sec)}_s{args.seed}")

    # 1) 读源音频 + 素材池（打乱）
    wavs = {}
    pools = {}
    for r in roster:
        if r["src"] not in wavs:
            wavs[r["src"]], sr = sf.read(EVAL / f"{r['src']}_ch0.wav", dtype="float32")
            assert sr == 16000
        pool = load_pool(r["src"], r["spk"], args.seg_min, args.seg_max)
        rng.shuffle(pool)
        pools[r["gt"]] = pool
        avail = sum(s["dur"] for s in pool)
        print(f"{r['gt']:>8} <- {r['src']}/{r['spk']}: 池 {len(pool)} 段 {avail:.0f}s",
              "OK" if avail >= args.target_speech_sec + args.speech_tol else "★素材偏紧")
        if avail < args.target_speech_sec - args.speech_tol:
            sys.exit(f"素材不足 {args.target_speech_sec - args.speech_tol}s，无法达标，退出")

    # 2) 逐轮生成：顺序每轮打乱；每人 1~max_sents 段自适应逼近 target
    plan = []  # [{speaker, sents:[{uid,src_st,dur}], }...] 按时间序
    used = set()
    for rnd in range(args.rounds):
        order = rng.sample([r["gt"] for r in roster], args.speakers_per_round)
        for gt in order:
            pool = pools[gt]
            used_sec = sum(s["dur"] for p in plan for s in p["sents"] if p["speaker"] == gt)
            remain = args.target_speech_sec - used_sec
            rounds_left = args.rounds - rnd
            per_round_need = remain / rounds_left
            # 从候选窗口选时长最接近剩余需求的段，1~max_sents 段逼近 per_round_need
            avail = [s for s in pool if s["uid"] not in used]
            sents = []
            acc = 0.0
            while avail and len(sents) < args.max_sents:
                under = [s for s in avail if s["dur"] <= per_round_need - acc + 0.5]
                cand = (max(under, key=lambda s: s["dur"]) if under
                        else min(avail, key=lambda s: s["dur"]))
                sents.append(cand)
                avail.remove(cand)
                acc += cand["dur"]
                if acc >= per_round_need:
                    break
            if not sents:  # 池空也必须保底 1 段
                cand = next(s for s in pool if s["uid"] not in used)
                sents = [cand]
            for s in sents:
                used.add(s["uid"])
            plan.append({"speaker": gt, "sents": sents})
            pools[gt] = [s for s in pool if s["uid"] not in used]

    # 3) 铺时间轴：句间/换人/轮间停顿按参数；轮间长静默按预算凑整
    t = 0.0
    turns = []  # {speaker, sents:[{uid,src_st,dur,dst_st}]}
    change_gaps, sent_pauses = [], []
    n_per_round = args.speakers_per_round
    for i, p in enumerate(plan):
        if i % n_per_round > 0:
            g = rng.uniform(*chg_gap)
            change_gaps.append(g)
            t += g
        for j, s in enumerate(p["sents"]):
            if j > 0:
                sp = rng.uniform(*sent_pause)
                sent_pauses.append(sp)
                t += sp
            s["dst_st"] = t
            t += s["dur"]
        turns.append({"speaker": p["speaker"], "sents": p["sents"]})
    base_total = t
    n_round_pauses = args.rounds - 1
    need_sum = args.target_total_sec - base_total
    lo, hi = round_pause
    if n_round_pauses == 0:
        round_pauses = []
    elif need_sum <= 0:
        print(f"★告警：语音+气口已 {base_total:.1f}s 达到/超过目标 {args.target_total_sec:.0f}s，"
              f"轮间停顿取 0，实际总时长 {base_total:.1f}s")
        round_pauses = [0.0] * n_round_pauses
    else:
        if need_sum / n_round_pauses > hi or need_sum / n_round_pauses < lo:
            print(f"★告警：轮间停顿需求均值 {need_sum / n_round_pauses:.1f}s 超出 [{lo},{hi}]，"
                  f"按上限/下限取值，实际总时长将偏离目标")
        raw = [rng.uniform(lo, hi) for _ in range(n_round_pauses)]
        # 归一化到 need_sum 并夹取 [lo,hi] 重分配
        for _ in range(200):
            s = sum(raw)
            if s <= 0:
                raw = [need_sum / n_round_pauses] * n_round_pauses
                break
            raw = [min(hi, max(lo, x * need_sum / s)) for x in raw]
            if abs(sum(raw) - need_sum) < 0.01:
                break
        round_pauses = raw if abs(sum(raw) - need_sum) < 0.5 else [
            min(hi, max(lo, x)) for x in raw]

    # 4) 组 wav + rttm
    total = base_total + sum(round_pauses)
    out = np.zeros(int(round(total * 16000)), dtype=np.float32)
    rttm = []
    pause_acc = 0.0
    for idx, turn in enumerate(turns):
        if idx > 0 and idx % n_per_round == 0:
            pause_acc += round_pauses[idx // n_per_round - 1]
        st = turn["sents"][0]["dst_st"] + pause_acc
        cur = st
        for s in turn["sents"]:
            src = roster[[r["gt"] for r in roster].index(turn["speaker"])]["src"]
            wav = wavs[src]
            a = int(s["src_st"] * 16000)
            b = a + int(s["dur"] * 16000)
            d0 = int(cur * 16000)
            out[d0:d0 + (b - a)] = wav[a:b]
            rttm.append(f"SPEAKER {Path(args.out_prefix).name} 1 {cur:.4f} {s['dur']:.4f} "
                        f"<NA> <NA> {turn['speaker']} <NA> <NA>")
            cur += s["dur"]
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    sf.write(f"{prefix}.wav", out, 16000, subtype="PCM_16")
    Path(f"{prefix}.rttm").write_text("\n".join(rttm) + "\n")

    # 5) 自检统计
    per_spk = defaultdict(float)
    for line in rttm:
        p = line.split()
        per_spk[p[7]] += float(p[4])
    speech = sum(per_spk.values())
    changes = args.rounds * (n_per_round - 1)
    recipe = {
        "seed": args.seed, "roster_preset": args.roster, "rounds": args.rounds,
        "speakers_per_round": n_per_round,
        "target_speech_sec_per_person": args.target_speech_sec,
        "target_total_sec": args.target_total_sec,
        "seg_clamp": [args.seg_min, args.seg_max], "max_sents_per_round": args.max_sents,
        "change_gap_sec": chg_gap, "sentence_pause_sec": sent_pause,
        "round_pause_sec": round_pause,
        "round_pause_actual": [round(x, 2) for x in round_pauses],
        "actual_total_sec": round(total, 2),
        "speech_sec": round(speech, 2),
        "speech_ratio": round(speech / total, 4),
        "speaker_seconds": {k: round(v, 2) for k, v in sorted(per_spk.items())},
        "speaker_turns": {k: sum(1 for x in turns if x["speaker"] == k) for k in per_spk},
        "speaker_changes": changes,
        "change_gap_stats": {"n": len(change_gaps), "min": round(min(change_gaps), 3),
                             "mean": round(sum(change_gaps) / len(change_gaps), 3),
                             "max": round(max(change_gaps), 3)},
        "round_pause_stats": {"n": len(round_pauses), "min": round(min(round_pauses), 2),
                              "mean": round(sum(round_pauses) / len(round_pauses), 2),
                              "max": round(max(round_pauses), 2)},
        "sentences_total": sum(len(p["sents"]) for p in plan),
        "material_reuse_count": len(used) - len({s["uid"] for p in plan for s in p["sents"]}),
        "roster": roster,
        "note_gender": "3男3女优先：M会仅2男声，L会可用男声素材均<170s，实际2男4女",
        "segments": [{"speaker": t["speaker"], "start": round(
            float(r.split()[3]), 4), "dur": round(float(r.split()[4]), 4)}
            for t, r in zip(turns, rttm)],
    }
    Path(f"{prefix}.recipe.json").write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2))

    print(f"\n总时长 {total:.1f}s ({total/60:.1f}min) | 语音 {speech:.0f}s "
          f"占比 {speech/total:.1%} | 轮数 {args.rounds} | 换人 {changes}")
    for k, v in sorted(per_spk.items()):
        flag = "" if abs(v - args.target_speech_sec) <= args.speech_tol else "★"
        print(f"  {k:>8}: {v:6.1f}s ({recipe['speaker_turns'][k]} 轮次){flag}")
    print(f"换人气口 {recipe['change_gap_stats']} | 轮间停顿 {recipe['round_pause_stats']}")
    print(f"素材复用: {recipe['material_reuse_count']}（必须为 0）")


if __name__ == "__main__":
    main()
