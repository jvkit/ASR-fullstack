"""会议会话：一次会议的完整生命周期编排（非实时批次架构）。

数据流：
    前端音频 → 累积缓冲 ──每 30~45s 一批──▶ 本地 GTCRN 降噪 + 3D-Speaker 聚类
        → GPU /identify_segments（簇级声纹认人 + 逐段 ASR + 标点 + ITN）
        → 带说话人的句子：落库 + 广播 → 批次增强（LLM 纠错 + 滚动纪要）
    会末：同一端点全场重跑 → 权威终版（utterances_re）

批次切点用 GPU /vad 找最后一个完整句尾，避免把句子从中间切断；
会末 flush 把尾巴音频（不足一批）也送出去，不丢结尾内容。

双版本架构（2026-09-28 用户拍板）：实时版（live，会中批次→波次 LLM→段纪要）
与会末版（final，全录音重跑→波次 LLM→段纪要→会末最终总结）是独立管道、互不
纠正，只复用声纹库/视觉/录音。version_mode=live|final|both 控制本场出哪版；
final-only 模式跳过会中批次（不送 GPU/不出 transcript/不跑 enhancer，音频照常
累积存档），视觉 OCR 改由 120s 定时任务驱动（见 _visual_timer_loop）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

from fastapi import WebSocket

from app.config import settings, load_hotwords
from app.core.audio_storage import save_raw_recording
from app.core.enhancer import BatchEnhancer
from app.core.gpu_pipeline import meeting_chunk_3ds, vad_ends
from app.core.segment import UtteranceBuffer
from app.db import event_stages
from app.db.database import SessionLocal
from app.db.events import log_event
from app.db.models import DiarSegment, Meeting, Summary, Utterance, title_is_default
from app.llm import client as llm_client

logger = logging.getLogger(__name__)

# 方案 B（2026-09-22）：会中不做匿名聚类，会中/会末共用 GPU identify_segments 的声纹认人。
# 未命中声纹库时 GPU 输出兜底号"说话人N"（全场一次分离，编号稳定可区分），前端原样保留
# 区分度；仅无意义标签（空/待定/旧版"陌生人N"聚类发号）降级"未识别"。
UNKNOWN_SPEAKER = "未识别"


def display_speaker(spk: str, *, keep_anon: bool = False) -> str:
    """GPU 侧标签 → 前端显示标签。

    keep_anon=False（会中默认，2026-09-28 用户拍板）：匿名兜底号（说话人N/陌生人N）
    与空/待定统一降级"未识别"，不区分未注册个体。
    keep_anon=True（会末版）：说话人N 原样保留——会末版的卖点就是区分人，
    未注册个体间保持可区分（101段 vs 27段 = 两个不同的人）。
    """
    if not spk or spk in ("待定",) or spk.startswith("陌生人"):
        return UNKNOWN_SPEAKER
    import re
    if re.fullmatch(r"说话人\d+", spk):
        return spk if keep_anon else UNKNOWN_SPEAKER
    return spk


class MeetingSession:
    def __init__(self, meeting_id: str, title: str, recorder: str,
                 replay_of: str | None = None, client_replay: bool = False,
                 online: bool = False, version_mode: str = "both",
                 source_mode: str = "mic", hotwords_extra: str = "") -> None:
        self.meeting_id = meeting_id
        self.title = title
        self.recorder = recorder
        self.replay_of = replay_of  # 非空=服务端重放：音频来自 recordings/<id>.wav
        self.client_replay = client_replay  # 客户端脚本重放（音频由客户端喂，仅打标记）
        self.replay_done = asyncio.Event()  # 重放音频喂完置位（供 ws 循环感知自动结束）
        self.clients: set[WebSocket] = set()
        self.buffer = UtteranceBuffer()
        self._stopped = False
        self._lock = asyncio.Lock()

        # 双版本架构（2026-09-28 用户拍板）：live=只会中实时版 | final=只会末版 |
        # both=两版都出。非法值回退 both——旧前端/重放脚本不传新字段时行为与改造前一致
        self.version_mode = version_mode if version_mode in ("live", "final", "both") else "both"
        # 采集来源：mic=现场麦克风 | online=线上会议（屏幕共享帧走视觉通道）
        self.source_mode = source_mode if source_mode in ("mic", "online") else "mic"

        # 视觉通道帧缓冲（线上会议/摄像头帧收发，波次触发时 take_group）
        from app.core.visual_buffer import VisualBuffer
        self.visual = VisualBuffer(meeting_id)
        # 视觉提示词应用层（None=无视觉；"online"=屏幕共享，"offline"=摄像头）。
        # 线上=屏幕模板；线下=摄像头模板（2026-09-29 起线下摄像头帧进 OCR，白板/PPT 投屏入纪要）
        self.visual_app: str | None = "online" if online else "offline"
        self._visual_persist_task: asyncio.Task | None = None  # 后台 OCR 落库（wait_visual_drained 收割）

        self.enhancer = BatchEnhancer(self)

        self.paused = False  # 前端暂停：丢弃上行音频（连接保持）
        self.final_title = ""
        self.final_attributes: list[dict] = []  # 累积的事实性属性（key 去重合并）
        self._audio = bytearray()      # 累计上行 PCM（int16/16kHz/mono），结束时写 wav
        self._processed = 0            # 已送出批次的字节偏移
        self._chunk_failures = 0       # /meeting_chunk 连续失败次数
        self._batch_loop_task: asyncio.Task | None = None
        self._visual_timer_task: asyncio.Task | None = None  # final-only 模式视觉 OCR 节拍
        # live 收尾与会末版并行（2026-10-01 gather）时，meeting 元信息（标题/标签/属性/待办）
        # 的读-改-写竞争锁：两边都是 merge 语义 + 同一把锁，先后提交都收敛
        self._meta_lock = asyncio.Lock()
        # 热词（FunASR 按空白切分；全局词库 + 本场配置弹窗追加，逗号格式转空格）
        _hw = f"{load_hotwords()},{hotwords_extra}" if hotwords_extra.strip() else load_hotwords()
        self._hotwords = _hw.replace(",", " ")
        # 方案 B（2026-09-22 用户拍板）：会中杀掉自研匿名聚类。会中只做声纹库认人：
        # GPU 回传的"说话人N"兜底号原样保留区分度，无意义标签由 display_speaker 降级。
        # 旧状态 anon_clusters/anon_next 已删（self-check：无引用残留）。

    # ---------- 生命周期 ----------

    @property
    def stopped(self) -> bool:
        """公开只读：会话是否已结束（REST 收帧接口用它拒收停止后的帧）。"""
        return self._stopped

    def _merge_anon(self, anon: list[dict]) -> None:
        """方案 B 后为空操作（保留方法防外部引用断裂；旧匿名状态已删）。

        旧版：GPU 每批回传全量匿名簇快照，跨批持久化实现"陌生人N 跨批次稳定"。
        2026-09-22 起会中不再做匿名聚类，anon 状态无需跨批维护。
        """
        return

    async def start(self) -> None:
        async with SessionLocal() as db:
            db.add(Meeting(id=self.meeting_id, title=self.title, recorder=self.recorder,
                           version_mode=self.version_mode, source_mode=self.source_mode))
            await db.commit()
        await log_event(self.meeting_id, event_stages.MEETING_START,
                        version_mode=self.version_mode,
                        source_mode=self.source_mode)
        # 双版本架构（2026-09-28）：live/both 走会中批次循环；final-only 跳过会中处理
        # （不送 GPU、不出 transcript、不跑 enhancer），音频只累积存档 + 视觉改由
        # 120s 定时任务驱动 OCR（final 模式的会末版按时间范围复用这些 VisualNote）
        if self.version_mode in ("live", "both"):
            self._batch_loop_task = asyncio.create_task(self._batch_loop())
        if self.version_mode == "final" and settings.visual_enabled and self.visual_app:
            self._visual_timer_task = asyncio.create_task(self._visual_timer_loop())
        logger.info("meeting %s started (mode=%s/%s, nonrealtime batches: min=%ss max=%ss tick=%ss)",
                    self.meeting_id, self.version_mode, self.source_mode,
                    settings.pipeline_batch_min_sec,
                    settings.pipeline_batch_max_sec, settings.pipeline_tick_sec)

    async def stop(self) -> None:
        """结束会议：立刻落库 ended 并广播，重活全部转后台。

        同步段（用户等待的部分）：存录音 → 落库 ended + 广播。
        后台段（_finalize，模式感知）：live → flush 尾巴批次 + 实时段纪要归约终版；
        final → 全场重跑 + 会末波次 + 会末最终总结（见 final_pass）。
        LLM 网关抽风时这些步骤慢，但用户点结束立即看到结束。
        """
        async with self._lock:
            if self._stopped:
                return
            self._stopped = True

        if self._batch_loop_task:
            self._batch_loop_task.cancel()
            try:
                await self._batch_loop_task
            except asyncio.CancelledError:
                pass

        if self._visual_timer_task:
            self._visual_timer_task.cancel()
            try:
                await self._visual_timer_task
            except asyncio.CancelledError:
                pass
        # 注意：剩余视觉帧不在此同步冲刷（OCR 分钟级，卡 ended）。
        # 帧由 _wait_visual_drained 在会末 final_pass 波次前统一落库（后台，用户无感）。

        self._save_recording()

        async with SessionLocal() as db:
            meeting = await db.get(Meeting, self.meeting_id)
            if meeting:
                meeting.status = "ended"
                meeting.ended_at = datetime.now()
                await db.commit()
        await log_event(self.meeting_id, event_stages.MEETING_END)
        await self.broadcast({"type": "ended", "meeting_id": self.meeting_id})
        logger.info("meeting %s ended (finalization continues in background)", self.meeting_id)

        asyncio.create_task(self._finalize())

    async def _finalize(self) -> None:
        """会末收尾（后台，2026-09-28 起模式感知）：live → 尾巴批 flush + 实时段纪要
        归约终版（scope='live'）；final → 全场重跑 + 会末波次 + 会末最终总结
        （见 final_pass.run_final_pass）。两版独立管道互不纠正，各自独立 try，
        一边失败不影响另一边。失败仅记日志。
        2026-10-01 起两路 gather 并行（互不依赖，省下 live 收尾的串行等待）；
        meeting 元信息写入由 self._meta_lock + merge 语义保证并发收敛。"""
        session_id = self.meeting_id

        async def _live() -> None:
            try:
                await self._finalize_live(session_id)
            except Exception:
                logger.exception("meeting %s live finalize failed", session_id)

        async def _final() -> None:
            from app.core.final_pass import _set_final_status, run_final_pass
            try:
                await run_final_pass(self)
            except Exception:
                logger.exception("meeting %s final pass failed", session_id)
                await _set_final_status(session_id, "failed")

        tasks = []
        if self.version_mode in ("live", "both"):
            tasks.append(_live())
        if self.version_mode in ("final", "both"):
            tasks.append(_final())
        if tasks:
            await asyncio.gather(*tasks)

    async def _finalize_live(self, session_id: str) -> None:
        """实时版终版：flush 尾巴批 → enhancer.finish() → 段纪要归约出标题/总结/标签/属性。

        逻辑与双版本改造前完全一致，仅多落 scope='live'、Summary/广播带结构化待办、
        meeting.action_items 存实时版聚合值（final/both 模式会由会末版覆盖为准）。
        """
        # 尾巴音频分块 flush（每块最多 60s）：高速重放时积压可能有几分钟，
        # 一口吞会超时；普通会议通常一块就够
        while (len(self._audio) - self._processed) / 32 >= 1000:  # 剩余 ≥1s
            before = self._processed
            await self._run_batch(force=True, max_chunk_ms=60_000)
            if self._processed == before:
                break  # 失败重试无进展（连续失败 3 次后丢弃），防死循环

        # 会末总结：输入 = 全部段纪要拼接（不含原始转写全文）。
        # 旧实现在此有「全文 >6000 字就丢掉全文、只用滚动摘要」的硬兜底——
        # 现已不需要：段纪要归约本身就是分层压缩，输入规模 = 波数 × 每波纪要，
        # 与会议原始文本量解耦（6h 会议约 94k token，chat 模型 256k 窗口内）。
        segments_text = await self.enhancer.finish()
        result = await llm_client.extract_final_attributes(segments_text)

        final_title = result["title"]
        # 重放测试会议：标题保留「重放测试」标记（LLM 生成标题后仍追加，便于区分）
        if (self.replay_of or self.client_replay) and final_title and "重放测试" not in final_title:
            final_title = f"{final_title}（重放测试）"
        # LLM 失败兜底：直接用段纪要拼接当总结（信息全在，只是没归约）
        final_summary = llm_client.normalize_summary_text(result["summary"]) or segments_text
        final_tags = result["tags"][:settings.final_tags_count]
        # 属性：滚动提取的 + 会末提取的合并（key 去重，滚动版优先）
        merged: dict[str, str] = {}
        for a in result["attributes"]:
            if isinstance(a, dict) and a.get("key"):
                merged[str(a["key"])] = str(a.get("value", ""))
        for a in self.final_attributes:
            merged[a["key"]] = a["value"]
        # 结构化待办（2026-09-28）：LLM 输出的 action_items 已按 quote 去重
        live_actions = llm_client.dedup_action_items(result.get("action_items") or [])

        async with self._meta_lock, SessionLocal() as db:
            meeting = await db.get(Meeting, session_id)
            if meeting:
                # 用户手填的标题永不覆盖；只有默认占位标题才允许 AI 提炼覆盖
                if final_title and title_is_default(meeting.title):
                    meeting.title = final_title
                if final_tags:
                    meeting.tags = json.dumps(final_tags, ensure_ascii=False)
                if merged:
                    # merge 语义（与会末版 gather 并行不丢 key）：live 优先，同 key 覆盖
                    existing = {
                        str(kv["key"]): str(kv.get("value", ""))
                        for kv in json.loads(meeting.attributes or "[]")
                        if isinstance(kv, dict) and kv.get("key")
                    }
                    existing.update(merged)
                    meeting.attributes = json.dumps(
                        [{"key": k, "value": v} for k, v in existing.items()],
                        ensure_ascii=False,
                    )
                # live-only 时这就是权威待办；final/both 由会末版覆盖（用户拍板）
                meeting.action_items = json.dumps(live_actions, ensure_ascii=False)
                await db.commit()

        # 会末大总结落一条 Summary（可审计、供报告导出）。
        # **seq=0 是终版专用号**：段纪要是 1..N，故不撞号，且 order_by(seq) 让
        # 终版排在最前（先总结后分章，前端 MeetingDetail 也是按 seq===0 认终版）。
        # 旧实现用 session 自有的 summary_seq（从 0 起 +1 → 永远是 1），既与第一段
        # 纪要撞号，也让前端的 seq===0 判断从未生效。
        if final_summary:
            async with SessionLocal() as db:
                db.add(Summary(
                    meeting_id=session_id,
                    seq=0,
                    scope="live",     # 实时版终版（会末版终版 scope='final'，见 final_pass）
                    window_text="",   # 终版输入是段纪要，不是原文窗口
                    content=final_summary,
                    action_items=json.dumps(live_actions, ensure_ascii=False),
                    attributes="[]",
                    title=final_title or "最终总结",
                ))
                await db.commit()
            await self.broadcast({
                "type": "summary",
                "seq": 0,
                "scope": "live",
                "final": True,
                "title": final_title or "最终总结",
                "points": [p.lstrip("· ").strip() for p in final_summary.splitlines() if p.strip()],
                "action_items": live_actions,
                "attributes": [{"key": k, "value": v} for k, v in merged.items()],
            })
        logger.info("meeting %s live finalized", session_id)

    async def _visual_timer_loop(self) -> None:
        """final-only 模式的视觉 OCR 节拍器（2026-09-28）。

        final 模式没有会中波次，enhancer 不驱动 take_group，视觉 OCR 缺节拍——这里
        每 visual_note_interval_sec（默认 120s）取走缓冲帧转后台任务 OCR 落库
        （cancel timer 不打断落库；final_pass 前 wait_visual_drained 统一收割），
        作为会末版按时间范围关联复用的唯一 OCR 来源。失败仅记日志（与实时版一致，
        不阻塞任何主流程）；暂停期间不打拍。
        """
        try:
            while not self._stopped:
                await asyncio.sleep(settings.visual_note_interval_sec)
                if self.paused or not self.visual_app:
                    continue
                try:
                    frames = await self.visual.take_group()
                    if not frames:
                        continue
                    # 落库转后台任务：stop 立即返回不等 OCR；final_pass 前会 drain 完
                    self._visual_persist_task = asyncio.create_task(self._ocr_and_persist(frames))
                except Exception:
                    logger.exception("meeting %s visual timer tick failed（不阻塞）",
                                     self.meeting_id)
        except asyncio.CancelledError:
            raise

    async def _ocr_and_persist(self, frames: list) -> None:
        """一组帧 → OCR → 落 VisualNote。独立后台任务，cancel timer 不影响它。"""
        try:
            from app.core.enhancer import persist_visual_note
            from app.core.visual_ocr import visual_ocr
            text = await visual_ocr(frames, app=self.visual_app)
            if text:
                await persist_visual_note(self.meeting_id,
                                          frames[0][0], frames[-1][0], text)
                logger.info("meeting %s visual note %d-%dms (%d 帧, %d 字)",
                            self.meeting_id, frames[0][0], frames[-1][0],
                            len(frames), len(text))
        except Exception:
            logger.exception("meeting %s visual persist failed（不阻塞）", self.meeting_id)

    async def wait_visual_drained(self) -> None:
        """会末波次前调用：等后台 OCR 落库完成 + 把缓冲里剩余帧也落库。

        final_pass 的 _load_visual_notes 按时间窗查 VisualNote，必须先于查询完成，
        否则 timer 取走但没来得及落库的帧对波次不可见（2026-09-29 竞态修复）。
        """
        t = self._visual_persist_task
        if t is not None:
            try:
                await t
            except Exception:
                pass
        frames = await self.visual.take_group()
        if frames:
            await self._ocr_and_persist(frames)

    # ---------- 音频上行与批次调度 ----------

    async def handle_audio(self, pcm: bytes) -> None:
        if self._stopped or self.paused:
            return
        self._audio.extend(pcm)

    async def _batch_loop(self) -> None:
        """定时成批：攒够 pipeline_batch_min_sec 后按 VAD 句尾切点送 3D 换芯路径
        （gpu_pipeline.meeting_chunk_3ds）。"""
        try:
            while not self._stopped:
                await asyncio.sleep(settings.pipeline_tick_sec)
                if self.paused:
                    continue
                await self._run_batch()
        except asyncio.CancelledError:
            raise

    async def _run_batch(self, force: bool = False, max_chunk_ms: int | None = None) -> None:
        """切一批音频送 GPU 管线，句子落库 + 广播 + 驱动增强。

        force=True（会末 flush）：剩余音频全送，不管大小和切点；
        max_chunk_ms 限定单块上限（flush 用，防高速重放下积压音频一口吞超时）。
        失败不推进 _processed（下轮重试）；连续失败 3 次丢弃这段防积压。
        """
        total = len(self._audio)
        if total <= self._processed:
            return
        pcm = bytes(self._audio[self._processed:])
        pending_ms = len(pcm) / 32

        if force:
            cut_ms = pending_ms
            if max_chunk_ms and cut_ms > max_chunk_ms:
                cut_ms = float(max_chunk_ms)
        else:
            if pending_ms < settings.pipeline_batch_min_sec * 1000:
                return
            ends = await vad_ends(pcm)
            if ends is None:
                return  # VAD 失败：下轮重试
            cut_ms = ends[-1] if ends else None
            if not cut_ms or cut_ms < 1000:
                if pending_ms < settings.pipeline_batch_max_sec * 1000:
                    return  # 还没有完整句子且未到强制上限：再等等
                cut_ms = settings.pipeline_batch_max_sec * 1000  # 长句/长静音：硬切
            cut_ms = min(cut_ms, pending_ms)

        chunk = pcm[:int(cut_ms * 32)]
        await log_event(self.meeting_id, event_stages.DENOISE_START)
        from app.core.preprocess import denoise_async
        denoised_chunk = await denoise_async(chunk)
        await log_event(self.meeting_id, event_stages.DENOISE_END,
                        chunk_ms=int(cut_ms))
        await log_event(self.meeting_id, event_stages.DIAR_START,
                        chunk_ms=int(cut_ms))
        # shield：stop 取消批次循环时，在飞的 GPU 请求仍跑完（否则 flush 会重复送）
        # P1 换芯（2026-09-28）：会中走 3D-Speaker（降噪→本地3D聚类→GPU identify_segments），
        # 无声纹统一"未识别"；meeting_chunk（自研 VAD）保留供回滚不再被调用。
        res = await asyncio.shield(meeting_chunk_3ds(denoised_chunk, self._hotwords))
        await log_event(self.meeting_id, event_stages.DIAR_END,
                        segs=len(res[0]) if res else 0)
        if res is None:
            self._chunk_failures += 1
            if self._chunk_failures >= 3 and not force:
                logger.warning(
                    "meeting %s: chunk failed %d times, dropping %.1fs audio",
                    self.meeting_id, self._chunk_failures, pending_ms / 1000,
                )
                self._processed = total
                self._chunk_failures = 0
            return
        segs, _anon = res  # anon 已弃用（方案 B：会中不做匿名聚类）
        self._chunk_failures = 0
        base_ms = self._processed // 32
        self._processed += len(chunk)
        if not segs:
            return

        seqs: list[int] = []
        for i, s in enumerate(segs):
            text = (s.get("text") or "").strip()
            if not text:
                continue  # 空段不展示（碎段已在 GPU 侧参与打标/修剪）
            start_ms = int(base_ms + float(s["start"]) * 1000)
            end_ms = int(base_ms + float(s["end"]) * 1000)
            raw_spk = s.get("speaker") or ""
            score = float(s["score"]) if s.get("score") is not None else None
            margin = float(s["margin"]) if s.get("margin") is not None else None
            # 声纹压线误匹配过滤（2026-09-29）：score 刚过阈值但 margin 极小，
            # 说明是"两个都不太像但稍微像一点"，强制降级未识别，避免 B 站视频男声
            # 被标成冯忠俊（285639543705 实测 score=0.4046 margin=0.0909）。
            if (
                score is not None
                and margin is not None
                and score >= settings.reannotate_name_threshold
                and margin < settings.pipeline_min_identify_margin
                and raw_spk not in ("未识别",)
                and not raw_spk.startswith("说话人")
            ):
                logger.info(
                    "meeting %s reject low-margin match: %s score=%.3f margin=%.3f < %.3f",
                    self.meeting_id, raw_spk, score, margin,
                    settings.pipeline_min_identify_margin,
                )
                raw_spk = "未识别"
            spk = display_speaker(raw_spk)
            item = await self.buffer.add(spk, text, start_ms, end_ms)
            async with SessionLocal() as db:
                db.add(Utterance(
                    meeting_id=self.meeting_id,
                    seq=item.seq,
                    speaker=item.speaker,
                    raw_text=text,
                    text=item.text,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    char_timestamps="[]",
                    source="live",
                    diar_score=score,
                    diar_margin=margin,
                ))
                db.add(DiarSegment(
                    meeting_id=self.meeting_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    cluster_label=str(s.get("cluster_id") or ""),
                    matched_speaker=spk,
                    score=score,
                    diar_margin=margin,
                    source="live",
                ))
                await db.commit()
            await self.broadcast({
                "type": "transcript",
                "seq": item.seq,
                "speaker": item.speaker,
                "text": item.text,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
            })
            logger.info(
                "meeting %s seq=%d spk=%r %d-%dms %s",
                self.meeting_id, item.seq, item.speaker, item.start_ms, item.end_ms, item.text,
            )
            seqs.append(item.seq)

        if seqs:
            self.enhancer.on_batch(seqs)

    # ---------- 重放测试（音频来自历史录音，按真实节奏喂入） ----------

    async def run_replay(self, wav_path: str) -> None:
        """把历史录音按 0.1s 节奏喂进会话，模拟真实开会。

        暂停时挂起（不丢节拍，恢复后继续），stop 后立即退出。
        喂完置位 replay_done，由 ws 循环触发正常结束流程。
        """
        import wave

        CHUNK_SEC = 0.1
        try:
            with wave.open(wav_path, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                    await self.broadcast({"type": "error", "message": "录音格式异常（需 16kHz/int16/mono）"})
                    self.replay_done.set()
                    return
                total = wf.getnframes()
                logger.info("meeting %s replay start: %.1f min", self.meeting_id, total / 16000 / 60)
                sent = 0
                while sent < total and not self._stopped:
                    if self.paused:
                        await asyncio.sleep(0.2)
                        continue
                    chunk = wf.readframes(1600)  # 0.1s
                    if not chunk:
                        break
                    await self.handle_audio(chunk)
                    sent += len(chunk) // 2
                    await asyncio.sleep(CHUNK_SEC)
                logger.info("meeting %s replay fed: %.1f/%.1f min", self.meeting_id,
                            sent / 16000 / 60, total / 16000 / 60)
        except FileNotFoundError:
            await self.broadcast({"type": "error", "message": f"录音文件不存在: {wav_path}"})
        finally:
            self.replay_done.set()

    # ---------- 总结 ----------

    def _save_recording(self) -> None:
        """把累计的 PCM 写成 wav（16kHz/int16/mono，与上行音频一致）。"""
        if not self._audio:
            return
        try:
            import wave
            from pathlib import Path

            out_dir = Path(settings.recordings_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{self.meeting_id}.wav"
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(self._audio))
            # 调试产物：原始整轨也落 data/debug/{meeting_id}/raw.wav
            raw_rel = save_raw_recording(self.meeting_id, bytes(self._audio))
            asyncio.create_task(self._save_recording_path(raw_rel))
            logger.info("meeting %s recording saved: %s (%.1f min)",
                        self.meeting_id, out_path, len(self._audio) / 32000 / 60)
        except Exception:
            logger.exception("meeting %s save recording failed", self.meeting_id)

    async def _save_recording_path(self, raw_rel: str) -> None:
        try:
            async with SessionLocal() as db:
                meeting = await db.get(Meeting, self.meeting_id)
                if meeting:
                    meeting.audio_raw_path = raw_rel
                    await db.commit()
        except Exception:
            logger.exception("save recording path failed")

    # ---------- 广播 ----------

    async def broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(msg, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class SessionManager:
    """meeting_id → MeetingSession 注册表。"""

    def __init__(self) -> None:
        self._sessions: dict[str, MeetingSession] = {}

    def create(self, title: str, recorder: str, replay_of: str | None = None,
               client_replay: bool = False, online: bool = False,
               version_mode: str = "both", source_mode: str = "mic",
               hotwords_extra: str = "") -> MeetingSession:
        meeting_id = uuid.uuid4().hex[:12]
        session = MeetingSession(meeting_id, title, recorder, replay_of=replay_of,
                                 client_replay=client_replay, online=online,
                                 version_mode=version_mode, source_mode=source_mode,
                                 hotwords_extra=hotwords_extra)
        self._sessions[meeting_id] = session
        return session

    def get(self, meeting_id: str) -> MeetingSession | None:
        return self._sessions.get(meeting_id)

    def remove(self, meeting_id: str) -> None:
        self._sessions.pop(meeting_id, None)


session_manager = SessionManager()
