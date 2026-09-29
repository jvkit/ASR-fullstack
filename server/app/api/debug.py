"""调试 API：把会议处理黑盒全部摊开。

所有中间产物（切段、音频、帧图、LLM 输入输出、事件时间线）都在这里查询。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import (
    DiarSegment,
    Meeting,
    MeetingEvent,
    ReAnnotateUtterance,
    SummaryLlmLog,
    Utterance,
    VisualNote,
)

router = APIRouter(prefix="/api", tags=["debug"])


def _data_root() -> Path:
    return Path(settings.recordings_dir).parent / "data" / "debug"


@router.get("/meetings/{meeting_id}/debug")
async def debug_overview(meeting_id: str) -> dict:
    """调试总览：会议元数据 + 事件时间线 + 产物计数。"""
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
        events = (await db.execute(
            select(MeetingEvent)
            .where(MeetingEvent.meeting_id == meeting_id)
            .order_by(MeetingEvent.created_at)
        )).scalars().all()
        counts = {
            "utterances": (await db.execute(
                select(Utterance).where(Utterance.meeting_id == meeting_id)
            )).scalars().all().__len__(),
            "utterances_re": (await db.execute(
                select(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == meeting_id)
            )).scalars().all().__len__(),
            "diar_segments": (await db.execute(
                select(DiarSegment).where(DiarSegment.meeting_id == meeting_id)
            )).scalars().all().__len__(),
            "visual_notes": (await db.execute(
                select(VisualNote).where(VisualNote.meeting_id == meeting_id)
            )).scalars().all().__len__(),
            "summary_llm_logs": (await db.execute(
                select(SummaryLlmLog).where(SummaryLlmLog.meeting_id == meeting_id)
            )).scalars().all().__len__(),
        }
        return {
            "id": meeting.id,
            "title": meeting.title,
            "status": meeting.status,
            "version_mode": meeting.version_mode,
            "source_mode": meeting.source_mode,
            "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
            "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
            "audio_raw_path": meeting.audio_raw_path,
            "audio_denoised_path": meeting.audio_denoised_path,
            "recordings_dir": settings.recordings_dir,
            "counts": counts,
            "events": [
                {
                    "id": e.id,
                    "stage": e.stage,
                    "detail": json.loads(e.detail or "{}"),
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
        }


@router.get("/meetings/{meeting_id}/debug/diar_segments")
async def debug_diar_segments(meeting_id: str, source: str | None = None) -> list[dict]:
    """说话人分离原始切段。source=live|final。"""
    async with SessionLocal() as db:
        q = select(DiarSegment).where(DiarSegment.meeting_id == meeting_id)
        if source in ("live", "final"):
            q = q.where(DiarSegment.source == source)
        rows = (await db.execute(q.order_by(DiarSegment.start_ms))).scalars().all()
        return [
            {
                "id": r.id,
                "start_ms": r.start_ms,
                "end_ms": r.end_ms,
                "cluster_label": r.cluster_label,
                "matched_speaker": r.matched_speaker,
                "score": r.score,
                "audio_path": r.audio_path,
                "source": r.source,
            }
            for r in rows
        ]


@router.get("/meetings/{meeting_id}/debug/utterances")
async def debug_utterances(meeting_id: str, scope: str = "live") -> list[dict]:
    """句子原文/纠错后/音频路径。scope=live|final。"""
    async with SessionLocal() as db:
        if scope == "final":
            model = ReAnnotateUtterance
        else:
            model = Utterance
        rows = (await db.execute(
            select(model).where(model.meeting_id == meeting_id).order_by(model.seq)
        )).scalars().all()
        return [
            {
                "id": r.id,
                "seq": r.seq,
                "speaker": r.speaker,
                "raw_text": r.raw_text,
                "corrected_text": r.corrected_text,
                "text": r.text,
                "start_ms": r.start_ms,
                "end_ms": r.end_ms,
                "audio_path": r.audio_path,
                "diar_score": r.diar_score,
            }
            for r in rows
        ]


@router.get("/meetings/{meeting_id}/debug/visual_notes")
async def debug_visual_notes(meeting_id: str) -> list[dict]:
    """视觉 OCR 组：文本 + 帧图路径。"""
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(VisualNote).where(VisualNote.meeting_id == meeting_id)
            .order_by(VisualNote.start_ms)
        )).scalars().all()
        return [
            {
                "id": r.id,
                "start_ms": r.start_ms,
                "end_ms": r.end_ms,
                "text": r.text,
                "frame_paths": json.loads(r.frame_paths or "[]"),
            }
            for r in rows
        ]


@router.get("/meetings/{meeting_id}/debug/summary_logs")
async def debug_summary_logs(meeting_id: str, scope: str | None = None) -> list[dict]:
    """每波 LLM 调用全记录。scope=live|final。"""
    async with SessionLocal() as db:
        q = select(SummaryLlmLog).where(SummaryLlmLog.meeting_id == meeting_id)
        if scope in ("live", "final"):
            q = q.where(SummaryLlmLog.scope == scope)
        rows = (await db.execute(q.order_by(SummaryLlmLog.id))).scalars().all()
        return [
            {
                "id": r.id,
                "seq": r.seq,
                "scope": r.scope,
                "model": r.model,
                "prompt_json": json.loads(r.prompt_json or "[]"),
                "raw_response": r.raw_response,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@router.get("/meetings/{meeting_id}/debug/audio/{rel_path:path}")
async def debug_audio_file(meeting_id: str, rel_path: str) -> FileResponse:
    """调试音频文件下载（相对 data/debug 的路径）。"""
    root = _data_root().parent.parent  # data/debug/{meeting_id}/...
    path = (root / rel_path).resolve()
    if not str(path).startswith(str(root.resolve())) or not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type="audio/wav")


@router.get("/meetings/{meeting_id}/debug/visual/{rel_path:path}")
async def debug_visual_file(meeting_id: str, rel_path: str) -> FileResponse:
    """调试帧图下载（相对 data/debug 的路径）。"""
    root = _data_root().parent.parent
    path = (root / rel_path).resolve()
    if not str(path).startswith(str(root.resolve())) or not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type="image/jpeg")
