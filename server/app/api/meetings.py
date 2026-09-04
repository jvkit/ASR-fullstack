"""REST API：会议查询与纪要导出（供 OA / 前端调用）。"""

import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Meeting, Summary, Utterance

router = APIRouter(prefix="/api", tags=["meetings"])


@router.get("/meetings")
async def list_meetings(limit: int = 50) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(Meeting).order_by(Meeting.started_at.desc()).limit(limit))).scalars().all()
        return [
            {
                "id": m.id,
                "title": m.title,
                "recorder": m.recorder,
                "status": m.status,
                "started_at": m.started_at.isoformat() if m.started_at else None,
                "ended_at": m.ended_at.isoformat() if m.ended_at else None,
            }
            for m in rows
        ]


@router.get("/meetings/{meeting_id}")
async def meeting_detail(meeting_id: str) -> dict:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
        utts = (await db.execute(
            select(Utterance).where(Utterance.meeting_id == meeting_id).order_by(Utterance.seq)
        )).scalars().all()
        return {
            "id": meeting.id,
            "title": meeting.title,
            "recorder": meeting.recorder,
            "status": meeting.status,
            "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
            "utterances": [
                {"seq": u.seq, "speaker": u.speaker, "text": u.text,
                 "start_ms": u.start_ms, "end_ms": u.end_ms}
                for u in utts
            ],
        }


@router.get("/meetings/{meeting_id}/summaries")
async def meeting_summaries(meeting_id: str) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Summary).where(Summary.meeting_id == meeting_id).order_by(Summary.seq)
        )).scalars().all()
        return [
            {
                "seq": s.seq,
                "title": s.title,
                "content": s.content,
                "action_items": json.loads(s.action_items or "[]"),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]


@router.get("/meetings/{meeting_id}/report")
async def meeting_report(meeting_id: str) -> dict:
    """完整纪要：全部要点按序拼接 + 待办汇总，Markdown 格式（OA 导出用）。"""
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
        summaries = (await db.execute(
            select(Summary).where(Summary.meeting_id == meeting_id).order_by(Summary.seq)
        )).scalars().all()

        parts = [f"# {meeting.title}\n"]
        all_actions: list[dict] = []
        for s in summaries:
            if s.title:
                parts.append(f"## {s.seq}. {s.title}\n")
            parts.append(s.content + "\n")
            all_actions.extend(json.loads(s.action_items or "[]"))

        if all_actions:
            parts.append("## 待办汇总\n")
            for a in all_actions:
                who = a.get("who", "?")
                what = a.get("what", "")
                due = a.get("due", "")
                status = a.get("status", "")
                parts.append(f"- [ ] **{who}**：{what}（{due}）{status}")

        return {"id": meeting.id, "title": meeting.title, "markdown": "\n".join(parts)}
