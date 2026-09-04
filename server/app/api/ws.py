"""前端 WebSocket 端点：音频上行 + 字幕/总结下行。

协议：
    客户端第一条消息（JSON 文本）：
        {"type": "start", "title": "周会", "recorder": "张三"}
    之后：
        二进制帧      → PCM int16 16kHz 音频块
        {"type":"stop"} → 结束会议
    服务端推送（JSON）：
        {"type": "transcript_online", "text": ...}          流式临时字幕
        {"type": "transcript", "seq", "speaker", "text", ...}  确定句子（带说话人）
        {"type": "summary", "seq", "title", "points", "action_items", "final"}
        {"type": "ended"}
        {"type": "error", "message": ...}
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.session import session_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# 前端音频块大小约定：0.1s @16kHz int16 = 3200 bytes
MAX_BINARY_FRAME = 3200 * 4  # 宽容 4 倍，防止异常大包


@router.websocket("/ws/meeting")
async def meeting_ws(ws: WebSocket) -> None:
    await ws.accept()
    session = None

    try:
        # 1. 首条必须是 start 控制消息
        raw = await ws.receive_text()
        ctrl = json.loads(raw)
        if ctrl.get("type") != "start":
            await ws.send_text(json.dumps({"type": "error", "message": "first message must be start"}))
            await ws.close(code=4000)
            return

        session = session_manager.create(
            title=str(ctrl.get("title") or "未命名会议"),
            recorder=str(ctrl.get("recorder") or ""),
        )
        session.clients.add(ws)
        await session.start()
        await ws.send_text(json.dumps({"type": "started", "meeting_id": session.meeting_id}))

        # 2. 音频循环
        while True:
            msg = await ws.receive()
            if msg.get("bytes") is not None:
                frame = msg["bytes"]
                if len(frame) > MAX_BINARY_FRAME:
                    continue
                await session.handle_audio(frame)
            elif msg.get("text") is not None:
                try:
                    ctrl = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if ctrl.get("type") == "stop":
                    break

    except WebSocketDisconnect:
        logger.info("client disconnected")
    except Exception:
        logger.exception("meeting ws error")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": "internal error"}))
        except Exception:
            pass
    finally:
        if session:
            session.clients.discard(ws)
            await session.stop()
            session_manager.remove(session.meeting_id)
        try:
            await ws.close()
        except Exception:
            pass
