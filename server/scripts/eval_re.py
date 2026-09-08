"""会后版说话人标注评测：拿用户人工标注的 ground truth 逐句对分。

用法: python scripts/eval_re.py <meeting_id>
GT 按 meeting_id 分键：{"meeting_id": [...]}，键 "common" 为所有会议通用。
text_contains: 句子原文包含此片段即匹配；speaker: 真实说话人。
"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ReAnnotateUtterance, Utterance

# 用户人工标注。
# common：de61a50cde94 / 8dfaa4aaa228 同一段二人对话录音（重放测试）。
GROUND_TRUTH: dict[str, list] = {
    "common": [
        {"text_contains": "好难搞啊", "speaker": "蒋泽鹏"},
        {"text_contains": "没啥用", "speaker": "admin"},
        {"text_contains": "你自己的有录入进去", "speaker": "蒋泽鹏"},
        {"text_contains": "我自己能录入进去了", "speaker": "admin"},
        {"text_contains": "什么发言人", "speaker": "蒋泽鹏"},  # 「你自己录入进去，是问什么发言人吗」
        {"text_contains": "再研究一下", "speaker": "admin"},
        {"text_contains": "我的应该有话呀", "speaker": "admin"},  # 用户 2026-09-08 点名：23.0s
        {"text_contains": "对呀。", "speaker": "admin"},           # 用户 2026-09-08 点名：21.9s「对呀」
    ],
    # ddaed75eb0e3 重放测试（用户 2026-09-08 标注）
    "ddaed75eb0e3": [
        {"text_contains": "就这样的一个问题", "speaker": "admin"},   # 16s-17s，会后版判蒋泽鹏
        {"text_contains": "那就不用做算了", "speaker": "蒋泽鹏"},     # 45s-50s 主体；「说了一下是吧」是 admin（句内切分，标主体）
        {"text_contains": "物理输入参会人数", "speaker": "蒋泽鹏"},   # 35s-40s 主体；「哎，对，我现在就是输入呀」是 admin
    ],
}


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

    gt_list = GROUND_TRUTH.get(meeting_id, []) + GROUND_TRUTH.get("common", []) \
        if meeting_id not in GROUND_TRUTH else GROUND_TRUTH[meeting_id]

    def score(rows, label: str) -> None:
        print(f"== {label} ==")
        ok = miss = 0
        used = set()
        for gt in gt_list:
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
