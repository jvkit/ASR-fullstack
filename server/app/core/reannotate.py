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
import json
import logging
import wave
from pathlib import Path

import httpx
from sqlalchemy import delete, select

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, ReAnnotateUtterance, Utterance

logger = logging.getLogger(__name__)


def _is_punct(ch: str) -> bool:
    """中英文标点（FunASR timestamp 不含标点，对齐时跳过）。"""
    import unicodedata

    return unicodedata.category(ch).startswith("P") or ch.isspace()


async def reannotate(
    meeting_id: str,
    pcm: bytes,
    speaker_count: int = 0,
    name_threshold: float | None = None,
) -> None:
    """会末重标注主入口（session.stop 里 asyncio.create_task 调用）。失败仅记日志。

    speaker_count: 前端填写的参会人数（0=未填写）。>0 时直接作为 oracle；
    否则按 reannotate_oracle_mode 决定：auto=阿里自估（默认），
    auto_tune=gpu 按簇内紧度扫 2..6 选优，registered=声纹库人数+extra。
    name_threshold: 声纹匹配阈值（None=用 config 默认 reannotate_name_threshold）。
    """
    try:
        if len(pcm) / 32000 < settings.reannotate_min_sec:
            logger.info("reannotate skip %s: too short", meeting_id)
            return

        wav_full = _pcm_to_wav(pcm)
        # oracle 优先级：前端填写的参会人数 > 声纹库人数估计；策略见 reannotate_oracle_mode
        data: dict[str, str] = {}
        if speaker_count and 1 <= speaker_count <= 15:
            data["oracle_num"] = str(speaker_count)
        else:
            mode = settings.reannotate_oracle_mode
            if mode == "auto_tune":
                data["auto_tune"] = "true"
            elif mode == "registered":
                oracle = await _next_oracle()
                if oracle:
                    data["oracle_num"] = str(oracle)
            # mode == "auto"：不传 oracle，让阿里自估（实测效果最稳）
        thr = name_threshold if name_threshold and 0 < name_threshold < 1 else settings.reannotate_name_threshold
        async with httpx.AsyncClient(timeout=600) as cli:
            r = await cli.post(
                f"{settings.diarize_http_url}/diarize_labeled",
                files={"file": (f"{meeting_id}.wav", wav_full, "audio/wav")},
                data={**data, "name_threshold": str(thr)},
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
            # 字级时间戳：实时版句子的字级区间 → 按重标注段时间重叠分配到段上
            char_chars = await _load_char_chars(db, meeting_id)
            seg_texts = _assign_chars_to_segs(segs, char_chars)
            for i, seg in enumerate(segs):
                s0, s1 = seg[0], seg[1]
                name = seg[3] if len(seg) > 3 else ""
                db.add(ReAnnotateUtterance(
                    meeting_id=meeting_id,
                    seq=i,
                    speaker=name,
                    text=seg_texts[i],
                    start_ms=int(s0 * 1000),
                    end_ms=int(s1 * 1000),
                ))
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                meeting.reannotated = True
            await db.commit()

        logger.info("reannotate %s done", meeting_id)
    except Exception:
        logger.exception("reannotate failed for %s", meeting_id)


async def reannotate_for_meeting(
    meeting_id: str,
    speaker_count: int = 0,
    name_threshold: float | None = None,
) -> bool:
    """按 meeting_id 从盘上读录音重跑重标注（手动触发 / 历史会议补跑）。

    speaker_count: 手动触发时前端传入的参会人数（0=按声纹库估计）。
    name_threshold: 声纹匹配阈值（None=用 config 默认）。
    """
    wav = Path(settings.recordings_dir) / f"{meeting_id}.wav"
    if not wav.is_file():
        return False
    raw = wav.read_bytes()
    pcm = raw
    with contextlib.suppress(Exception):
        with wave.open(io.BytesIO(raw), "rb") as w:
            pcm = w.readframes(w.getnframes())
    await reannotate(meeting_id, pcm, speaker_count=speaker_count, name_threshold=name_threshold)
    return True


def _pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


async def _load_char_chars(db, meeting_id: str) -> list[tuple[str, int, int]]:
    """读实时版句子的字级时间戳 → [(char, start_ms, end_ms), ...]（按 seq 升序）。

    老会议（无字级时间戳）返回空列表：会后版文字为空，段照常落库。
    时间戳不含标点，标点继承前一字区间（与 session 端一致）。
    """
    rows = (await db.execute(
        select(Utterance).where(Utterance.meeting_id == meeting_id).order_by(Utterance.seq)
    )).scalars().all()
    chars: list[tuple[str, int, int]] = []
    for u in rows:
        try:
            ts = json.loads(u.char_timestamps or "[]")
        except json.JSONDecodeError:
            ts = []
        if not ts:
            continue
        prev = [int(ts[0][0]), int(ts[0][1])]
        idx = 0
        for ch in u.text:
            if idx < len(ts) and not _is_punct(ch):
                prev = [int(ts[idx][0]), int(ts[idx][1])]
                idx += 1
            chars.append((ch, prev[0], prev[1]))
    return chars


def _assign_chars_to_segs(segs: list[list], chars: list[tuple[str, int, int]]) -> list[str]:
    """按字级时间戳把实时转写的每个字分配到重叠最大的重标注段。

    chars 时间与 segs 时间同为会议绝对毫秒（gpu 端段偏移 patch 已对齐），
    每字取重叠最大的段；无重叠的字丢弃（对应段内无转写，正常现象）。
    返回与 segs 等长的段文本列表。
    """
    if not chars:
        return [""] * len(segs)
    spans = [(float(s[0]) * 1000, float(s[1]) * 1000) for s in segs]
    out: list[list[str]] = [[] for _ in segs]
    for ch, c0, c1 in chars:
        mid = (c0 + c1) / 2  # 字中点定位：边界字不劈两半，归中点所在段
        for i, (s0, s1) in enumerate(spans):
            if s0 <= mid < s1:
                out[i].append(ch)
                break
    return ["".join(cs) for cs in out]


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
