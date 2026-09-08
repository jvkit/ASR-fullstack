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

import asyncio

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

        # 重放测试：音频来自历史录音（recordings/{replay_of}.wav），前端不开麦
        replay_of = str(ctrl.get("replay_of") or "")
        if replay_of:
            replay_of = replay_of.strip() or None

        session = session_manager.create(
            title=str(ctrl.get("title") or "未命名会议"),
            recorder=str(ctrl.get("recorder") or ""),
            replay_of=replay_of,
        )
        # 说话人聚类阈值（前端可调）：透传给 FunASR 服务端
        try:
            thr = float(ctrl.get("spk_threshold") or 0)
            if 0 < thr < 1:
                session.spk_threshold = thr
        except (TypeError, ValueError):
            pass
        # 注册声纹匹配阈值（前端可调，0.40=逐段声纹命中的 cosine 线；缺省走 config）
        try:
            mt = float(ctrl.get("spk_match_threshold") or 0)
            if 0 < mt < 1:
                session.spk_match_threshold = mt
        except (TypeError, ValueError):
            pass
        # 会末重标注声纹阈值（前端可调；缺省走 config 的 reannotate_name_threshold）
        try:
            rt = float(ctrl.get("reannotate_name_threshold") or 0)
            if 0 < rt < 1:
                session.reannotate_name_threshold = rt
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

        # 重放模式：后台线程按真实节奏喂历史录音，前端只负责播放同一音频
        replay_task = None
        if session.replay_of:
            from app.config import settings
            from pathlib import Path
            wav_path = Path(settings.recordings_dir) / f"{session.replay_of}.wav"
            replay_task = asyncio.create_task(session.run_replay(str(wav_path)))

        # 2. 音频循环
        while True:
            if session.replay_of:
                # 重放模式：receive 超时轮询，感知 replay_done（音频喂完自动结束）
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                except (TimeoutError, asyncio.TimeoutError):
                    if session.replay_done.is_set():
                        break
                    continue
            else:
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
        if replay_task:
            replay_task.cancel()
        if session:
            # 先收尾（最终转写/总结/ended 还要广播给这个客户端），再摘除连接
            await session.stop()
            session.clients.discard(ws)
            session_manager.remove(session.meeting_id)
        try:
            await ws.close()
        except Exception:
            pass
