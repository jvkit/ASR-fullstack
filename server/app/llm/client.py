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


async def extract_final_attributes(segments_text: str) -> dict:
    """会议结束时对【各波段纪要】提取主题 + 完整总结 + 标签 + 动态属性。

    segments_text 是全部段纪要的拼接（不是原始转写全文）——会末总结只看段纪要，
    故输入规模 = 波数 × 每波纪要，与会议时长的原始文本量解耦。
    返回 {"title": str, "summary": str, "tags": list[str], "attributes": list[{"key","value"}]}
    """
    if not segments_text.strip():
        return {"title": "", "summary": "", "tags": [], "attributes": []}
    messages = prompts.build_final_attributes_messages(segments_text)
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


def build_enhance_messages(ctx_summaries: list[str], prev_summary: str,
                           prev_lines: list[tuple[str, str]],
                           cur_lines: list[tuple[str, str]]) -> list[dict]:
    """一波一次合并调用（纠错本波 + 订正上一波文本/纪要 + 出本波段纪要）的提示词构造。"""
    return prompts.build_enhance_messages(ctx_summaries, prev_summary, prev_lines, cur_lines)

