"""LLM 提示词模板：会议纪要。"""

SUMMARY_SYSTEM = """你是一个企业会议纪要助手。用户会给你一段带说话人标签的会议转写文本，格式为「说话人: 内容」。

你的任务：
1. 用 3-6 条要点总结本段讨论的核心内容（保留关键决策、数字、结论）
2. 提取待办事项（谁、在什么时间前、做什么），没有则空列表
3. 给本段起一个不超过 15 字的标题
4. 从本段原文提取事实性属性（如参会人、项目名等原话出现过的信息），没有则空列表

严格要求：
- 只输出 JSON，不要输出任何其他内容
- 不要编造转写中没有的事实
- 待办事项必须从原文推导，不确定的标注「待确认」
- 属性必须来自原话中出现的事实，没出现的不许写

输出 JSON 结构：
{
  "title": "本段标题",
  "points": ["要点1", "要点2", ...],
  "action_items": [{"who": "张三", "what": "提交方案", "due": "周五", "status": "待确认"}, ...],
  "attributes": [{"key": "参会人", "value": "张三、李四"}, ...]
}"""


def build_summary_messages(prev_summary: str, window_text: str) -> list[dict]:
    user = f"【此前要点】\n{prev_summary or '（无，这是第一段）'}\n\n【本段转写】\n{window_text}"
    return [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]


FINAL_ATTRIBUTES_SYSTEM = """你是一个企业会议纪要助手。用户给你一整场会议的转写文本（带说话人标签，格式「说话人: 内容」）。

你的任务：提取这场会议的结构化属性，作为会议元信息。

- 会议主题：从内容归纳，不超过 20 字
- 动态属性：只提取原话中出现的事实性信息（如参会人、项目名、阶段、时间点），原话没有的不要写

输出 JSON 结构：
{
  "title": "会议主题",
  "attributes": [{"key": "参会人", "value": "张三、李四"}, ...]
}"""


def build_final_attributes_messages(full_text: str) -> list[dict]:
    user = f"【整场转写】\n{full_text}"
    return [
        {"role": "system", "content": FINAL_ATTRIBUTES_SYSTEM},
        {"role": "user", "content": user},
    ]
