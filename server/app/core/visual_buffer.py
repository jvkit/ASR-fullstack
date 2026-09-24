"""视觉帧缓冲：收前端定时抽的屏幕帧，会议纪要波次触发时作为一个组取走。

协同模型（docs/achit/视频侧接口与代码地图-20260922.md §1）：
- 音频侧是节拍器：enhancer 凑满一波（summary_every_n_batches 个 L1 批次 ≈ 2min）
  时调用 take_group()，把「上次波到现在」缓冲的全部帧作为一个组送 OCR。
- 视频侧不自己计时：波次浮动（VAD 切点）由音频侧统一裁决，避免两套时钟漂移。
- 视频侧不等 LLM：take_group() 原子取走，LLM 在飞期间新帧继续缓冲，不阻塞采集。
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


class VisualBuffer:
    """挂在 MeetingSession 上的帧缓冲。非阻塞，失败仅记日志。"""

    def __init__(self, meeting_id: str) -> None:
        self._meeting_id = meeting_id
        # [(t_ms, jpeg_bytes)]，按时间升序；t_ms = 会议开始后的相对毫秒（与 utterances 同基准）
        self._frames: list[tuple[int, bytes]] = []
        self.total_received = 0   # 会话统计：累计收到帧数（日志/观测用）
        self.total_dropped = 0    # 防雪球丢弃的帧数

    def add_frame(self, t_ms: int, jpeg: bytes) -> bool:
        """收一帧。缓冲满时丢最旧帧（防雪球，丢帧记日志）。"""
        if not jpeg:
            return False
        self._frames.append((int(t_ms), jpeg))
        self.total_received += 1
        limit = settings.visual_max_buffer_frames
        if len(self._frames) > limit:
            self._frames.pop(0)
            self.total_dropped += 1
            if self.total_dropped % 4 == 1:
                logger.warning("visual %s: 帧缓冲满（>%d），丢最旧帧（累计丢 %d）",
                               self._meeting_id, limit, self.total_dropped)
        return True

    async def take_group(self) -> list[tuple[int, bytes]]:
        """波次触发时调用：原子取走缓冲的全部帧作为一个组（按时间升序）。"""
        frames, self._frames = self._frames, []
        return frames

    @property
    def buffered(self) -> int:
        return len(self._frames)
