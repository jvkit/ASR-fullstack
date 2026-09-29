"""音频中间产物落盘工具。

把原始整轨、降噪后整轨、以及 3D/VAD 切出来的小段音频按统一目录结构落盘，
DB 只存相对路径，便于调试页直接 serve。
"""

from __future__ import annotations

import io
import logging
import wave
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    """调试产物根目录：recordings 同级 data/debug/{meeting_id}。"""
    return Path(settings.recordings_dir).parent / "data" / "debug"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """int16/mono PCM → WAV bytes。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def save_raw_recording(meeting_id: str, pcm: bytes) -> str:
    """保存原始整轨录音，返回相对路径。"""
    out = _ensure_dir(_data_dir() / meeting_id) / "raw.wav"
    out.write_bytes(pcm_to_wav_bytes(pcm))
    return str(out.relative_to(Path(settings.recordings_dir).parent))


def save_denoised_recording(meeting_id: str, pcm: bytes) -> str:
    """保存降噪后整轨录音，返回相对路径。"""
    out = _ensure_dir(_data_dir() / meeting_id) / "denoised.wav"
    out.write_bytes(pcm_to_wav_bytes(pcm))
    return str(out.relative_to(Path(settings.recordings_dir).parent))


def save_segment_audio(meeting_id: str, source: str, idx: int,
                       pcm: bytes, start_ms: int, end_ms: int) -> str:
    """保存单段音频（source=live/final/enroll 等），返回相对路径。

    文件名包含起止毫秒，方便调试页按时间排序。
    """
    out = _ensure_dir(_data_dir() / meeting_id / "segments" / source)
    fname = f"seg_{idx:04d}_{start_ms:08d}_{end_ms:08d}.wav"
    path = out / fname
    path.write_bytes(pcm_to_wav_bytes(pcm))
    return str(path.relative_to(Path(settings.recordings_dir).parent))
