"""会末版（final）管道：重标注终版 → 波次 LLM → 会末最终总结。

双版本架构（2026-09-28 用户拍板）：实时版（live）与会末版（final）是独立管道、互不
纠正，只复用声纹库/视觉/录音。会末版以整场录音重跑（reannotate）的 utterances_re
为唯一原文源：

    reannotate（降噪→3D-Speaker→GPU 认人+转写，现有函数复用）
    → utterances_re 按 120s 固定窗分波（final_wave_sec，不对齐 OCR/批次边界）
    → 逐波调与实时版同一套波次 LLM（enhancer._llm_enhance + prompts 同一套）：
      输入原文 = utterances_re 行（说话人: 文本）、上下文 = 前 5 波会末段纪要、
      VisualNote 按 [波.start, 波.end] 查出拼进 ocr_text（复用不重的 OCR）；
      纠错写回 utterances_re.text，段纪要落 Summary(scope='final', seq=1..N)
    → 全部会末段纪要归约出会末最终总结（extract_final_attributes 复用）：
      Summary(scope='final', seq=0, action_items=结构化待办) + meeting 元信息

失败语义与实时版一致（用户原则）：某波 LLM 失败记日志跳过（该波无段纪要、原文留
草稿），不重试不累积补做；reannotate 失败 = 会末版整体终止（无原文源，绝不回退
旧聚类）。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, ReAnnotateUtterance, Summary, VisualNote
from app.llm import client as llm_client

if TYPE_CHECKING:
    from app.core.session import MeetingSession

logger = logging.getLogger(__name__)


async def run_final_pass(session: MeetingSession) -> bool:
    """会末版收尾主入口（session._finalize 按 version_mode 调用）。返回是否产出终版。

    广播节奏：final_progress(reannotate,5) → reannotated → final_progress(waves,30..85)
    → 每波 summary(scope=final) → final_progress(summary,85) → summary(scope=final,seq=0)
    → final_progress(done,100) → final_done。
    """
    from app.core.enhancer import BatchEnhancer, _llm_enhance
    from app.core.reannotate import reannotate

    meeting_id = session.meeting_id
    broadcast = session.broadcast

    # 1) 全场重跑 → utterances_re（复用现有 reannotate；失败=会末版整体失败，不回退）
    await broadcast({"type": "final_progress", "stage": "reannotate", "pct": 5})
    if not await reannotate(meeting_id, bytes(session._audio)):
        logger.error("final pass %s: reannotate failed, 会末版终止", meeting_id)
        return False
    await broadcast({"type": "reannotated", "meeting_id": meeting_id})

    # 2.5) 视觉落库收割：timer 转后台的 OCR 任务先跑完，再把缓冲剩余帧落库——
    # 否则下面波次 _load_visual_notes 按时间窗查不到（2026-09-29 竞态修复）
    if settings.visual_enabled and session.visual_app:
        await session.wait_visual_drained()

    # 2) 读 utterances_re（会末版唯一原文源；说话人已 keep_anon 区分个体）
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ReAnnotateUtterance)
            .where(ReAnnotateUtterance.meeting_id == meeting_id)
            .order_by(ReAnnotateUtterance.seq)
        )).scalars().all()
        utts = [
            {"id": r.id, "seq": r.seq, "speaker": r.speaker or "发言人",
             "text": r.text or "", "start_ms": r.start_ms, "end_ms": r.end_ms}
            for r in rows
        ]
    if not utts:
        logger.error("final pass %s: utterances_re 为空，会末版终止", meeting_id)
        return False

    # 3) 会末波次：120s 固定窗分波（按句首时间落窗；固定窗简单可靠，
    #    VisualNote 按时间范围关联不需要边界对齐）
    await broadcast({"type": "final_progress", "stage": "waves", "pct": 30})
    wave_ms = max(1, int(settings.final_wave_sec * 1000))
    waves: dict[int, list[dict]] = {}
    for u in utts:
        waves.setdefault(u["start_ms"] // wave_ms, []).append(u)
    wave_keys = sorted(waves)

    segments: list[dict] = []              # 会末段纪要（append-only：上下文 + 最终总结输入）
    all_actions: list[dict] = []           # 各波 action_items 累积（最终总结待办清单原料）
    prev_lines: list[tuple[str, str]] = []  # 上一波原文（稀疏订正输入，同实时版契约）
    prev_items: list[dict] = []            # 上一波行（i → 行 id，供 prev_corrections 写回）
    n_ctx = settings.summary_context_segments

    for wi, wk in enumerate(wave_keys):
        items = waves[wk]
        cur_raw = [(u["speaker"], u["text"]) for u in items]
        w_start = min(u["start_ms"] for u in items)
        w_end = max(u["end_ms"] for u in items)
        ocr_text = await _load_visual_notes(meeting_id, w_start, w_end)

        # 上下文滑动窗口与实时版同规则：前 N 波（排除最后一波——它单独给，因为要订正）
        ctx = segments[-(n_ctx + 1):-1] if len(segments) > 1 else []
        ctx_summaries = [BatchEnhancer._fmt_segment(s) for s in ctx]
        prev_summary = segments[-1]["content"] if segments else ""

        result = await _llm_enhance(ctx_summaries, prev_summary, prev_lines, cur_raw,
                                    ocr_text=ocr_text)
        pct = 30 + int(55 * (wi + 1) / len(wave_keys))
        if result is None:
            # 失败不兜底（同实时版原则）：记日志跳过，本波无段纪要、文字留草稿
            logger.error("final pass %s: 会末波 %d/%d LLM 失败，跳过",
                         meeting_id, wi + 1, len(wave_keys))
            prev_lines = cur_raw
            prev_items = items
            await broadcast({"type": "final_progress", "stage": "waves", "pct": pct})
            continue

        # 纠错写回 utterances_re（按显式行号 i 对齐，与实时版同一套契约：
        # 模型漏行只影响漏掉的行，越界/非法行号忽略，不整波失败）
        lines = result["lines"]
        n_cur = len(cur_raw)
        updates: list[tuple[int, str, str]] = []  # (row_id, speaker, text)
        corrected_cur: list[tuple[str, str]] = list(cur_raw)
        for d in lines:
            i = d.get("i")
            if not isinstance(i, int) or not (0 <= i < n_cur):
                continue
            spk, txt = str(d.get("speaker", "")).strip(), str(d.get("text", "")).strip()
            if not txt:
                continue
            u = items[i]
            if spk:
                u["speaker"] = spk
            u["text"] = txt
            corrected_cur[i] = (u["speaker"], txt)
            updates.append((u["id"], u["speaker"], txt))

        # 上一波稀疏订正（按 i 指回上一波原文，写回 utterances_re）
        n_prev_applied = 0
        for c in result["prev_corrections"]:
            i = c.get("i")
            if not isinstance(i, int) or not (0 <= i < len(prev_items)):
                continue
            spk, txt = str(c.get("speaker", "")).strip(), str(c.get("text", "")).strip()
            if not txt:
                continue
            pu = prev_items[i]
            if spk:
                pu["speaker"] = spk
            pu["text"] = txt
            updates.append((pu["id"], pu["speaker"], txt))
            n_prev_applied += 1

        if updates:
            async with SessionLocal() as db:
                for row_id, spk, txt in updates:
                    await db.execute(
                        ReAnnotateUtterance.__table__.update()
                        .where(ReAnnotateUtterance.id == row_id)
                        .values(speaker=spk, text=txt)
                    )
                await db.commit()

        # 订正上一波段纪要（LLM 判断无需改动时原样返回，此时不落库不广播）
        prev_sum = (result.get("prev_summary") or "").strip()
        if prev_sum and segments and prev_sum != segments[-1]["content"]:
            segments[-1]["content"] = prev_sum
            async with SessionLocal() as db:
                await db.execute(
                    Summary.__table__.update()
                    .where(Summary.meeting_id == meeting_id)
                    .where(Summary.seq == segments[-1]["seq"])
                    .where(Summary.scope == "final")
                    .values(content=prev_sum, title=segments[-1]["title"])
                )
                await db.commit()
            await _broadcast_segment(session, segments[-1], final=False)

        # 本波段纪要：append 落库 + 广播（seq 1..N，scope='final' 与实时版区分）
        summary = (result.get("summary") or "").strip()
        actions = result.get("action_items") or []
        all_actions.extend(actions)
        if summary:
            seg = {
                "seq": len(segments) + 1,
                "title": (result.get("title") or "").strip() or f"第{len(segments) + 1}段",
                "content": summary,
                "start_ms": w_start,
                "end_ms": w_end,
                "window_text": "\n".join(f"{s}: {t}" for s, t in cur_raw),
                "action_items": actions,
            }
            segments.append(seg)
            await _persist_segment(meeting_id, seg)
            await _broadcast_segment(session, seg, final=False)

        prev_lines = corrected_cur
        prev_items = items
        await broadcast({"type": "final_progress", "stage": "waves", "pct": pct})
        logger.info("final pass %s: 会末波 %d/%d 完成（%d 句，订正上一波 %d 句，"
                    "累计段纪要 %d 段）",
                    meeting_id, wi + 1, len(wave_keys), n_cur, n_prev_applied,
                    len(segments))

    # 4) 会末最终总结：输入 = 全部会末段纪要拼接（不碰原始全文，同实时版归约原则）；
    #    待办清单 = LLM 归约 + 各波 action_items 拼接，按 quote 去重
    await broadcast({"type": "final_progress", "stage": "summary", "pct": 85})
    segments_text = "\n\n".join(BatchEnhancer._fmt_segment(s) for s in segments)
    result = await llm_client.extract_final_attributes(segments_text)
    final_actions = llm_client.dedup_action_items(
        (result.get("action_items") or []) + all_actions)

    final_title = result["title"]
    # 重放测试会议：标题保留「重放测试」标记（与实时版终版同一规则）
    if (session.replay_of or session.client_replay) and final_title \
            and "重放测试" not in final_title:
        final_title = f"{final_title}（重放测试）"
    # LLM 失败兜底：直接用段纪要拼接当总结（信息全在，只是没归约）
    final_summary = result["summary"] or segments_text
    final_tags = result["tags"][:settings.final_tags_count]

    if final_summary:
        async with SessionLocal() as db:
            db.add(Summary(
                meeting_id=meeting_id,
                seq=0,                        # 终版专用号（同实时版约定）
                scope="final",
                window_text="",               # 终版输入是段纪要，不是原文窗口
                content=final_summary,
                action_items=json.dumps(final_actions, ensure_ascii=False),
                attributes="[]",
                title=final_title or "会末总结",
            ))
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                # 元信息落库规则（2026-09-28 用户拍板）：final-only 没有 live 值 → 直接落；
                # both 模式 live 已落 title/tags → final 不覆盖；attributes 按 key 合并
                # （live 优先，同实时版「滚动版优先」语义）；action_items 以 final 为准
                live_done = session.version_mode == "both"
                if final_title and not live_done:
                    meeting.title = final_title
                if final_tags and not live_done:
                    meeting.tags = json.dumps(final_tags, ensure_ascii=False)
                final_attrs = {
                    str(a["key"]): str(a.get("value", ""))
                    for a in result["attributes"]
                    if isinstance(a, dict) and a.get("key")
                }
                if live_done:
                    existing = {
                        str(kv["key"]): str(kv.get("value", ""))
                        for kv in json.loads(meeting.attributes or "[]")
                        if isinstance(kv, dict) and kv.get("key")
                    }
                    for k, v in final_attrs.items():
                        existing.setdefault(k, v)
                    meeting.attributes = json.dumps(
                        [{"key": k, "value": v} for k, v in existing.items()],
                        ensure_ascii=False)
                elif final_attrs:
                    meeting.attributes = json.dumps(
                        [{"key": k, "value": v} for k, v in final_attrs.items()],
                        ensure_ascii=False)
                meeting.action_items = json.dumps(final_actions, ensure_ascii=False)
            await db.commit()
        await broadcast({
            "type": "summary",
            "seq": 0,
            "scope": "final",
            "final": True,
            "title": final_title or "会末总结",
            "points": [p.lstrip("· ").strip() for p in final_summary.splitlines()
                       if p.strip()],
            "action_items": final_actions,
            "attributes": result["attributes"],
        })

    await broadcast({"type": "final_progress", "stage": "done", "pct": 100})
    await broadcast({"type": "final_done", "meeting_id": meeting_id})
    logger.info("final pass %s done（%d 波，%d 段会末纪要）",
                meeting_id, len(wave_keys), len(segments))
    return True


async def _load_visual_notes(meeting_id: str, start_ms: int, end_ms: int) -> str:
    """按时间范围查 VisualNote（与波有重叠即取，按时间排序拼接）。

    会末版复用实时版波次/final 定时任务的 OCR 结果，不重跑 OCR（双版本只复用视觉）。
    """
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(VisualNote)
            .where(VisualNote.meeting_id == meeting_id)
            .where(VisualNote.start_ms <= end_ms)
            .where(VisualNote.end_ms >= start_ms)
            .order_by(VisualNote.start_ms)
        )).scalars().all()
    return "\n\n".join(r.text for r in rows if r.text)


async def _persist_segment(meeting_id: str, seg: dict) -> None:
    async with SessionLocal() as db:
        db.add(Summary(
            meeting_id=meeting_id,
            seq=seg["seq"],
            scope="final",
            window_text=seg["window_text"],
            content=seg["content"],
            action_items=json.dumps(seg.get("action_items") or [], ensure_ascii=False),
            attributes="[]",
            title=seg["title"],
        ))
        await db.commit()


async def _broadcast_segment(session: MeetingSession, seg: dict, final: bool) -> None:
    points = [ln.lstrip("· ").strip() for ln in seg["content"].splitlines() if ln.strip()]
    await session.broadcast({
        "type": "summary",
        "seq": seg["seq"],
        "scope": "final",            # 前端按 scope 分 tab（2026-09-28 双版本架构）
        "final": final,
        "title": seg["title"],
        "points": points,
        "action_items": seg.get("action_items") or [],
        "attributes": [],
    })
