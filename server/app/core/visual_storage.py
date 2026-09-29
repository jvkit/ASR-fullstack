"""视觉帧图落盘工具。

OCR 的原始帧图按会议落盘，DB 只存相对路径列表。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    return Path(settings.recordings_dir).parent / "data" / "debug"


def save_visual_frames(meeting_id: str, note_id: int,
                       frames: list[tuple[int, bytes]]) -> list[str]:
    """把一组帧保存到 data/debug/{meeting_id}/visual/{note_id}/ 下，返回相对路径列表。"""
    if not frames:
        return []
    base = _data_dir() / meeting_id / "visual" / str(note_id)
    base.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    root = Path(settings.recordings_dir).parent
    for i, (t_ms, jpeg) in enumerate(frames):
        path = base / f"frame_{i:03d}_{t_ms:08d}.jpg"
        path.write_bytes(jpeg)
        paths.append(str(path.relative_to(root)))
    return paths
