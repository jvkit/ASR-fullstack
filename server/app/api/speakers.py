"""REST API：声纹库管理（唯一权威库在 GPU diarize_svc 的 speaker_enrolls/*.pcm）。

2026-09-15 单库改造：原 FunASR speaker_db.json 双库架构废弃（假删除根因——
DELETE 只删列表库、认人用的 pcm 段不动，且两库成员互相不一致）。
list/enroll/delete/identify 全部转发 diarize_svc。
"""

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, HTTPException, Query

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["speakers"])

MIN_ENROLL_BYTES = 16000 * 2 * 3  # 3 秒 @16kHz int16，与 gpu 端一致


@router.get("/speakers")
async def list_speakers() -> dict:
    """已注册声纹列表（pcm 目录为唯一权威库）。"""
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(f"{settings.diarize_http_url}/speaker_names")
            r.raise_for_status()
            return r.json()
    except Exception:
        logger.exception("speaker_names failed")
        raise HTTPException(502, "声纹服务不可用")


@router.post("/speakers/enroll")
async def enroll_speaker(
    name: str = Query(..., min_length=1, max_length=32),
    audio: bytes = Body(...),
) -> dict:
    """智能注册：raw PCM(int16/16kHz/mono) body。

    GPU 端做 VAD 切段 + 质量过滤（太短/太轻/爆音/声纹不一致丢弃）+
    多段质心留存，返回 kept/rejected 明细；不合格 ok=False 不落库。
    """
    if len(audio) < MIN_ENROLL_BYTES:
        raise HTTPException(400, f"音频太短：{len(audio) / 32000:.1f}s < 3s，请自然说 20~60 秒")
    from app.core.preprocess import denoise_async
    audio = await denoise_async(audio)  # 与会议推理同信号处理，防向量漂移
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
                params={"name": name.strip(), **params},
                content=audio,
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_enroll failed")
        raise HTTPException(502, "注册服务不可用，请稍后重试")


@router.post("/speakers/identify")
async def identify_speaker(audio: bytes = Body(...)) -> dict:
    """录入检测：几秒语音 -> 命中声纹库里的谁（返回全候选相似度降序）。

    兼作注册质量自查：注册完立刻测，能稳定认出自己=注册合格。
    """
    if len(audio) < 16000 * 2 * 1.5:
        raise HTTPException(400, f"音频太短：{len(audio) / 32000:.1f}s，请说 3~10 秒")
    from app.core.preprocess import denoise_async
    audio = await denoise_async(audio)
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/speaker_identify",
                params={"match_threshold": str(settings.identify_match_threshold)},
                content=audio,
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_identify failed")
        raise HTTPException(502, "检测服务不可用，请稍后重试")


@router.get("/speakers/{name}/segments")
async def speaker_segments(name: str) -> dict:
    """列出某人的全部注册段（前端试听质检用）。"""
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(
                f"{settings.diarize_http_url}/speaker_segments",
                params={"name": name},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_segments failed")
        raise HTTPException(502, "声纹服务不可用")


@router.get("/speakers/{name}/segments/{i}/audio")
async def speaker_segment_audio(name: str, i: int):
    """返回某注册段的 wav 音频流（GPU pcm 包装），前端 <audio>/Audio 直接播。"""
    from fastapi.responses import Response
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(
                f"{settings.diarize_http_url}/speaker_segment_audio",
                params={"name": name, "i": i},
            )
            if r.status_code == 404:
                raise HTTPException(404, "注册段不存在")
            r.raise_for_status()
            return Response(content=r.content, media_type="audio/wav")
    except httpx.HTTPError:
        logger.exception("speaker_segment_audio failed")
        raise HTTPException(502, "声纹服务不可用")


@router.delete("/speakers/{name}")
async def delete_speaker(name: str) -> dict:
    """真删除：删掉此人全部 pcm 注册段并失效缓存，立即生效。"""
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/speaker_delete",
                data={"name": name},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_delete failed")
        raise HTTPException(502, "删除服务不可用，请稍后重试")


@router.post("/speakers/rename")
async def rename_speaker(old_name: str = Body(...), new_name: str = Body(...)) -> dict:
    """重命名声纹（打错名字可改，不用删了重录）。"""
    old, new = str(old_name).strip(), str(new_name).strip()
    if not old or not new or len(new) > 32:
        raise HTTPException(400, "姓名无效")
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(f"{settings.diarize_http_url}/speaker_rename",
                               data={"old_name": old, "new_name": new})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_rename failed")
        raise HTTPException(502, "声纹服务不可用")


@router.post("/speakers/segment_delete")
async def delete_speaker_segment(name: str = Body(...), i: int = Body(...)) -> dict:
    """删除某人的一个注册段（其余段保留，质心自动重建）。"""
    nm = str(name).strip()
    if not nm:
        raise HTTPException(400, "姓名必填")
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(f"{settings.diarize_http_url}/speaker_segment_delete",
                               data={"name": nm, "i": int(i)})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError:
        logger.exception("speaker_segment_delete failed")
        raise HTTPException(502, "声纹服务不可用")


# 段级分数缓存（2026-10-09）：key = (人名, 段文件签名)。签名 = 段 (i,file,dur) 列表——
# 任何注册/合成/删除都会改文件名（含时间戳）或段列表，天然失效。避免每次打开
# 声纹页都全库重算把 GPU 打饱和（用户实测"一直在算、页面打不开"根因）。
_SEG_SCORES_CACHE: dict[str, tuple[str, list]] = {}


@router.get("/speakers/{name}/segment_scores")
async def speaker_segment_scores(name: str) -> dict:
    """某人注册段 vs 本人质心的逐段相似度（2026-10-09 段级质检）。

    低分段 = 拖质心后腿/疑似混入他人的重点检查对象（前端标红）。
    实现：拉全部注册段音频 → GPU /segments_sim 一次算完；结果按段列表签名缓存。
    """
    from fastapi.responses import Response
    nm = name.strip()
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(f"{settings.diarize_http_url}/speaker_segments", params={"name": nm})
            r.raise_for_status()
            segs = r.json().get("segments") or []
    except httpx.HTTPError:
        raise HTTPException(502, "声纹服务不可用")

    sig = repr([(s.get("i"), s.get("file"), s.get("dur")) for s in segs])
    cached = _SEG_SCORES_CACHE.get(nm)
    if cached and cached[0] == sig:
        return {"name": nm, "scores": sorted(cached[1], key=lambda x: -x["sim"]),
                "warn_sim": settings.enroll_seg_warn_sim, "cached": True}

    # 逐段取音频（复用 segment_audio 的转发逻辑）
    pcms: list[bytes] = []
    kept: list[dict] = []
    for s in segs:
        try:
            async with httpx.AsyncClient(timeout=30) as cli:
                ar = await cli.get(
                    f"{settings.diarize_http_url}/speaker_segment_audio",
                    params={"name": nm, "i": s["i"]},
                )
            if ar.status_code != 200:
                continue
            import io, wave
            with wave.open(io.BytesIO(ar.content), "rb") as w:
                pcm = w.readframes(w.getnframes())
            if len(pcm) < 16000:  # <0.5s 跳过
                continue
            pcms.append(pcm)
            kept.append({"i": s["i"], "dur": s.get("dur")})
        except Exception:
            continue
    if len(pcms) < 2:
        return {"name": nm, "scores": []}

    payload = bytearray()
    for p in pcms:
        payload += b"SEGS" + len(p).to_bytes(4, "big") + p
    try:
        async with httpx.AsyncClient(timeout=settings.segments_sim_timeout_sec) as cli:
            r = await cli.post(f"{settings.diarize_http_url}/segments_sim", content=bytes(payload))
            r.raise_for_status()
            d = r.json()
    except Exception:
        logger.exception("segment_scores failed")
        raise HTTPException(502, "相似度计算失败")
    sims = d.get("sims") or []
    scores = []
    for pos, k in enumerate(kept):
        if pos < len(sims) and sims[pos] is not None:
            scores.append({"i": k["i"], "dur": k["dur"], "sim": sims[pos]})
    _SEG_SCORES_CACHE[nm] = (sig, scores)
    return {"name": nm, "scores": sorted(scores, key=lambda x: -x["sim"]),
            "warn_sim": settings.enroll_seg_warn_sim,
            "centroid_sim": d.get("centroid_sim")}


# ---------- VAD 精修（2026-10-09）：长段切细 → 听/叉 → 合成回库 ----------

def _splits_dir() -> Path:
    d = Path(settings.recordings_dir).parent / "data" / "debug" / "_splits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_wav_pcm(data: bytes) -> bytes:
    import io, wave
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.readframes(w.getnframes())


@router.post("/speakers/{name}/split")
async def speaker_split(name: str) -> dict:
    """把某人的全部注册段送 GPU VAD 切成细片，落盘缓存并预评相似度。

    返回片列表（含试听地址 + sim 预评分）；前端默认勾选全部（低分预剔除交给前端
    按 warn_sim 决定）。缓存目录按 token 区分，24h 自动清。
    """
    import time as _time
    import uuid
    nm = name.strip()
    # 清 24h 以上旧缓存
    for old in _splits_dir().iterdir():
        try:
            if _time.time() - old.stat().st_mtime > 86400:
                import shutil
                shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass
    # 拉全部注册段音频
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.get(f"{settings.diarize_http_url}/speaker_segments", params={"name": nm})
            r.raise_for_status()
            segs = r.json().get("segments") or []
    except httpx.HTTPError:
        raise HTTPException(502, "声纹服务不可用")
    if not segs:
        return {"name": nm, "pieces": []}

    token = uuid.uuid4().hex[:12]
    out_dir = _splits_dir() / f"{nm}_{token}"
    out_dir.mkdir(parents=True, exist_ok=True)

    pieces: list[dict] = []
    pcms: list[bytes] = []      # 与 pieces 对齐，用于批量相似度
    meta: dict[int, dict] = {}  # piece_id -> {src_seg} 合成时按原段合并用
    idx = 0
    from app.core.gpu_pipeline import pcm_to_wav, vad_ends
    for s in segs:
        try:
            async with httpx.AsyncClient(timeout=30) as cli:
                ar = await cli.get(f"{settings.diarize_http_url}/speaker_segment_audio",
                                   params={"name": nm, "i": s["i"]})
            if ar.status_code != 200:
                continue
            pcm = _read_wav_pcm(ar.content)
        except Exception:
            continue
        ends = await vad_ends(pcm)  # 句子结束点（ms，相对本段）
        if not ends:
            ends = [len(pcm) // 32]
        prev = 0.0
        for e in ends:
            e_ms = min(float(e), len(pcm) // 32)
            if e_ms - prev < 300:   # <0.3s 的碎尾不要
                continue
            piece = pcm[int(prev * 32) // 2 * 2:int(e_ms * 32) // 2 * 2]  # 偶数字节对齐（int16）
            if len(piece) < 16000 * 0.5:
                prev = e_ms
                continue
            fn = f"piece_{idx:04d}_{int(prev)}_{int(e_ms)}.wav"
            (out_dir / fn).write_bytes(pcm_to_wav(piece))
            pieces.append({"id": idx, "src_seg": s["i"],
                           "dur": round(len(piece) / 32000, 2),
                           "audio_url": f"/api/speakers/_split_audio/{nm}/{token}/{idx}"})
            meta[idx] = {"src_seg": s["i"]}
            pcms.append(piece)
            idx += 1
            prev = e_ms
    import json as _json
    (out_dir / "meta.json").write_text(_json.dumps(meta, ensure_ascii=False))

    # 批量预评：每片 vs 全体片质心
    if len(pcms) >= 2:
        payload = bytearray()
        for p in pcms:
            payload += b"SEGS" + len(p).to_bytes(4, "big") + p
        try:
            async with httpx.AsyncClient(timeout=settings.segments_sim_timeout_sec) as cli:
                r = await cli.post(f"{settings.diarize_http_url}/segments_sim", content=bytes(payload))
                r.raise_for_status()
                sims = r.json().get("sims") or []
            for pos, piece in enumerate(pieces):
                if pos < len(sims):
                    piece["sim"] = sims[pos]
        except Exception:
            logger.exception("split pre-score failed")

    return {"name": nm, "token": token, "warn_sim": settings.enroll_seg_warn_sim,
            "pieces": sorted(pieces, key=lambda p: -(p.get("sim") or 0))}


@router.get("/speakers/_split_audio/{name}/{token}/{idx}")
async def split_audio(name: str, token: str, idx: int):
    """VAD 精修片的试听音频。"""
    from fastapi.responses import FileResponse
    d = _splits_dir() / f"{name}_{token}"
    hits = sorted(d.glob(f"piece_{idx:04d}_*.wav"))
    if not hits:
        raise HTTPException(404, "piece not found")
    return FileResponse(hits[0], media_type="audio/wav")


@router.post("/speakers/rebuild")
async def speaker_rebuild(payload: dict = Body(...)) -> dict:
    """勾选片合成回库：拼接 → GPU /speaker_replace 整批替换（用户亲验，不再票出）。

    payload: {name, token, keep: [piece_id...]}（keep 为空 = 拒绝执行，防误清空）
    """
    nm = str(payload.get("name") or "").strip()
    token = str(payload.get("token") or "")
    keep = [int(x) for x in (payload.get("keep") or [])]
    if not nm or not token:
        raise HTTPException(400, "参数缺失")
    if not keep:
        raise HTTPException(400, "至少保留一片，否则请用「删除声纹」")
    d = _splits_dir() / f"{nm}_{token}"
    if not d.is_dir():
        raise HTTPException(404, "切分缓存已过期，请重新点「VAD 精修」")

    # 相邻同原段的勾选片合并成一段再入库（2026-10-09）：25 片 → 通常回到 3~6 段，
    # 避免注册段数膨胀拖慢认人质心重建；无 meta 的旧缓存退回逐片写入。
    import json as _json
    meta_f = d / "meta.json"
    meta: dict = {}
    if meta_f.is_file():
        try:
            meta = {int(k): v for k, v in _json.loads(meta_f.read_text()).items()}
        except Exception:
            meta = {}

    def piece_pcm(i: int) -> bytes:
        hits = sorted(d.glob(f"piece_{i:04d}_*.wav"))
        if not hits:
            raise HTTPException(400, f"片 {i} 不存在")
        return _read_wav_pcm(hits[0].read_bytes())

    merged: list[bytes] = []
    if meta:
        cur = bytearray()
        cur_src = None
        for i in sorted(keep):
            src = meta.get(i, {}).get("src_seg")
            pcm = piece_pcm(i)
            if cur and src != cur_src:
                merged.append(bytes(cur))
                cur = bytearray()
            cur += pcm
            cur_src = src
        if cur:
            merged.append(bytes(cur))
    else:
        merged = [piece_pcm(i) for i in sorted(keep)]

    body = bytearray()
    for p in merged:
        body += b"SEGS" + len(p).to_bytes(4, "big") + p
    try:
        async with httpx.AsyncClient(timeout=settings.enroll_timeout_sec) as cli:
            r = await cli.post(f"{settings.diarize_http_url}/speaker_replace",
                               params={"name": nm}, content=bytes(body))
            r.raise_for_status()
            result = r.json()
    except httpx.HTTPError:
        logger.exception("speaker_replace failed")
        raise HTTPException(502, "合成服务不可用")
    if result.get("ok") is False:
        raise HTTPException(400, result.get("error") or "合成失败")
    # 成功后清缓存
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    return result
