"""REST API：会议查询与纪要导出（供 OA / 前端调用）。"""

import json
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.config import settings
from app.core.segment import merge_display_rows
from app.db.database import SessionLocal
from app.db.models import Meeting, ReAnnotateUtterance, Summary, Utterance
from app.llm.client import normalize_summary_text

router = APIRouter(prefix="/api", tags=["meetings"])


@router.get("/config")
async def get_config() -> dict:
    """可调参数只此一处：前端默认值、批次管线、手动重标注全部从这里读。"""
    return {
        "reannotate_name_threshold": settings.reannotate_name_threshold,  # 认人阈值（批次/终版/手动共用一个）
        "pipeline_batch_min_sec": settings.pipeline_batch_min_sec,        # 攒够这么多音频成一批
        "pipeline_batch_max_sec": settings.pipeline_batch_max_sec,        # 超过强制成批
        "pipeline_tick_sec": settings.pipeline_tick_sec,                  # 批次调度检查间隔
        "pipeline_vad_max_end_silence_ms": settings.pipeline_vad_max_end_silence_ms,
        # 视觉通道（线上会议抓屏抽帧）：前端 ScreenFrameCapture 从这里读，不硬编码
        "visual_enabled": settings.visual_enabled,
        "visual_frame_interval_sec": settings.visual_frame_interval_sec,
        "visual_frame_width": settings.visual_frame_width,
        "visual_frame_quality": settings.visual_frame_quality,
        # 视频持久化（2026-10-08）：MediaRecorder 落盘开关与码率，前端读这里
        "video_record_enabled": settings.video_record_enabled,
        "video_bits_per_second": settings.video_bits_per_second,
    }


@router.get("/meetings")
async def list_meetings(limit: int = 50) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(Meeting).order_by(Meeting.started_at.desc()).limit(limit))).scalars().all()
        # 产物计数：失败/中断会议一眼可辨（0 句 0 段）
        ids = [m.id for m in rows]
        uc: dict[str, int] = {}
        sc: dict[str, int] = {}
        if ids:
            uc = dict((await db.execute(
                select(Utterance.meeting_id, func.count()).where(Utterance.meeting_id.in_(ids)).group_by(Utterance.meeting_id)
            )).all())
            sc = dict((await db.execute(
                select(Summary.meeting_id, func.count()).where(Summary.meeting_id.in_(ids)).group_by(Summary.meeting_id)
            )).all())
        return [
            {
                "id": m.id,
                "title": m.title,
                "recorder": m.recorder,
                "status": m.status,
                "started_at": m.started_at.isoformat() if m.started_at else None,
                "ended_at": m.ended_at.isoformat() if m.ended_at else None,
                "attributes": json.loads(m.attributes or "[]"),
                "tags": json.loads(m.tags or "[]"),
                "reannotated": bool(m.reannotated),
                "final_status": m.final_status or "",
                "utterance_count": uc.get(m.id, 0),
                "summary_count": sc.get(m.id, 0),
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
        re_utts = (await db.execute(
            select(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == meeting_id)
            .order_by(ReAnnotateUtterance.seq)
        )).scalars().all()
        gap_ms = int(settings.display_merge_gap_sec * 1000)
        return {
            "id": meeting.id,
            "title": meeting.title,
            "recorder": meeting.recorder,
            "status": meeting.status,
            "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
            "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
            "attributes": json.loads(meeting.attributes or "[]"),
            "tags": json.loads(meeting.tags or "[]"),
            "reannotated": bool(meeting.reannotated),
            "final_status": meeting.final_status or "",
            # 双版本架构（2026-09-28）：版本模式 / 采集来源 / 权威待办清单（会末版为准）
            "version_mode": meeting.version_mode,
            "source_mode": meeting.source_mode,
            "action_items": json.loads(meeting.action_items or "[]"),
            # 有录音文件才有回放地址
            "audio_url": (
                f"/api/meetings/{meeting_id}/audio"
                if (Path(settings.recordings_dir) / f"{meeting_id}.wav").is_file()
                else None
            ),
            "utterances": merge_display_rows([
                {"seq": u.seq, "speaker": u.speaker, "text": u.text,
                 "start_ms": u.start_ms, "end_ms": u.end_ms}
                for u in utts
            ], gap_ms),
            # 会末重标注版（阿里 pipeline 切分 + ERes2NetV2 声纹标身份；段级时间+文字）
            "utterances_re": merge_display_rows([
                {"seq": u.seq, "speaker": u.speaker, "text": u.text,
                 "start_ms": u.start_ms, "end_ms": u.end_ms}
                for u in re_utts
            ], gap_ms),
        }


@router.post("/meetings/{meeting_id}/reannotate")
async def meeting_reannotate(meeting_id: str) -> dict:
    """手动完整重跑会末版（重标注 → 波次纪要 → 会末总结），后台执行。

    2026-10-01 起从「只重跑转写」升级为完整会末版：声纹注册后重跑才能同时刷新
    段纪要/会末总结里的人名。进度/结果看 meeting.final_status（前端轮询）。
    """
    import asyncio

    from app.core.final_pass import rerun_final_pass_standalone

    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
        if meeting.final_status == "running":
            raise HTTPException(409, "会末版正在生成中，请勿重复触发")
    if not (Path(settings.recordings_dir) / f"{meeting_id}.wav").is_file():
        raise HTTPException(404, "recording not found")
    asyncio.create_task(rerun_final_pass_standalone(meeting_id))
    return {"ok": True, "meeting_id": meeting_id, "started": True}


# 视觉帧上行限制：单帧 200KB（base64 后 ~267KB），单请求 ≤ 6 帧
_MAX_JPEG_B64 = 267 * 1024
_MAX_FRAMES_PER_REQ = 6


@router.post("/meetings/{meeting_id}/frames")
async def meeting_frames(meeting_id: str, payload: dict) -> dict:
    """视觉通道收帧（线上会议/摄像头定时抽帧，见 docs/achit/视频侧接口与代码地图 §3.1）。

    payload: {"frames": [{"t_ms": int, "jpeg_b64": str}, ...]}
    帧进 VisualBuffer；会议纪要波次（enhancer）触发时作为一组送 OCR。
    """
    from base64 import b64decode

    from app.core.session import session_manager

    session = session_manager.get(meeting_id)
    if session is None or session.stopped:
        raise HTTPException(404, "live meeting not found")
    frames = payload.get("frames") or []
    if not isinstance(frames, list) or len(frames) > _MAX_FRAMES_PER_REQ:
        raise HTTPException(400, f"frames must be a list of ≤{_MAX_FRAMES_PER_REQ}")
    ok = 0
    for f in frames:
        if not isinstance(f, dict):
            continue
        b64 = f.get("jpeg_b64") or ""
        if not isinstance(b64, str) or len(b64) > _MAX_JPEG_B64:
            continue
        try:
            jpeg = b64decode(b64)
        except Exception:
            continue
        if session.visual.add_frame(int(f.get("t_ms") or 0), jpeg):
            ok += 1
    return {"ok": True, "accepted": ok, "buffered": session.visual.buffered}


@router.get("/meetings/{meeting_id}/audio")
async def meeting_audio(meeting_id: str) -> FileResponse:
    """会议录音回放（wav，16kHz/16bit/mono）。"""
    path = Path(settings.recordings_dir) / f"{meeting_id}.wav"
    if not path.is_file():
        raise HTTPException(404, "recording not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{meeting_id}.wav")


@router.get("/meetings/{meeting_id}/audio_denoised")
async def meeting_audio_denoised(meeting_id: str) -> FileResponse:
    """降噪版回放：原始录音过 GTCRN（与识别/声纹同一份处理），落盘缓存只算一次。

    原始存档不动；本端点按需生成 recordings/_denoised/<id>.wav。
    """
    from starlette.concurrency import run_in_threadpool

    src = Path(settings.recordings_dir) / f"{meeting_id}.wav"
    if not src.is_file():
        raise HTTPException(404, "recording not found")
    cache_dir = Path(settings.recordings_dir) / "_denoised"
    cache = cache_dir / f"{meeting_id}.wav"

    def _build() -> None:
        import soundfile as sf
        from app.core.preprocess import denoise_sync
        data, _sr = sf.read(str(src), dtype="int16")
        out = denoise_sync(data.tobytes())
        cache_dir.mkdir(exist_ok=True)
        # 必须写标准 WAV 容器（裸 PCM 浏览器无法解码）
        sf.write(str(cache), np.frombuffer(out, dtype=np.int16), 16000, subtype="PCM_16")

    if not cache.is_file():
        await run_in_threadpool(_build)
    return FileResponse(cache, media_type="audio/wav", filename=f"{meeting_id}_denoised.wav")


@router.get("/meetings/{meeting_id}/summaries")
async def meeting_summaries(meeting_id: str, scope: str | None = None) -> list[dict]:
    """段纪要/终版总结查询。scope=live|final 过滤版本（2026-09-28 双版本架构，
    前端按 scope 分 tab）；缺省全返，每条带 scope 字段自行区分。"""
    async with SessionLocal() as db:
        q = select(Summary).where(Summary.meeting_id == meeting_id)
        if scope in ("live", "final"):
            q = q.where(Summary.scope == scope)
        rows = (await db.execute(q.order_by(Summary.seq))).scalars().all()
        return [
            {
                "seq": s.seq,
                "scope": s.scope,
                "title": s.title,
                "content": normalize_summary_text(s.content),
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "action_items": json.loads(s.action_items or "[]"),
                "attributes": json.loads(s.attributes or "[]"),
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

        # 会议信息：系统时间（固定）+ AI 提取的事实性动态字段
        info: list[tuple[str, str]] = []
        if meeting.started_at:
            info.append(("开始时间", meeting.started_at.strftime("%Y-%m-%d %H:%M")))
        if meeting.ended_at:
            info.append(("结束时间", meeting.ended_at.strftime("%Y-%m-%d %H:%M")))
        for kv in json.loads(meeting.attributes or "[]"):
            if isinstance(kv, dict) and kv.get("key"):
                info.append((str(kv["key"]), str(kv.get("value", ""))))
        tags = json.loads(meeting.tags or "[]")
        if tags:
            info.append(("标签", "、".join(str(t) for t in tags)))
        if info:
            parts.append("## 会议信息\n")
            for k, v in info:
                parts.append(f"- **{k}**：{v}")
            parts.append("")

        all_actions: list[dict] = []
        for s in summaries:
            # seq=0 是会末终版（排在最前，先总结后分章）；1..N 是各波段纪要
            heading = "最终总结" if s.seq == 0 else f"{s.seq}. {s.title or f'第 {s.seq} 段'}"
            parts.append(f"## {heading}\n")
            parts.append(normalize_summary_text(s.content) + "\n")
            all_actions.extend(json.loads(s.action_items or "[]"))

        if all_actions:
            parts.append("## 待办汇总\n")
            # 按人分组导出（同前端待办卡片分组口径 2026-10-13）；未指派归"其它"
            grouped: dict[str, list[dict]] = {}
            for a in all_actions:
                who = str(a.get("assignee") or a.get("who") or "").strip() or "其它"
                grouped.setdefault(who, []).append(a)
            for who in sorted(grouped.keys(), key=lambda x: (x == "其它", x)):
                parts.append(f"### {who}\n")
                for a in grouped[who]:
                    what = str(a.get("task") or a.get("what") or "").strip()
                    due = str(a.get("due") or "").strip()
                    status = str(a.get("status") or "").strip()
                    due_txt = f"（{due}）" if due else ""
                    parts.append(f"- [ ] {what}{due_txt}{status}")
                parts.append("")

        return {"id": meeting.id, "title": meeting.title, "markdown": "\n".join(parts)}


# 视频持久化上传（2026-10-08）：前端 MediaRecorder 把整轨 webm 分片 POST 上来落盘
_MAX_VIDEO_SEGMENT_BYTES = 64 * 1024 * 1024  # 单片 ≤64MB


@router.post("/meetings/{meeting_id}/video_segment")
async def meeting_video_segment(meeting_id: str, segment_index: int = 0,
                                payload: bytes = Body(...)) -> dict:
    """收会议视频分片（webm bytearray）。segment_index 从 0 递增，落盘为
    recordings/_video/<meeting_id>/seg_<index>.webm；同时记 meeting.video_path。"""
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
    if not payload or len(payload) > _MAX_VIDEO_SEGMENT_BYTES:
        raise HTTPException(400, "empty or too large segment")
    out_dir = Path(settings.recordings_dir) / "_video" / meeting_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"seg_{segment_index:04d}.webm").write_bytes(payload)
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting:
            meeting.video_path = str(
                (out_dir / "playlist.m3u8").relative_to(Path(settings.recordings_dir).parent))
            await db.commit()
    return {"ok": True, "segment_index": segment_index, "bytes": len(payload)}


@router.get("/meetings/{meeting_id}/video")
async def meeting_video(meeting_id: str) -> FileResponse:
    """整场视频回放（单文件模式）：segments 拼成一个 webm 返回。

    分片模式下前端按 Range 逐个请求 /video_segment/<i>；为简单起见这里先支持
    单文件（前端每次 stop 时 finalize 只留一个 seg_0000.webm），多片时按序拼接。
    """
    vdir = Path(settings.recordings_dir) / "_video" / meeting_id
    if not vdir.is_dir():
        raise HTTPException(404, "no video for this meeting")
    segs = sorted(vdir.glob("seg_*.webm"))
    if not segs:
        raise HTTPException(404, "no video segments")
    if len(segs) == 1:
        return FileResponse(segs[0], media_type="video/webm", filename=f"{meeting_id}.webm")
    # 多片：拼到缓存（一次拼接，后续直接复用）
    import tempfile
    cache = vdir / "_merged.webm"
    if not cache.is_file():
        with tempfile.NamedTemporaryFile(dir=vdir, delete=False) as tmp:
            for s in segs:
                tmp.write(s.read_bytes())
            tmp_path = Path(tmp.name)
        tmp_path.replace(cache)
    return FileResponse(cache, media_type="video/webm", filename=f"{meeting_id}.webm")
