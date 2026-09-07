"""会末重标注：完整录音 -> gpu diarize_labeled（切分+ERes2NetV2 标身份）-> 落库。

流程（方案 v2 M1）：
1. 会议结束后异步触发（不阻塞 ended 广播）
2. 录音整包上传 gpu:10096 /diarize_labeled；人数 = 本场声纹库人数 +1（宁多勿少，
   最多 6）。gpu 端：阿里 pipeline 切分 -> 每段 ERes2NetV2 embedding 对声纹库投票
   -> 簇多数票定名（同名簇自动合并，人数估错自愈）
3. 结果写 utterances_re 表（与实时版并存），meeting.reannotated = True
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import wave
from pathlib import Path

import httpx
from sqlalchemy import delete

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, ReAnnotateUtterance

logger = logging.getLogger(__name__)


async def reannotate(meeting_id: str, pcm: bytes, speaker_count: int = 0) -> None:
    """会末重标注主入口（session.stop 里 asyncio.create_task 调用）。失败仅记日志。

    speaker_count: 前端填写的参会人数（0=未填写）。>0 时直接作为 oracle，
    否则退回「声纹库人数 + extra」的旧策略。
    """
    try:
        if len(pcm) / 32000 < settings.reannotate_min_sec:
            logger.info("reannotate skip %s: too short", meeting_id)
            return

        wav_full = _pcm_to_wav(pcm)
        # oracle 优先级：前端填写的参会人数 > 声纹库人数估计
        if speaker_count and 1 <= speaker_count <= 15:
            oracle = speaker_count
        else:
            oracle = await _next_oracle()

        async with httpx.AsyncClient(timeout=600) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/diarize_labeled",
                files={"file": (f"{meeting_id}.wav", wav_full, "audio/wav")},
                data={"oracle_num": oracle or ""},
            )
            r.raise_for_status()
            diar = r.json()

        segs: list[list] = diar["segs"]
        logger.info(
            "reannotate %s: %d segs, oracle=%s, registered=%s",
            meeting_id, len(segs), diar.get("used_oracle"), diar.get("registered"),
        )

        async with SessionLocal() as db:
            await db.execute(
                delete(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == meeting_id)
            )
            for i, seg in enumerate(segs):
                s0, s1 = seg[0], seg[1]
                name = seg[3] if len(seg) > 3 else ""
                db.add(ReAnnotateUtterance(
                    meeting_id=meeting_id,
                    seq=i,
                    speaker=name,
                    start_ms=int(s0 * 1000),
                    end_ms=int(s1 * 1000),
                ))
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                meeting.reannotated = True
            await db.commit()

        # 回写：把重标注的说话人映射回实时版 utterances（按时间重叠匹配段），
        # 逐句转写（实时版）因此与重标注区块一致，不再有第三个匿名说话人。
        await _apply_to_utterances(meeting_id, segs)
        logger.info("reannotate %s done", meeting_id)
    except Exception:
        logger.exception("reannotate failed for %s", meeting_id)


async def reannotate_for_meeting(meeting_id: str, speaker_count: int = 0) -> bool:
    """按 meeting_id 从盘上读录音重跑重标注（手动触发 / 历史会议补跑）。

    speaker_count: 手动触发时前端传入的参会人数（0=按声纹库估计）。
    """
    wav = Path(settings.recordings_dir) / f"{meeting_id}.wav"
    if not wav.is_file():
        return False
    raw = wav.read_bytes()
    pcm = raw
    with contextlib.suppress(Exception):
        with wave.open(io.BytesIO(raw), "rb") as w:
            pcm = w.readframes(w.getnframes())
    await reannotate(meeting_id, pcm, speaker_count=speaker_count)
    return True


def _pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


async def _apply_to_utterances(meeting_id: str, segs: list[list]) -> int:
    """把重标注说话人回写实时版 utterances（逐句转写不再有第三个说话人）。

    句子时间戳已是会议绝对毫秒（gpu 端段偏移 patch），因此用时间重叠逐句
    匹配重标注段；无重叠（时间戳缺失的老会议）时退回白名单收敛策略。
    """
    from app.db.models import Utterance

    spans = [
        (int(s[0] * 1000), int(s[1] * 1000), (s[3] if len(s) > 3 else ""))
        for s in segs
    ]
    if not spans:
        return 0

    def match(u_start: int, u_end: int) -> str | None:
        """与句子时间重叠最大的重标注段名；重叠须过半句时长。"""
        best, best_ov = None, 0
        for s0, s1, name in spans:
            ov = min(u_end, s1) - max(u_start, s0)
            if ov > best_ov:
                best, best_ov = name, ov
        if best_ov <= 0 or not best:
            return None
        if (u_end - u_start) > 0 and best_ov / (u_end - u_start) < 0.5:
            return None
        return best

    async with SessionLocal() as db:
        result = await db.execute(
            Utterance.__table__.select().where(Utterance.meeting_id == meeting_id)
        )
        rows = result.fetchall()

        # 时间戳有效的句子走精确匹配
        stamped = [r for r in rows if r.end_ms > 0]
        unstamped = [r for r in rows if r.end_ms <= 0]
        updated = 0
        for row in stamped:
            name = match(row.start_ms, row.end_ms)
            if name and name != row.speaker:
                await db.execute(
                    Utterance.__table__.update()
                    .where(Utterance.id == row.id)
                    .values(speaker=name)
                )
                updated += 1

        # 无时间戳（老会议）：白名单收敛（保底，不再出第三说话人）
        if unstamped:
            from collections import Counter
            dur: Counter = Counter()
            for s0, s1, name in spans:
                if name:
                    dur[name] += s1 - s0
            whitelist = set(dur.keys())
            if len(whitelist) == 1:
                primary = whitelist.pop()
            elif len(whitelist) >= 2:
                primary = dur.most_common(1)[0][0]
            else:
                primary = None
            if primary:
                for row in unstamped:
                    spk = row.speaker or ""
                    if spk and spk in whitelist:
                        continue
                    if spk != primary:
                        await db.execute(
                            Utterance.__table__.update()
                            .where(Utterance.id == row.id)
                            .values(speaker=primary)
                        )
                        updated += 1
        await db.commit()
        logger.info("reannotate %s: applied to %d/%d utterances", meeting_id, updated, len(rows))
        return updated


async def _next_oracle() -> int | None:
    """人数估计：声纹库人数 +1（宁多勿少）；库为空则 None（gpu 端自动模式）。"""
    from app.core.speaker_client import speaker_control

    try:
        res = await speaker_control({"mode": "speaker_list"})
        n = len(res.get("speakers", []))
    except Exception:
        logger.exception("speaker_list failed, fallback to auto oracle")
        return None
    if n <= 0:
        return None
    return min(n + settings.reannotate_oracle_extra, settings.reannotate_max_oracle)
