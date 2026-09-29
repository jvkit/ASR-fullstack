"""会议事件时间线落库工具。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.db.database import SessionLocal
from app.db.models import MeetingEvent

logger = logging.getLogger(__name__)


async def log_event(meeting_id: str, stage: str, **kwargs: Any) -> None:
    """写一条会议事件。

    kwargs 会序列化为 JSON 存 detail；失败仅记日志，不抛异常。
    """
    try:
        detail = kwargs or {}
        async with SessionLocal() as db:
            db.add(MeetingEvent(
                meeting_id=meeting_id,
                stage=stage,
                detail=json.dumps(detail, ensure_ascii=False, default=str),
            ))
            await db.commit()
    except Exception:
        logger.exception("log_event failed: %s %s", meeting_id, stage)


async def log_error(meeting_id: str, message: str, exc: Exception | None = None) -> None:
    detail: dict[str, Any] = {"message": message}
    if exc is not None:
        detail["exc_type"] = type(exc).__name__
        detail["exc_str"] = str(exc)
    await log_event(meeting_id, "error", **detail)
