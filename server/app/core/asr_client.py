"""FunASR WebSocket 客户端（2pass 协议）。

协议参考 funasr/runtime/python/websocket：
1. 连接后先发送一条 JSON 配置消息（mode/chunk_size/is_speaking 等）
2. 之后持续发送二进制 PCM（int16 little-endian, 16kHz）
3. 结束时发送 {"is_speaking": false}
4. 服务端持续返回 JSON：
   - {"mode": "2pass-online",  "text": ...}                 流式临时结果
   - {"mode": "2pass-offline", "text": ..., "timestamp": ..., "spk": ...}  句终确定结果
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import websockets

logger = logging.getLogger(__name__)

OnlineCallback = Callable[[str], Awaitable[None]]
OfflineCallback = Callable[[str, list, str | None], Awaitable[None]]
ErrorCallback = Callable[[str], Awaitable[None]]
ClosedCallback = Callable[[], Awaitable[None]]

# 16kHz * 2 bytes * 0.1s = 3200 bytes，与 FunASR 官方 web 客户端一致的节拍
SEND_INTERVAL_SEC = 0.1
FRAME_BYTES = 3200

# 2pass 模式下服务端按 600ms 窗口出 online 结果；[5,10,5] 是官方默认
DEFAULT_CONFIG = {
    "mode": "2pass",
    "chunk_size": [5, 10, 5],
    "chunk_interval": 10,
    "wav_name": "browser",
    "is_speaking": True,
    "itn": True,
}


class FunasrClient:
    """一条会议 = 一个 FunASR ws 连接。"""

    def __init__(
        self,
        url: str,
        hotwords: str = "",
        ssl_insecure: bool = False,
        on_online: OnlineCallback | None = None,
        on_offline: OfflineCallback | None = None,
        on_error: ErrorCallback | None = None,
        on_closed: ClosedCallback | None = None,
    ) -> None:
        self.url = url
        self.hotwords = hotwords
        self.ssl_insecure = ssl_insecure
        self._on_online = on_online
        self._on_offline = on_offline
        self._on_error = on_error
        self._on_closed = on_closed
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._closed = False
        self._finished = False  # 是否已发送 is_speaking=false

    async def start(self) -> None:
        config = dict(DEFAULT_CONFIG)
        if self.hotwords:
            config["hotwords"] = self.hotwords
        await self._connect(json.dumps(config, ensure_ascii=False))

    async def _connect(self, config_msg: str) -> None:
        try:
            # FunASR server 默认 certfile 路径存在即启用 TLS（wss://），自签证书需忽略验证；
            # 且 serve 端 subprotocols=["binary"]，客户端必须带上该子协议
            import ssl

            ssl_ctx = None
            if self.url.startswith("wss://") or self.ssl_insecure:
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            self._ws = await websockets.connect(
                self.url, max_queue=1024, subprotocols=["binary"], ssl=ssl_ctx
            )
            await self._ws.send(config_msg)
        except Exception as e:
            msg = f"connect funasr failed: {e}"
            logger.error(msg)
            if self._on_error:
                await self._on_error(msg)
            raise

        # 接收循环
        import asyncio

        asyncio.get_running_loop().create_task(self._recv_loop())

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws and not self._finished:
            try:
                await self._ws.send(pcm)
            except websockets.ConnectionClosed:
                await self._handle_close()

    async def finish(self) -> None:
        """发送结束标记；服务端会把缓冲的音频跑完后发最终 offline 结果。"""
        if self._ws and not self._finished:
            self._finished = True
            try:
                await self._ws.send(json.dumps({"is_speaking": False}))
            except websockets.ConnectionClosed:
                await self._handle_close()

    async def close(self) -> None:
        await self._handle_close()

    async def _handle_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._on_closed:
            await self._on_closed()

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mode = msg.get("mode", "")
                text = msg.get("text", "")
                if mode == "2pass-online":
                    if self._on_online and text:
                        await self._on_online(text)
                elif mode == "2pass-offline":
                    ts = msg.get("timestamp") or []
                    spk = msg.get("spk_name") or msg.get("spk")
                    if self._on_offline and text:
                        await self._on_offline(text, ts, spk)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            logger.exception("funasr recv loop error")
        finally:
            await self._handle_close()
