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
    """会议结束时对【各波段纪要】提取主题 + 完整总结 + 标签 + 动态属性 + 待办清单。

    segments_text 是全部段纪要的拼接（不是原始转写全文）——会末总结只看段纪要，
    故输入规模 = 波数 × 每波纪要，与会议时长的原始文本量解耦。
    返回 {"title", "summary", "tags", "attributes", "action_items"}；
    action_items 为结构化待办 [{task,assignee,due,quote}]（2026-09-28 新增，
    实时/会末两个版本的最终总结共用本函数与同一套 prompt）。
    """
    if not segments_text.strip():
        return {"title": "", "summary": "", "tags": [], "attributes": [], "action_items": []}
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
            "summary": normalize_summary_text(data.get("summary", "")),
            "tags": [str(t) for t in data.get("tags", [])],
            "attributes": data.get("attributes", []),
            "action_items": parse_action_items(data.get("action_items")),
        }
    except Exception:
        logger.exception("extract_final_attributes failed")
        return {"title": "", "summary": "", "tags": [], "attributes": [], "action_items": []}


def normalize_summary_text(raw: object) -> str:
    """LLM summary 归一化：LLM 有时返回多行字符串，有时返回 Python list 字面量
    （如 "['· a','· b']"）。统一剥壳成以 · 开头、每行一条要点的多行字符串，
    前端按行渲染要点列表，不再出现 `['· xxx','· yyy']` 一坨（2026-10-13 截图 bug）。
    """
    if isinstance(raw, list):
        parts = [str(x) for x in raw]
    else:
        s = str(raw).strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                import ast as _ast
                parsed = _ast.literal_eval(s)
                parts = [str(x) for x in parsed] if isinstance(parsed, list) else [s]
            except Exception:
                parts = [s]
        else:
            parts = [s]
    # 逐条清理引号/逗号残渣，确保 · 开头，过滤空行
    cleaned: list[str] = []
    for p in parts:
        p = str(p).strip().strip(",")
        if not p:
            continue
        # list 字面量残留的引号在 ast.literal_eval 已剥；纯字符串场景可能带首尾引号
        p = p.strip('"').strip("'").strip()
        if not p:
            continue
        cleaned.append(p if p.startswith("·") else f"· {p.lstrip('-•* ')}")
    return "\n".join(cleaned)


def parse_action_items(raw: object) -> list[dict]:
    """波次/会末 LLM 输出的 action_items 归一化：只保留 dict 项，补齐四字段为 str。"""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        out.append({
            "task": str(a.get("task", "")).strip(),
            "assignee": str(a.get("assignee", "")).strip(),
            "due": str(a.get("due", "")).strip(),
            "quote": str(a.get("quote", "")).strip(),
        })
    return out


def dedup_action_items(items: list[dict]) -> list[dict]:
    """最终总结待办清单 = 全部波次 action_items 拼接去重（quote 相同视为同一条，
    2026-09-28 用户定规）；quote 为空的条目退化为整串去重。"""
    seen: set[str] = set()
    out: list[dict] = []
    for a in items:
        quote = str(a.get("quote", ""))
        key = quote if quote else json.dumps(a, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def build_enhance_messages(ctx_summaries: list[str], prev_summary: str,
                           prev_lines: list[tuple[str, str]],
                           cur_lines: list[tuple[str, str]],
                           ocr_text: str = "") -> list[dict]:
    """一波一次合并调用（纠错本波 + 订正上一波文本/纪要 + 出本波段纪要）的提示词构造。

    ocr_text: 本波视觉 OCR 文本（线上会议屏幕内容），空串 = 无视觉上下文。
    """
    return prompts.build_enhance_messages(ctx_summaries, prev_summary, prev_lines,
                                          cur_lines, ocr_text=ocr_text)



def build_final_wave_messages(before_lines: list[tuple[str, str]],
                              cur_lines: list[tuple[str, str]],
                              after_lines: list[tuple[str, str]],
                              ocr_text: str = "") -> list[dict]:
    """会末版并行波（冗余上下文模式）的提示词构造，见 prompts.build_final_wave_messages。"""
    return prompts.build_final_wave_messages(before_lines, cur_lines, after_lines,
                                             ocr_text=ocr_text)
