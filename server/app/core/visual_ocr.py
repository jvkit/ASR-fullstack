"""视觉 OCR：一组屏幕/摄像头帧 → markdown 文字记录。

实测依据（video/spike/）：
- 多模态池必须用「new」——「chat」池对图片请求报错（2026-09-16 实测）。
- 960 宽缩略下屏幕小字/WiFi 列表/MAC 地址全可读（2026-09-22 摄像头实测），
  帧缩略是前端职责（visual_frame_width），这里直接吃 JPEG bytes。
- 32 帧读图 2 分钟起，逼近超时——单组帧数建议 ≤16；超出的帧按间隔抽样。
- 失败返回空串：调用方（enhancer）按「无视觉上下文」继续出纪要，不阻塞。
"""

from __future__ import annotations

import asyncio
import base64
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# 提示词模板：视应用层无关，只换文案（visual_prompt_app 配置选择）
PROMPTS = {
    "online": (
        "以下是会议屏幕内容的定时截图（按时间顺序，间隔约10秒，附相对时间戳）。\n"
        "请提取其中出现的文字信息（PPT标题/要点/白板板书/共享文档/操作界面内容），"
        "按时间顺序输出为简洁的 markdown 记录。\n"
        "要求：无新内容的重复画面跳过；不可辨认的内容标注（不可见），不要编造；"
        "每条记录前标注 [mm:ss] 时间戳。"
    ),
    "offline": (
        "以下是会议室摄像头画面截图（按时间顺序，间隔约10秒，附相对时间戳）。\n"
        "请描述白板/纸张/屏幕上的板书与文字内容，以及出现的人物动作与位置变化，"
        "按时间顺序输出为简洁的 markdown 记录。\n"
        "要求：无新内容的重复画面跳过；不可辨认的内容标注（不可见），不要编造；"
        "每条记录前标注 [mm:ss] 时间戳。"
    ),
}

# 单组帧数上限：实测 32 帧读图 2min+，16 帧内延迟可控（超出按均匀间隔抽样）
MAX_FRAMES_PER_GROUP = 16

# 视觉请求用裸 OpenAI 客户端：langfuse 的 openai wrapper 提取多模态 prompt
# 时抛异常（image_url 数组消息未适配），请求到不了网关。裸客户端绕开拦截，
# 视觉调用不进 Langfuse trace（文字链路 trace 不受影响）。
_raw_client = None


def _get_raw_client():
    global _raw_client
    if _raw_client is None:
        from openai import OpenAI

        _raw_client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.visual_llm_timeout_sec,
            max_retries=0,
        )
    return _raw_client


def _mmss(t_ms: int) -> str:
    s = max(0, int(t_ms)) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _sample_frames(frames: list[tuple[int, bytes]]) -> list[tuple[int, bytes]]:
    """帧数超上限时按均匀间隔抽样（保首尾，时间轴均匀覆盖）。"""
    if len(frames) <= MAX_FRAMES_PER_GROUP:
        return frames
    step = len(frames) / MAX_FRAMES_PER_GROUP
    idxs = sorted({min(int(i * step), len(frames) - 1) for i in range(MAX_FRAMES_PER_GROUP)})
    if idxs[-1] != len(frames) - 1:
        idxs[-1] = len(frames) - 1   # 保尾（最新状态必在）
    return [frames[i] for i in idxs]


async def visual_ocr(frames: list[tuple[int, bytes]], app: str = "") -> str:
    """一组帧 → markdown 文字。失败返回 ''（不阻塞纪要，调用方记日志）。

    app: 提示词模板（"online" 屏幕共享 / "offline" 摄像头）；空则回退配置默认。
    """
    if not frames:
        return ""
    frames = _sample_frames(frames)
    if app not in PROMPTS:
        app = settings.visual_prompt_app if settings.visual_prompt_app in PROMPTS else "online"
    parts = [{"type": "text", "text": PROMPTS[app]}]
    for t_ms, jpeg in frames:
        b64 = base64.b64encode(jpeg).decode()
        parts.append({"type": "text", "text": f"[{_mmss(t_ms)}]"})
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(
                _get_raw_client().chat.completions.create,
                model=settings.visual_llm_model,
                messages=[{"role": "user", "content": parts}],
                temperature=0.2,
            ),
            timeout=settings.visual_llm_timeout_sec,
        )
        text = (resp.choices[0].message.content or "").strip()
        logger.info("visual_ocr: %d 帧出文 %d 字", len(frames), len(text))
        return text
    except Exception:
        logger.exception("visual_ocr failed（%d 帧，返回空，不阻塞纪要）", len(frames))
        return ""
