"""会后版说话人标注评测：拿用户人工标注的 ground truth 逐句对分。

用法: python scripts/eval_re.py <meeting_id>
GT 维护在下方 GROUND_TRUTH：按文字前缀匹配（会后版沿用实时原文，跨会议可复用）。
"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ReAnnotateUtterance, Utterance

# 用户人工标注（de61a50cde94 / 8dfaa4aaa228 同一段二人对话录音）。
# text_contains: 句子原文包含此片段即匹配；speaker: 真实说话人。
GROUND_TRUTH = [
    {"text_contains": "好难搞啊", "speaker": "蒋泽鹏"},
    {"text_contains": "没啥用", "speaker": "admin"},
    {"text_contains": "你自己的有录入进去", "speaker": "蒋泽鹏"},
    {"text_contains": "我自己能录入进去了", "speaker": "admin"},
    {"text_contains": "什么发言人", "speaker": "蒋泽鹏"},  # 「你自己录入进去，是问什么发言人吗」
    {"text_contains": "再研究一下", "speaker": "admin"},
    {"text_contains": "我的应该有话呀", "speaker": "admin"},  # 用户 2026-09-08 点名：23.0s
    {"text_contains": "对呀。", "speaker": "admin"},           # 用户 2026-09-08 点名：21.9s「对呀」
]


async def main(meeting_id: str) -> None:
    async with SessionLocal() as db:
        re_rows = (await db.execute(
            select(ReAnnotateUtterance)
            .where(ReAnnotateUtterance.meeting_id == meeting_id)
            .order_by(ReAnnotateUtterance.seq)
        )).scalars().all()
        rt_rows = (await db.execute(
            select(Utterance)
            .where(Utterance.meeting_id == meeting_id)
            .order_by(Utterance.seq)
        )).scalars().all()

    def score(rows, label: str) -> None:
        print(f"== {label} ==")
        ok = miss = 0
        used = set()
        for gt in GROUND_TRUTH:
            hit = next((u for u in rows if gt["text_contains"] in (u.text or "") and u.seq not in used), None)
            if hit is None:
                print(f"  ? 未找到句子 [{gt['text_contains']}]")
                continue
            used.add(hit.seq)
            good = hit.speaker == gt["speaker"]
            ok += good
            miss += not good
            mark = "OK " if good else "BAD"
            print(f"  {mark} [{gt['text_contains']}] 真实={gt['speaker']} 判={hit.speaker}")
        total = ok + miss
        print(f"  小计: {ok}/{ok + miss} 对" if total else "  无样本")

    score(re_rows, "会后版 utterances_re")
    score(rt_rows, "实时版 utterances（参照）")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "8dfaa4aaa228"))
