"""LLM 客户端：OpenAI SDK，Langfuse 自动埋点（可开关）。

Langfuse 通过官方 openai wrapper 实现零侵入 trace：
启用时 from langfuse.openai import OpenAI 替换原生客户端，
每一次 chat.completions 调用自动记录 prompt/response/耗时/token。
"""

import asyncio
import json
import logging

from app.config import settings
from app.llm import prompts

logger = logging.getLogger(__name__)

if settings.langfuse_enabled:
    try:
        # langfuse openai wrapper：与原生 OpenAI SDK 接口一致，调用自动上报 trace。
        # 需要显式初始化全局 Langfuse client（SDK 不会读我们的 .env）
        from langfuse import Langfuse as _Langfuse

        _langfuse = _Langfuse(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
        from langfuse.openai import OpenAI  # type: ignore[assignment]
    except Exception:  # langfuse SDK 版本差异导致初始化失败时，降级为原生客户端
        from openai import OpenAI

        logger.warning("langfuse init failed, fallback to native OpenAI client")
else:
    from openai import OpenAI

client = OpenAI(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    timeout=settings.llm_timeout_sec,
    max_retries=0,  # 网关抽风时秒失败，绝不重试排队（重试曾导致单任务卡 6 分钟堵死轮次）
)


async def summarize_window(prev_summary: str, window_text: str) -> dict:
    """总结一个窗口的转写文本。

    返回 {"title": str, "points": list[str], "action_items": list[dict], "attributes": list[dict]}
    解析失败时返回兜底结构，绝不向上抛异常（总结不能打断会议）。
    """
    messages = prompts.build_summary_messages(prev_summary, window_text)
    try:
        # openai SDK 是同步阻塞调用，丢线程池防止卡住事件循环
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "title": str(data.get("title", "")),
            "points": [str(p) for p in data.get("points", [])],
            "action_items": data.get("action_items", []),
            "attributes": data.get("attributes", []),
        }
    except Exception:
        logger.exception("summarize_window failed")
        return {"title": "", "points": [], "action_items": [], "attributes": []}


async def extract_final_attributes(full_text: str) -> dict:
    """会议结束时对全文提取主题 + 完整总结 + 标签 + 动态属性（事实性）。

    返回 {"title": str, "summary": str, "tags": list[str], "attributes": list[{"key","value"}]}
    """
    if not full_text.strip():
        return {"title": "", "summary": "", "tags": [], "attributes": []}
    messages = prompts.build_final_attributes_messages(full_text)
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "title": str(data.get("title", "")),
            "summary": str(data.get("summary", "")),
            "tags": [str(t) for t in data.get("tags", [])],
            "attributes": data.get("attributes", []),
        }
    except Exception:
        logger.exception("extract_final_attributes failed")
        return {"title": "", "summary": "", "tags": [], "attributes": []}


def build_correction_messages(context: str, summary: str, window_text: str) -> list[dict]:
    """纠错提示词构造（保持 prompts 单点维护）。"""
    return prompts.build_correction_messages(context, summary, window_text)


def build_enhance_messages(summary: str, prev_lines: list[tuple[str, str]],
                           cur_lines: list[tuple[str, str]]) -> list[dict]:
    """合并调用（纠错+修正上轮+滚动纪要）提示词构造（供 roller 每轮调用）。"""
    return prompts.build_enhance_messages(summary, prev_lines, cur_lines)

