"""数据库表结构：会议 / 句子 / 段落总结 / 调试审计。

2026-09-29：从 SQLite 迁移到 MySQL（asyncmy），新增调试页所需中间产物表。
所有 JSON 字段以 Text 存储，default 用标量字符串，保持 MySQL/SQLite 兼容。
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# -----------------------------------------------------------------------------
# 基础业务表
# -----------------------------------------------------------------------------

class Meeting(Base):
    """会议元数据。"""

    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    title: Mapped[str] = mapped_column(String(200), default="未命名会议")
    recorder: Mapped[str] = mapped_column(String(64), default="")  # 采集人标识（弃用，兼容保留）
    status: Mapped[str] = mapped_column(String(16), default="live")  # live | ended
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 结束时 AI 全文提取的事实性属性：JSON list[{"key","value"}]
    attributes: Mapped[str] = mapped_column(Text, default='""')
    # 结束时 AI 归纳的会议标签：JSON list[str]
    tags: Mapped[str] = mapped_column(Text, default='"[]"')
    # 会末重标注是否完成
    reannotated: Mapped[bool] = mapped_column(default=False)
    # 双版本架构：live=只会中实时版 | final=只会末版 | both=两版都出
    version_mode: Mapped[str] = mapped_column(String(8), default="both")
    # 采集来源：mic=现场麦克风 | online=线上会议
    source_mode: Mapped[str] = mapped_column(String(8), default="mic")
    # 会末最终待办清单：JSON list[{task,assignee,due,quote}]
    action_items: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 调试/审计：整轨录音路径
    audio_raw_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_denoised_path: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Utterance(Base):
    """一句确定的话（实时版 / 会末重标注版共用结构）。"""

    __tablename__ = "utterances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 会议内序号
    speaker: Mapped[str] = mapped_column(String(64), default="")  # ASR 原始标签或重标注人名

    # 文本三态：原始 ASR、LLM 纠错后、当前展示（text 保持展示语义）
    raw_text: Mapped[str] = mapped_column(Text, default="")        # ASR 原文
    corrected_text: Mapped[str] = mapped_column(Text, default="")  # LLM 纠错后
    text: Mapped[str] = mapped_column(Text, default="")            # 当前生效文本

    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    # FunASR offline 字级时间戳 JSON：[[char, start_ms, end_ms], ...]
    char_timestamps: Mapped[str] = mapped_column(Text, default='"[]"')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    # 调试/审计
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | final | manual
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 该段音频文件
    diar_score: Mapped[float | None] = mapped_column(Float, nullable=True)      # 声纹匹配分


class ReAnnotateUtterance(Base):
    """会末重标注版句子：与实时版独立，互不纠正。"""

    __tablename__ = "utterances_re"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(64), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    corrected_text: Mapped[str] = mapped_column(Text, default="")
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    diar_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class Summary(Base):
    """一次滚动总结（波段纪要 / 会末总结）。"""

    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 第几次总结
    scope: Mapped[str] = mapped_column(String(8), default="live")  # live | final
    window_text: Mapped[str] = mapped_column(Text, default="")  # 本窗口送入的原文
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 本波注入的 OCR 文本
    content: Mapped[str] = mapped_column(Text, default="")      # 总结正文
    action_items: Mapped[str] = mapped_column(Text, default='"[]"')  # JSON list
    attributes: Mapped[str] = mapped_column(Text, default='"[]"')    # JSON list
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class VisualNote(Base):
    """视觉 OCR 文本存档 + 帧图路径。"""

    __tablename__ = "visual_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)  # 帧组首帧时间
    end_ms: Mapped[int] = mapped_column(Integer, default=0)    # 帧组末帧时间
    text: Mapped[str] = mapped_column(Text, default="")        # OCR markdown 记录
    frame_paths: Mapped[str] = mapped_column(Text, default='"[]"')  # JSON list[相对路径]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# -----------------------------------------------------------------------------
# 调试/审计中间产物表
# -----------------------------------------------------------------------------

class DiarSegment(Base):
    """说话人分离原始切段（3D-Speaker 或 VAD 子段）。

    与 utterances 区别：utterances 是最终分配好说话人/文字的句子；
    diar_segments 是分离器吐出来的原始声学区段，供调试页看"机器怎么切的"。
    """

    __tablename__ = "diar_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    cluster_label: Mapped[str] = mapped_column(String(32), default="")   # 聚类标签，如 spk0
    matched_speaker: Mapped[str] = mapped_column(String(64), default="") # 声纹匹配后的人名
    score: Mapped[float | None] = mapped_column(Float, nullable=True)    # 匹配分
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="final")     # final | live
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SummaryLlmLog(Base):
    """每波 LLM 调用全记录：prompt、原始输出、耗时、模型名。"""

    __tablename__ = "summary_llm_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)      # 对应 Summary.seq
    scope: Mapped[str] = mapped_column(String(8), default="live")  # live | final
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_json: Mapped[str] = mapped_column(Text, default="")     # 完整 messages JSON
    raw_response: Mapped[str] = mapped_column(Text, default="")    # LLM 原始返回
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)   # 调用耗时
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MeetingEvent(Base):
    """会议管道事件时间线：节点级埋点，定位黑盒卡在哪。"""

    __tablename__ = "meeting_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(32))  # 见 server/app/db/event_stages.py
    detail: Mapped[str] = mapped_column(Text, default='"{}"')  # JSON dict
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
