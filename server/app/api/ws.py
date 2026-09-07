"""前端 WebSocket 端点：音频上行 + 字幕/总结下行。

协议：
    客户端第一条消息（JSON 文本）：
        {"type": "start", "title": "周会", "spk_threshold": 0.55}
    之后：
        二进制帧      → PCM int16 16kHz 音频块
        {"type":"stop"} → 结束会议
        {"type":"pause"} / {"type":"resume"} → 暂停/恢复采集（暂停期间上行音频被丢弃）
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
        # 说话人聚类阈值（前端可调）：透传给 FunASR 服务端
        try:
            thr = float(ctrl.get("spk_threshold") or 0)
            if 0 < thr < 1:
                session.spk_threshold = thr
        except (TypeError, ValueError):
            pass
        # 参会人数（前端可填，0=自动估计）：会末重标注用它做 oracle，显著提升区分效果
        try:
            sc = int(ctrl.get("speaker_count") or 0)
            if 1 <= sc <= 15:
                session.speaker_count = sc
        except (TypeError, ValueError):
            pass
        # 高级调参（前端配置面板，逐项透传给 gpu FunASR / diarize）
        try:
            ms = int(ctrl.get("max_speakers") or 0)
            if 1 <= ms <= 15:
                session.max_speakers = ms
        except (TypeError, ValueError):
            pass
        try:
            vad = int(ctrl.get("vad_max_end_silence_ms") or 0)
            if 100 <= vad <= 2000:
                session.vad_max_end_silence_ms = vad
        except (TypeError, ValueError):
            pass
        try:
            vseg = int(ctrl.get("vad_max_segment_sec") or 0)
            if 2 <= vseg <= 60:
                session.vad_max_segment_sec = vseg
        except (TypeError, ValueError):
            pass
        try:
            smin = float(ctrl.get("spk_min_seg_sec") or 0)
            if 0 <= smin <= 10:
                session.spk_min_seg_sec = smin
        except (TypeError, ValueError):
            pass
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
                if ctrl.get("type") == "pause":
                    session.paused = True
                    logger.info("meeting %s paused", session.meeting_id)
                elif ctrl.get("type") == "resume":
                    session.paused = False
                    logger.info("meeting %s resumed", session.meeting_id)

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
            # 先收尾（最终转写/总结/ended 还要广播给这个客户端），再摘除连接
            await session.stop()
            session.clients.discard(ws)
            session_manager.remove(session.meeting_id)
        try:
            await ws.close()
        except Exception:
            pass
