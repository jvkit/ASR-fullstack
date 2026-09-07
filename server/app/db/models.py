"""数据库表结构：会议 / 句子 / 段落总结。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    title: Mapped[str] = mapped_column(String(200), default="未命名会议")
    recorder: Mapped[str] = mapped_column(String(64), default="")  # 采集人标识（弃用，兼容保留）
    status: Mapped[str] = mapped_column(String(16), default="live")  # live | ended
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 结束时 AI 全文提取的事实性属性：JSON list[{"key","value"}]（主题/时间外的动态字段）
    attributes: Mapped[str] = mapped_column(Text, default="")
    # 会末重标注是否完成（阿里 pipeline 切分 + ERes2NetV2 声纹标身份）
    reannotated: Mapped[bool] = mapped_column(default=False)


class Utterance(Base):
    """一句确定的话（FunASR offline 结果，已带说话人）。"""

    __tablename__ = "utterances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 会议内序号
    speaker: Mapped[str] = mapped_column(String(32), default="")  # ASR 分离出的标签，如 spk0
    text: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Summary(Base):
    """一次滚动总结（每分钟窗口）。"""

    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 第几次总结
    window_text: Mapped[str] = mapped_column(Text)  # 本窗口送入的原文（可审计）
    content: Mapped[str] = mapped_column(Text)  # 总结正文
    action_items: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    attributes: Mapped[str] = mapped_column(Text, default="[]")  # 本段事实性属性 JSON list
    title: Mapped[str] = mapped_column(String(200), default="")  # 本段标题
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ReAnnotateUtterance(Base):
    """会末重标注版句子：阿里 pipeline 切分 + ERes2NetV2 声纹标身份（与实时版并存）。"""

    __tablename__ = "utterances_re"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 会议内序号
    speaker: Mapped[str] = mapped_column(String(32), default="")  # 注册名 / 说话人N
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
