"""重放会议终版 vs AISHELL-4 rttm 标注的 DER 评分（帧级，10ms）。

与 aishell4_der.py（直驱 GPU 端点）不同：本脚本评的是「走完整生产管线后的
本地库 utterances_re」，衡量用户真实看到的结果。

说话人映射用最优置换（生产声纹库的注册名与 rttm 说话人 id 无对应关系，
最优映射把命名差异剥离，只度量「分人」本身的质量；生产命名场景另看映射表输出）。

DER = (漏检 miss + 误检 FA + 错标 confusion) / 总 GT 语音帧

用法（项目根目录）:
    python server/scripts/replay_der.py <meeting_id> --rttm eval/aishell4/test/TextGrid/S_R003S01C01.rttm [--max-sec 300]

产物：eval/<meeting_id>/replay_der.txt（DER 明细 + 最优映射 + 每标签帧数）
"""
from __future__ import annotations

import argparse
import itertools
import sqlite3
from collections import Counter
from pathlib import Path

FRAME_MS = 10
ROOT = Path(__file__).resolve().parents[2]  # meeting-asr/


def load_rttm(path: Path, max_sec: float | None) -> list[tuple[str, int, int]]:
    """rttm -> [(spk, start_ms, end_ms)]，可选只取前 max_sec 秒。"""
    out = []
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            spk = p[7]
            s = float(p[3])
            e = s + float(p[4])
            if max_sec and s >= max_sec:
                continue
            if max_sec:
                e = min(e, max_sec)
            out.append((spk, int(s * 1000), int(e * 1000)))
    return out


def load_canonical(rttm: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """AISHELL-4 同人别名合并（2026-09-15 用户确认：编号前缀=同一个人，
    同一人可在不同会议注册不同别名，如 001-M == 001-今天把大、
    010-老师 == 002-M）。

    预测名先经 enroll_map（eval/<rttm_stem>/enroll_map.json）逆映射回 rttm id，
    再取前导数字为 canonical id；GT 直接取前导数字。
    返回 (canonical 函数, 合并明细)。"""
    import json
    import re

    name_to_rttm: dict[str, str] = {}
    mpath = ROOT / "eval" / rttm.stem / "enroll_map.json"
    if mpath.exists():
        m = json.loads(mpath.read_text(encoding="utf-8"))
        name_to_rttm = {v: k for k, v in m.get("rttm_to_name", {}).items()}

    merged: dict[str, set[str]] = {}

    def canon(x: str) -> str:
        rid = name_to_rttm.get(x, x)
        mm = re.match(r"(\d+)", rid)
        cid = mm.group(1) if mm else rid
        merged.setdefault(cid, set()).add(x)
        return cid

    return canon, {k: sorted(v) for k, v in merged.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("meeting_id")
    ap.add_argument("--rttm", required=True)
    ap.add_argument("--max-sec", type=float, default=None, help="只评前 N 秒（切片重放用）")
    args = ap.parse_args()

    db = sqlite3.connect(ROOT / "server" / "meeting.db")
    segs = db.execute(
        "select speaker, start_ms, end_ms, text from utterances_re where meeting_id=? order by seq",
        (args.meeting_id,),
    ).fetchall()
    if not segs:
        raise SystemExit(f"utterances_re 为空: {args.meeting_id}")
    meeting_ms = max(e for _, _, e, _ in segs)

    gt = load_rttm(Path(args.rttm), args.max_sec)
    canon, merged = load_canonical(Path(args.rttm))
    total_ms = min(meeting_ms, int(args.max_sec * 1000)) if args.max_sec else meeting_ms
    n = total_ms // FRAME_MS

    # GT 帧：哪个 GT 说话人在说（重叠取第一个；AISHELL-4 单人标注无重叠）
    gt_frames = [None] * n
    for spk, s, e in gt:
        for i in range(max(0, s // FRAME_MS), min(n, (e + FRAME_MS - 1) // FRAME_MS)):
            gt_frames[i] = canon(spk)

    # 预测帧：子段说话人（同一人多别名先合并再计混淆）
    pred_frames = [None] * n
    for spk, s, e, _ in segs:
        for i in range(max(0, s // FRAME_MS), min(n, (e + FRAME_MS - 1) // FRAME_MS)):
            pred_frames[i] = canon(spk) if spk else None

    gt_spks = sorted({s for s in gt_frames if s})
    pred_labels = sorted({s for s in pred_frames if s})

    # 混淆计数（pred_label × gt_spk 帧数），最优映射只在这张小表上算
    conf = Counter()
    miss_frames = fa_frames = 0
    for g, p in zip(gt_frames, pred_frames):
        if g and p:
            conf[(p, g)] += 1
        elif g:
            miss_frames += 1
        elif p:
            fa_frames += 1
    gt_total = sum(1 for g in gt_frames if g)

    # 最优映射：pred_label × gt_spk 的判对帧矩阵上做最大权匹配（Hungarian），
    # 每个 GT 最多映射一个 pred，多余 pred 保持未映射。修过 bug：旧版
    # permutations(gt_spks, k) 只会映射字典序前 k 个 pred 标签，
    # 陌生人数量 > GT 时（盲测常态）大簇永远轮不到映射，DER 虚高。
    try:
        from scipy.optimize import linear_sum_assignment
        have_scipy = True
    except Exception:
        have_scipy = False

    def best_mapping() -> dict[str, str]:
        if have_scipy and pred_labels and gt_spks:
            import numpy as np
            w = np.zeros((len(pred_labels), len(gt_spks)))
            for i, p in enumerate(pred_labels):
                for j, g in enumerate(gt_spks):
                    w[i, j] = conf.get((p, g), 0)
            ri, cj = linear_sum_assignment(-w)
            return {pred_labels[i]: gt_spks[j] for i, j in zip(ri, cj) if w[i, j] > 0}
        # 无 scipy 的回退：按判对帧数降序贪心（GT/pred 一对一）
        m: dict[str, str] = {}
        used_g: set[str] = set()
        for (p, g), cnt in sorted(conf.items(), key=lambda kv: -kv[1]):
            if p not in m and g not in used_g and cnt > 0:
                m[p] = g
                used_g.add(g)
        return m

    mapping = best_mapping()
    err = gt_total - sum(c for (p, g), c in conf.items() if mapping.get(p) == g)
    # err = GT 语音帧中未判对的 = miss + confusion（FA 不在 GT 语音里，不计入 err）
    conf_frames = err - miss_frames
    fa = fa_frames

    der = (miss_frames + fa + conf_frames) / max(1, gt_total)
    lines = [
        f"meeting: {args.meeting_id}",
        f"rttm: {args.rttm} (前 {args.max_sec or total_ms/1000:.0f}s)",
        f"GT 语音: {gt_total*FRAME_MS/1000:.0f}s | 终版子段: {len(segs)}",
        f"DER = {der*100:.1f}%  (miss {miss_frames*FRAME_MS/1000:.0f}s + FA {fa_frames*FRAME_MS/1000:.0f}s + conf {conf_frames*FRAME_MS/1000:.0f}s) / {gt_total*FRAME_MS/1000:.0f}s",
        f"最优映射: {mapping}",
        f"别名合并: {merged}",
        f"GT 说话人: {gt_spks}",
        f"预测标签: {pred_labels}",
        "",
        "每预测标签帧数（映射后判对的/总共）:",
    ]
    for p in pred_labels:
        tot = sum(c for (pp, _), c in conf.items() if pp == p)
        ok = sum(c for (pp, g), c in conf.items() if pp == p and mapping.get(p) == g)
        lines.append(f"  {p or '(空)'}: {ok}/{tot} 帧对 ({mapping.get(p, '未映射')})")
    lines.append("")
    lines.append("终版前 15 行:")
    for spk, s, e, t in segs[:15]:
        lines.append(f"  [{s//1000:>3}s-{e//1000:>3}s] {spk or '-'}: {t[:36]}")

    out = ROOT / "eval" / args.meeting_id / "replay_der.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"\n明细已写入 {out}")


if __name__ == "__main__":
    main()
