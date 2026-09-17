"""LLM 纠错效果 A/B 测试：同一批恶意文本，分别用当前提示词与新提示词测。

用法:
  cd server && ../.venv/bin/python scripts/test_llm_correction.py [--new]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from openai import OpenAI

# 恶意文本：同音/近音错别字 + 像人名的业务词错误（张商=招商）
CASES = [
    [("李迪", "我闷明天一起探讨一下这个回忆"),
     ("B", "好的，你滴老师")],
    [("王芳", "张商银行的贷款方案已经批了，下午去办手续"),
     ("李迪", "收到，我让我门财务对接一下")],
]


def build_messages_new(summary, prev_lines, cur_lines):
    from app.llm import prompts
    msgs = prompts.build_enhance_messages(summary, prev_lines, cur_lines)
    return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", action="store_true", help="使用 app.llm.prompts 当前版本（改完提示词后）")
    args = ap.parse_args()

    client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                    timeout=300, max_retries=0)
    from app.llm import prompts

    print(f"== prompt: {'当前线上版' if not args.new else '新版'} ==")
    print(f"== system prompt 前 200 字: {prompts.ENHANCE_SYSTEM[:200]}...\n")

    for i, cur in enumerate(CASES, 1):
        messages = prompts.build_enhance_messages("", [], cur)
        resp = client.chat.completions.create(
            model=settings.llm_model, messages=messages,
            temperature=0.0, response_format={"type": "json_object"})
        data = json.loads(resp.choices[0].message.content or "{}")
        print(f"--- case{i} 输入 ---")
        for spk, txt in cur:
            print(f"  {spk}: {txt}")
        print(f"--- case{i} LLM 输出 lines ---")
        for d in data.get("lines", []):
            print(f"  {d.get('speaker')}: {d.get('text')}")
        print()


if __name__ == "__main__":
    main()

# ---- 回归用例（新增于 2026-09-17 提示词强化后） ----
REGRESSION_CASES = [
    [("李迪", "我闷明天一起探讨一下这个回忆"),
     ("B", "好的，你滴老师")],
    [("王芳", "张商银行的贷款方案已经批了，下午去办手续"),
     ("李迪", "收到，我让我门财务对接一下")],
    [("冯忠俊", "我们部门明天开季度总结会"),
     ("蒋泽鹏", "收到，我把数句整理一下发你")],   # 数句->数据
]
