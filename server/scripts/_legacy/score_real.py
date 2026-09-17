"""给真实管线重放的基准块算 DER 并写属性。用法: score_real.py <meeting_id> <rttm_meeting> [title]"""
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # server/ 根，app 包在此
from aishell4_der import frame_speaker  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import Meeting, ReAnnotateUtterance, Utterance  # noqa: E402

T = 300
mid, rttm_mid = sys.argv[1], sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else f"【基准】{rttm_mid} 真实管线"

ref = []
for line in Path(f"../eval/aishell4/test/TextGrid/{rttm_mid}.rttm").read_text().splitlines():
    p = line.split()
    if p[0] == "SPEAKER":
        st, dur = float(p[3]), float(p[4])
        if st < T:
            ref.append((p[7], st, min(dur, T - st)))
rl = frame_speaker(ref, T)


async def main() -> None:
    async with SessionLocal() as db:
        re_ = (await db.execute(select(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == mid))).scalars().all()
        uts = (await db.execute(select(Utterance).where(Utterance.meeting_id == mid))).scalars().all()

    def acc(rows):
        hl = frame_speaker([(u.speaker, u.start_ms / 1000, (u.end_ms - u.start_ms) / 1000) for u in rows], T)
        n = min(len(rl), len(hl))
        ok = sum(1 for r, h in zip(rl[:n], hl[:n]) if r and r == h)
        tot = sum(1 for r in rl[:n] if r)
        return ok, tot, hl

    ok, tot, _ = acc(uts)
    print("实时: 帧命中 %d/%d (%.1f%%)" % (ok, tot, 100 * ok / max(tot, 1)))
    ok2, tot2, hl = acc(re_)
    print("会后: 帧命中 %d/%d (%.1f%%)" % (ok2, tot2, 100 * ok2 / max(tot2, 1)))

    errors = []
    for spk, a, b in ref:
        seg = hl[int(a / 0.01):int(b / 0.01) + 1]
        if len(seg) == 0:
            continue
        vals, cnts = np.unique(seg, return_counts=True)
        top = sorted(zip(vals, cnts), key=lambda x: -x[1])[0]
        cov = sum(c for v, c in zip(vals, cnts) if v) / len(seg)
        if top[0] != spk or cov < 0.5:
            errors.append({"a": round(a, 1), "b": round(b, 1), "ref": spk, "hyp": top[0] or "(无名)"})
    print("会后错段 %d/%d" % (len(errors), len(ref)))

    attrs = [
        {"key": "基准DER(对rttm标注)", "value": "%.1f%%" % (100 * ok2 / max(tot2, 1))},
        {"key": "实时帧命中", "value": "%.1f%%" % (100 * ok / max(tot, 1))},
        {"key": "实时句/会后段", "value": "%d / %d" % (len(uts), len(re_))},
        {"key": "错段", "value": "%d/%d" % (len(errors), len(ref))},
    ]
    for e in errors[:12]:
        attrs.append({"key": "✗ %.0f-%.0fs" % (e["a"], e["b"]),
                      "value": "真值 %s → 判 %s" % (e["ref"], e["hyp"])})
    async with SessionLocal() as db:
        m = await db.get(Meeting, mid)
        m.title = title
        m.attributes = json.dumps(attrs, ensure_ascii=False)
        await db.commit()
    print("属性已写:", mid)


asyncio.run(main())
