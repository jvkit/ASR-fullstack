"""REST API：会末陌生人 → 声纹注册闭环（2026-10-01 用户拍板）。

痛点：陌生人（如临时参会的大领导）没有声纹，现场让人念注册语料体验极差。
闭环：会末版 3D-Speaker 聚类天然把未注册个体区分为"说话人N"，且每段音频
（降噪域）已落盘——前端按簇试听、勾选剔除错分段，拼接后直接注册进声纹库；
注册成功重跑会末版即可让此人"无中生有"地被认出，全程无感知。

三个端点：
  GET  /api/meetings/{mid}/unknown_clusters   按簇聚合未识别段（面板数据）
  GET  /api/meetings/{mid}/segments/{seq}/audio  单段音频试听
  POST /api/meetings/{mid}/enroll_cluster      勾选段拼接 → GPU 注册
"""

from __future__ import annotations

import logging
import re
import wave
import io
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import DiarSegment, Meeting, ReAnnotateUtterance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["enroll"])

# 会末版未注册个体的聚类兜底号（display_speaker keep_anon=True 原样保留）
_ANON_RE = re.compile(r"说话人\d+")

# 与 speakers.py 一致：注册下限 3 秒 @16kHz int16
MIN_ENROLL_BYTES = 16000 * 2 * 3


def _seg_abs_path(audio_path: str) -> Path:
    """DB 存的是 recordings 上级目录的相对路径（见 audio_storage）。"""
    return Path(settings.recordings_dir).parent / audio_path


@router.get("/meetings/{meeting_id}/unknown_clusters")
async def unknown_clusters(meeting_id: str) -> dict:
    """会末版全部簇聚合（2026-10-10 改造）：含已识别簇，支持注册/重注册/合并补录。

    数据源 ReAnnotateUtterance + DiarSegment（cluster_label）；已识别簇显示当前判名
    （可能是错的——错判不管，人工勾选覆盖注册）。段带试听与 cluster_sim 预评分。
    """
    async with SessionLocal() as db:
        if not await db.get(Meeting, meeting_id):
            raise HTTPException(404, "meeting not found")
        rows = (await db.execute(
            select(ReAnnotateUtterance)
            .where(ReAnnotateUtterance.meeting_id == meeting_id)
            .order_by(ReAnnotateUtterance.seq)
        )).scalars().all()
        diar_rows = (await db.execute(
            select(DiarSegment)
            .where(DiarSegment.meeting_id == meeting_id)
            .where(DiarSegment.source == "final")
            .order_by(DiarSegment.start_ms)
        )).scalars().all()
    # seq -> cluster_label 映射（diar_segments 表按 seq 对齐会末段）
    seq2label: dict[int, str] = {}
    for d in diar_rows:
        if d.audio_path and d.audio_path.endswith(".wav"):
            # 从文件名 seg_XXXX_<start>_<end>.wav 提取 seq（音频文件与会末段一一对应）
            pass
    # 更稳的映射：ReAnnotateUtterance 的 audio_path 文件名 seg_{seq:04d}_*
    seq2label = {}
    for r in rows:
        if r.audio_path:
            base = r.audio_path.split("/")[-1]
            if base.startswith("seg_"):
                try:
                    seq2label[r.seq] = base.split("_")[1].lstrip("0") or "0"
                except Exception:
                    pass
    # cluster_label 从 diar_segments 拿（按 seq 对齐）
    label_map: dict[int, str] = {}
    for d in diar_rows:
        # diar_segments 没有 seq 字段，按 start_ms 对齐
        label_map[d.start_ms] = d.cluster_label or "0"
    for r in rows:
        label_map.setdefault(r.start_ms, "0")
        seq2label[r.seq] = label_map[r.start_ms]

    clusters: dict[str, dict] = {}
    for r in rows:
        label = str(seq2label.get(r.seq, "0"))
        speaker = r.speaker or "未识别"
        # 簇 key：cluster_label（3D 聚类），同簇跨已识别/未识别也归一起
        c = clusters.setdefault(label, {"label": f"簇{label}", "cluster_label": label,
                                        "current_speaker": speaker, "seg_count": 0,
                                        "total_ms": 0, "segments": []})
        # 同簇若已有名字（优先保留非"说话人N"的判名）
        if not _ANON_RE.fullmatch(speaker) and (_ANON_RE.fullmatch(c["current_speaker"]) or c["current_speaker"] == "未识别"):
            c["current_speaker"] = speaker
        dur = max(0, (r.end_ms or 0) - (r.start_ms or 0))
        c["seg_count"] += 1
        c["total_ms"] += dur
        c["segments"].append({
            "seq": r.seq,
            "start_ms": r.start_ms,
            "end_ms": r.end_ms,
            "text": r.text or "",
            "score": r.diar_score,
            "audio_url": f"/api/meetings/{meeting_id}/segments/{r.seq}/audio" if r.audio_path else None,
            "_abs_path": str(_seg_abs_path(r.audio_path)) if r.audio_path else None,
        })
    # 按总时长降序：话最多的人排最前（注册价值最高）
    out = sorted(clusters.values(), key=lambda c: -c["total_ms"])

    # 每簇算"段 vs 簇质心"相似度（2026-10-09）：驱动注册弹窗的 40s 自动勾选与低分标红。
    # 格式 SEGS+大端长度+pcm 逐段拼接，一次 HTTP 每簇。
    for c in out:
        c["segments"] = await _score_cluster_segments(c["segments"])
    # 段按相似度降序展示：头部是"最像本人"的高质量段，尾部是重点检查对象
    for c in out:
        c["segments"].sort(key=lambda s: -(s.get("cluster_sim") or 0))
    for c in out:  # 内部字段不出口
        for s in c["segments"]:
            s.pop("_abs_path", None)

    return {"meeting_id": meeting_id,
            "auto_pick_sec": settings.enroll_auto_pick_sec,
            "warn_sim": settings.enroll_seg_warn_sim,
            "clusters": out}


def _pack_segs_pcm(pcms: list[bytes]) -> bytes:
    """SEGS 协议：b"SEGS" + 4字节大端长度 + int16 pcm，逐段拼接（GPU /segments_sim）。"""
    out = bytearray()
    for p in pcms:
        out += b"SEGS" + len(p).to_bytes(4, "big") + p
    return bytes(out)


async def _score_cluster_segments(segments: list[dict]) -> list[dict]:
    """给簇内每段音频补 cluster_sim（段 vs 簇质心余弦相似度）。

    音频缺失/过短的段 sim=None（前端不勾选不标红，按普通段处理）。
    GPU 不可用/失败时整簇 sim 留空（降级为旧的全选行为，不阻塞注册）。
    """
    pcms: list[bytes] = []
    for s in segments:
        pcm = None
        ap = s.get("_abs_path")
        if ap and Path(ap).is_file():
            try:
                pcm = _read_wav_pcm(Path(ap).read_bytes())
            except Exception:
                pcm = None
        pcms.append(pcm if pcm and len(pcm) >= 16000 else None)  # <0.5s 的段 GPU 会拒

    valid_idx = [i for i, p in enumerate(pcms) if p]
    if len(valid_idx) < 2:
        return segments
    payload = _pack_segs_pcm([pcms[i] for i in valid_idx])
    try:
        async with httpx.AsyncClient(timeout=settings.segments_sim_timeout_sec) as cli:
            r = await cli.post(f"{settings.diarize_http_url}/segments_sim", content=payload)
            r.raise_for_status()
            d = r.json()
        sims = d.get("sims") or []
    except Exception:
        logger.exception("segments_sim failed, cluster scores skipped")
        return segments
    for pos, i in enumerate(valid_idx):
        if pos < len(sims):
            segments[i]["cluster_sim"] = sims[pos]
    return segments


@router.get("/meetings/{meeting_id}/segments/{seq}/audio")
async def segment_audio(meeting_id: str, seq: int) -> FileResponse:
    """单段音频试听（会末版切段，降噪域 wav）。"""
    async with SessionLocal() as db:
        row = (await db.execute(
            select(ReAnnotateUtterance)
            .where(ReAnnotateUtterance.meeting_id == meeting_id)
            .where(ReAnnotateUtterance.seq == seq)
        )).scalar_one_or_none()
    if not row or not row.audio_path:
        raise HTTPException(404, "segment audio not found")
    path = _seg_abs_path(row.audio_path)
    if not path.is_file():
        raise HTTPException(404, "segment audio file missing")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


def _read_wav_pcm(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.readframes(w.getnframes())


@router.post("/meetings/{meeting_id}/enroll_cluster")
async def enroll_cluster(meeting_id: str, payload: dict = Body(...)) -> dict:
    """勾选段拼接 → GPU speaker_enroll 注册（2026-10-10 开放版）。

    payload: {
      name,                            # 注册目标名（新名字=新注册，已有名=补录/合并）
      labels: ["说话人2", ...],        # 旧：按会末判名筛选（未识别簇）
      cluster_labels: ["1", "4", ...], # 新：按 3D 聚类簇筛选（含已识别簇，支持错判覆盖）
      exclude_seqs: [int, ...],        # 剔除的段 seq
      trims: {seq: [start_ms, end_ms], ...}  # 段内裁剪：只保留该范围的音频（毫秒，相对段首）
    }
    两种筛选二选一（cluster_labels 优先）。trims 用于句尾剪错时保留中段。
    """
    name = str(payload.get("name") or "").strip()
    labels = [str(x) for x in (payload.get("labels") or [])]
    exclude = set(int(x) for x in (payload.get("exclude_seqs") or []))
    trims = {int(k): [float(v[0]), float(v[1])] for k, v in (payload.get("trims") or {}).items()}
    if not name or len(name) > 32:
        raise HTTPException(400, "姓名必填且 ≤32 字")
    labels = [str(x) for x in (payload.get("labels") or [])]
    cluster_labels = [str(x) for x in (payload.get("cluster_labels") or [])]
    if not labels and not cluster_labels:
        raise HTTPException(400, "至少选择一个说话人簇")

    async with SessionLocal() as db:
        if not await db.get(Meeting, meeting_id):
            raise HTTPException(404, "meeting not found")
        if cluster_labels:
            # 新簇键（cluster_label）——需按 seq2label 反查（与 unknown_clusters 同逻辑）
            rows_all = (await db.execute(
                select(ReAnnotateUtterance)
                .where(ReAnnotateUtterance.meeting_id == meeting_id)
                .order_by(ReAnnotateUtterance.seq)
            )).scalars().all()
            diar_rows = (await db.execute(
                select(DiarSegment)
                .where(DiarSegment.meeting_id == meeting_id)
                .where(DiarSegment.source == "final")
                .order_by(DiarSegment.start_ms)
            )).scalars().all()
            label_map = {d.start_ms: (d.cluster_label or "0") for d in diar_rows}
            rows = [r for r in rows_all
                    if str(label_map.get(r.start_ms, "0")) in set(cluster_labels)]
        else:
            rows = (await db.execute(
                select(ReAnnotateUtterance)
                .where(ReAnnotateUtterance.meeting_id == meeting_id)
                .where(ReAnnotateUtterance.speaker.in_(labels))
                .order_by(ReAnnotateUtterance.start_ms)
            )).scalars().all()

    chunks: list[bytes] = []
    used = 0
    for r in rows:
        if r.seq in exclude or not r.audio_path:
            continue
        path = _seg_abs_path(r.audio_path)
        if not path.is_file():
            continue
        try:
            piece = _read_wav_pcm(path.read_bytes())
            if r.seq in trims:
                a_ms, b_ms = trims[r.seq]
                s0 = max(0, int(a_ms * 32))
                s1 = min(len(piece), int(b_ms * 32))
                if s1 - s0 >= 16000:  # 裁剪后 ≥0.5s 才保留
                    piece = piece[s0 // 2 * 2:s1 // 2 * 2]
                else:
                    continue  # 裁没了 = 剔除
            chunks.append(piece)
            used += 1
        except Exception:
            logger.warning("enroll_cluster %s: bad wav %s", meeting_id, path)

    pcm = b"".join(chunks)
    if len(pcm) < MIN_ENROLL_BYTES:
        raise HTTPException(400, f"勾选音频太短：{len(pcm) / 32000:.1f}s < 3s，多勾几段")

    params = {
        "min_total_sec": settings.enroll_min_total_sec,
        "min_seg_sec": settings.enroll_min_seg_sec,
        "min_segments": settings.enroll_min_segments,
        "min_effective_sec": settings.enroll_min_effective_sec,
        "max_keep": settings.enroll_max_keep,
        "consistency": settings.enroll_consistency,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.enroll_timeout_sec) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/speaker_enroll",
                params={"name": name, **params},
                content=pcm,
            )
            r.raise_for_status()
            result = r.json()
    except httpx.HTTPError:
        logger.exception("enroll_cluster: speaker_enroll failed")
        raise HTTPException(502, "注册服务不可用，请稍后重试")

    logger.info("enroll_cluster %s: %s <- %s（勾选 %d 段，GPU kept=%s）",
                meeting_id, name, labels, used, result.get("kept"))
    return {**result, "meeting_id": meeting_id, "name": name, "used_segments": used}


@router.get("/meetings/{meeting_id}/segments/{seq}/trim_audio")
async def segment_trim_audio(meeting_id: str, seq: int, start_ms: int = 0, end_ms: int = 0):
    """段内裁剪预览：按 [start_ms, end_ms]（相对段首）截取音频返回。

    前端波形编辑器实时试听裁剪范围用；不落地、不改原文件。
    """
    from fastapi.responses import Response
    async with SessionLocal() as db:
        row = (await db.execute(
            select(ReAnnotateUtterance)
            .where(ReAnnotateUtterance.meeting_id == meeting_id)
            .where(ReAnnotateUtterance.seq == seq)
        )).scalar_one_or_none()
    if not row or not row.audio_path:
        raise HTTPException(404, "segment audio not found")
    path = _seg_abs_path(row.audio_path)
    if not path.is_file():
        raise HTTPException(404, "segment audio file missing")
    pcm = _read_wav_pcm(path.read_bytes())
    s0 = max(0, int(start_ms) * 32)
    s1 = min(len(pcm), int(end_ms) * 32) if end_ms else len(pcm)
    if s1 <= s0:
        raise HTTPException(400, "裁剪范围无效")
    pcm = pcm[s0 // 2 * 2:s1 // 2 * 2]
    import io as _io
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return Response(content=buf.getvalue(), media_type="audio/wav")
