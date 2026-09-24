"""降噪决赛 CER 对比：aishell4 TextGrid 真值 vs 三个会议的转写。

用法: ../.venv/bin/python scripts/denoise_final_cer.py
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import Meeting, Utterance, ReAnnotateUtterance  # noqa: E402

TG = Path("../eval/aishell4/test/TextGrid/M_R003S01C01.TextGrid").resolve()
IDS = {"原版": "13c9dc9ed192", "西工大GTCRN": "49385009f6fd", "阿里FRCRN": "aa2399b22487"}


def norm(t: str) -> str:
    t = re.sub(r"<sil>|</sil>", "", t or "")
    t = re.sub(r"[^一-鿿0-9a-zA-Z]", "", t)
    return t.lower()


def ref_text() -> str:
    """从 TextGrid 抽出全部参考文字（按出现顺序）。"""
    txt = TG.read_text(encoding="utf-8")
    parts = re.findall(r'text = "(.*?)"', txt)
    return norm("".join(parts))


def cer(ref: str, hyp: str) -> float:
    """字级编辑距离 / 参考长度。"""
    m, n = len(ref), len(hyp)
    if m == 0:
        return 0.0
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ri = ref[i - 1]
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ri != hyp[j - 1]))
        prev = cur
    return prev[n] / m


async def meeting_text(mid: str, re_version: bool) -> str:
    model = ReAnnotateUtterance if re_version else Utterance
    async with SessionLocal() as db:
        us = (await db.execute(select(model).where(model.meeting_id == mid).order_by(model.seq))).scalars().all()
    return norm("".join(u.text or "" for u in us))


async def main():
    ref = ref_text()
    print(f"参考真值字数: {len(ref)}  前80字: {ref[:80]}")
    print(f"{'版本':12s} {'CER(会中版)':>10s} {'CER(终版)':>10s}")
    for name, mid in IDS.items():
        mid_txt = await meeting_text(mid, re_version=False)
        re_txt = await meeting_text(mid, re_version=True)
        cer_mid = cer(ref, mid_txt) * 100 if mid_txt else float("nan")
        cer_re = cer(ref, re_txt) * 100 if re_txt else float("nan")
        print(f"{name:12s} {cer_mid:9.1f}% {cer_re:9.1f}%  (字数 会中{len(mid_txt)}/终版{len(re_txt)})")


if __name__ == "__main__":
    asyncio.run(main())
