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


FINAL_ATTRIBUTES_SYSTEM = """你是一个企业会议纪要助手。用户给你一段会议的转写文本（带说话人标签，格式「说话人: 内容」）。

你的任务：从这段文本中提取会议的结构化属性，作为会议元信息。

- 会议主题：从内容归纳，不超过 20 字（如果这段文本不足以判断主题，给一个保守的概括）
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


CORRECTION_SYSTEM = """你是会议转写纠错助手。用户给你一段实时语音转写的原文（带说话人标签，格式「说话人: 内容」），可能存在同音字错误、专有名词错字、断句碎等问题。

你的任务：
1. 修正明显的同音字/错别字（如人名、公司内部名词，可参考上下文和已修正的前文）
2. 允许用（）补充说话人省略但语义明显的内容（如「（这个）方案」），让句子更完整可读；严禁大规模改写句意
3. 碎句如果语义上属于上一句的延续，合并进上一句；合并后说话人取较长一句的
4. 不确定的地方保持原文，宁可不动也不臆改

严格输出 JSON（不要输出其他内容）：
{"corrected": "修正后的全文，保持「说话人: 内容」每行一条，行数和顺序与输入对应"}"""


def build_correction_messages(context: str, summary: str, window_text: str) -> list[dict]:
    parts = []
    if summary:
        parts.append(f"【当前会议纪要要点（全局）】\n{summary}")
    if context:
        parts.append(f"【前文（已修正，仅作上下文参考，不要重复输出）】\n{context}")
    parts.append(f"【本段原文（待纠正）】\n{window_text}")
    user = "\n\n".join(parts)
    return [
        {"role": "system", "content": CORRECTION_SYSTEM},
        {"role": "user", "content": user},
    ]
