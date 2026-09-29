"""会议管道事件 stage 常量。

用于 MeetingEvent.stage，保持统一命名，避免各处拼写不一致。
"""

# 会议生命周期
MEETING_START = "meeting_start"
MEETING_END = "meeting_end"

# 音频预处理
DENOISE_START = "denoise_start"
DENOISE_END = "denoise_end"

# 说话人分离（会末 / 会中批次）
DIAR_START = "diar_start"
DIAR_END = "diar_end"

# ASR / 重标注
ASR_START = "asr_start"
ASR_END = "asr_end"
REANNOTATE_START = "reannotate_start"
REANNOTATE_END = "reannotate_end"

# 视觉 OCR
OCR_START = "ocr_start"
OCR_END = "ocr_end"

# 波段 LLM
LLM_WAVE_START = "llm_wave_start"
LLM_WAVE_END = "llm_wave_end"
LLM_WAVE_FAIL = "llm_wave_fail"

# 会末总结
FINAL_SUMMARY_START = "final_summary_start"
FINAL_SUMMARY_END = "final_summary_end"

# 广播/推送
BROADCAST = "broadcast"

# 错误/异常
ERROR = "error"
