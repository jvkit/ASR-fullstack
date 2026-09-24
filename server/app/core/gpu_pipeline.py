"""GPU 非实时一体化管线客户端（/meeting_chunk + /vad + /identify_segments）。

/meeting_chunk:      wav -> [{start,end,speaker,text}]（归一化+VAD+逐子段ASR+ER认人）
                     —— 会中批次路径；会末终版自换芯一期 B 起不再使用
/vad:                wav -> [[start_s, end_s], ...]（批次切点用）
/identify_segments:  wav + 外部切段 -> [{start,end,cluster_id,speaker,text}]
                     —— 会末 3D-Speaker 分离结果的认人+转写（B 阶段新，唯一生产会末路径）
"""

from __future__ import annotations

import io
import json
import logging
import wave

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


async def vad_ends(pcm: bytes) -> list[float] | None:
    """VAD 句子结束点（相对输入音频的毫秒）。失败返回 None（调用方跳过本轮）。"""
    try:
        async with httpx.AsyncClient(timeout=settings.pipeline_vad_timeout_sec) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/vad",
                files={"file": ("chunk.wav", pcm_to_wav(pcm), "audio/wav")},
                data={"max_end_silence_ms": str(settings.pipeline_vad_max_end_silence_ms)},
            )
            r.raise_for_status()
            sents = r.json().get("sents", [])
        return [float(b) * 1000 for _, b in sents]
    except Exception:
        logger.exception("vad endpoints failed")
        return None


async def meeting_chunk(pcm: bytes, hotwords: str = "", timeout_sec: float | None = None,
                        final: bool = False) -> tuple | None:
    """一体化管线：音频 -> (带说话人的子段列表, 匿名簇更新)。

    返回 (segs, anon)；segs=[{start, end, speaker, text}]。anon 仍返回（GPU 契约不变）
    但调用方已弃用（方案 B：会中不做匿名聚类，"说话人N"兜底号由 session.display_speaker
    原样保留区分度）；失败返回 None。
    timeout_sec: 覆盖默认 pipeline_chunk_timeout_sec（会末全场重跑传大值）。
    final: True=会末终版（兜底发号+尾音归属+显示降级）；False=会中批次。
    """
    try:
        from app.core.preprocess import denoise_pcm
        pcm = denoise_pcm(pcm)  # 集中降噪层：会中批次/会末重标注共用此入口
        data: dict = {
            "name_threshold": str(settings.reannotate_name_threshold),
            "max_end_silence_ms": str(settings.pipeline_vad_max_end_silence_ms),
            "final": "true" if final else "false",
            "hw_weight": str(settings.pipeline_hw_weight),
            "punc": "true" if settings.pipeline_punc_enabled else "false",
            "itn": "true" if settings.pipeline_itn_enabled else "false",
        }
        # 方案 B：anon_clusters/anon_threshold/confirm_count 等匿名聚类参数不再传，
        # GPU 侧走默认值（空状态），等价于"只做声纹库认人"
        if hotwords:
            data["hotwords"] = hotwords
        async with httpx.AsyncClient(timeout=timeout_sec or settings.pipeline_chunk_timeout_sec) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/meeting_chunk",
                files={"file": ("chunk.wav", pcm_to_wav(pcm), "audio/wav")},
                data=data,
            )
            r.raise_for_status()
            j = r.json()
            return j.get("segs", []), j.get("anon", [])
    except Exception:
        logger.exception("meeting_chunk failed")
        return None


async def identify_segments(pcm: bytes, segments: list[dict], hotwords: str = "",
                            timeout_sec: float | None = None,
                            name_threshold: float | None = None) -> list[dict] | None:
    """会末 3D-Speaker 分离结果 → GPU 认人 + 转写（换芯一期 B，唯一生产会末路径）。

    pcm: 已过 preprocess.denoise_pcm 的 int16/16k/mono 音频（降噪在本地完成，
    与 GPU /meeting_chunk 的会中处理域一致）。
    segments: diarize3ds.diarize 的输出 [{start, end, cluster_id}]（秒）。
    一次 HTTP 批量传整段 wav + 全部边界，GPU 逐段 ASR（热词/标点/ITN 复用现有
    模型）+ 逐簇 ERes2NetV2 对声纹库认人（匈牙利一对一，未命中标"说话人N"）。

    返回 [{start, end, cluster_id, speaker, text}]（start/end 秒，与输入对齐）；
    GPU 不可用/失败返回 None（调用方按终版失败处理，不得回退旧聚类）。
    """
    try:
        thr = name_threshold if name_threshold and 0 < name_threshold < 1 \
            else settings.reannotate_name_threshold
        data = {
            "segments": json.dumps(
                [[float(s["start"]), float(s["end"]), int(s["cluster_id"])] for s in segments]
            ),
            "name_threshold": str(thr),
            "hw_weight": str(settings.pipeline_hw_weight),
            "punc": "true" if settings.pipeline_punc_enabled else "false",
            "itn": "true" if settings.pipeline_itn_enabled else "false",
        }
        if hotwords:
            data["hotwords"] = hotwords
        async with httpx.AsyncClient(
            timeout=timeout_sec or settings.identify_segments_timeout_sec
        ) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/identify_segments",
                files={"file": ("meeting.wav", pcm_to_wav(pcm), "audio/wav")},
                data=data,
            )
            r.raise_for_status()
            return r.json().get("segs", [])
    except Exception:
        logger.exception("identify_segments failed")
        return None
