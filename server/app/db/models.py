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
    # 结束时 AI 归纳的会议标签：JSON list[str]
    tags: Mapped[str] = mapped_column(Text, default="[]")
    # 会末重标注是否完成（阿里 pipeline 切分 + ERes2NetV2 声纹标身份）
    reannotated: Mapped[bool] = mapped_column(default=False)
    # 双版本架构（2026-09-28 用户拍板）：实时版/会末版独立管道互不纠正。
    # version_mode: live=只会中实时版 | final=只会末版 | both=两版都出
    version_mode: Mapped[str] = mapped_column(String(8), default="both")
    # 采集来源：mic=现场麦克风 | online=线上会议（屏幕共享帧走视觉通道）
    source_mode: Mapped[str] = mapped_column(String(8), default="mic")
    # 会末最终总结的待办清单（JSON list[{task,assignee,due,quote}]）：
    # live-only 存实时版聚合值；final/both 以会末版为准（用户拍板 2026-09-28）
    action_items: Mapped[str] = mapped_column(Text, default="[]")


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
    # FunASR offline 字级时间戳 JSON：[[char, start_ms, end_ms], ...]（会后版按字分配文字用）
    char_timestamps: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Summary(Base):
    """一次滚动总结（每分钟窗口）。"""

    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 第几次总结
    # 归属版本（双版本架构 2026-09-28）：live=实时波段纪要 | final=会末波段纪要/会末总结。
    # 终版总结固定 seq=0；两版段纪要各自 1..N（scope 区分，不撞号语义）
    scope: Mapped[str] = mapped_column(String(8), default="live")
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
    text: Mapped[str] = mapped_column(Text, default="")  # 按字级时间戳分配到本段的文字
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class VisualNote(Base):
    """视觉 OCR 文本存档（2026-09-28 双版本架构）：每波/每 2min 周期一组帧的 OCR 结果。

    实时版由 enhancer 波次驱动落库；final-only 模式由 session 的 120s 定时任务落库。
    会末版按时间范围 [start_ms, end_ms] 关联复用（不重复跑 OCR）——双版本只复用
    视觉结果，互不纠正。
    """

    __tablename__ = "visual_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(String(36), index=True)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)  # 帧组首帧时间（会议内相对 ms）
    end_ms: Mapped[int] = mapped_column(Integer, default=0)    # 帧组末帧时间
    text: Mapped[str] = mapped_column(Text, default="")        # OCR markdown 记录
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
