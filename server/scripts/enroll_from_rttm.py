"""从 AISHELL-4 rttm 标注抽每个人的干净时间段，注册进生产声纹库。

模拟真实部署的「会前已录声纹」形态：说话人=库里的人，测试时才有真名归属。

名字来源（--intro-sec，默认 60）：
    这类会议开头有点名自我介绍（"010 教师""012 厨师""013 保安"）。
    对开场前 N 秒转写一次，按时间与 rttm 说话人对齐，从每人自己的话语里
    解析「编号+职务」作为注册名（如 010-教师）；解析失败回退 rttm id。

抽取策略：每人在会中取最长且不在开头的段落，取其中段（避开边界），≥3s。

用法（项目根）:
    python server/scripts/enroll_from_rttm.py <wav> <rttm> [--intro-sec 60]

示例:
    python server/scripts/enroll_from_rttm.py eval/aishell4/M_R003S01C01_ch0.wav \
        eval/aishell4/test/TextGrid/M_R003S01C01.rttm

注意：名字里的下划线会替换为 -（pcm 文件名按 _ 前缀分组）；映射表存
eval/<rttm_stem>/enroll_map.json，DER 评分和后续排查都用它。
重复注册同一人会追加新段（多段质心，通常更稳）；清理用
`curl -X DELETE /api/speakers/<name>`。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import wave
from pathlib import Path

import httpx

API = "http://127.0.0.1:8010"
GPU = "http://192.168.30.87:10096"

CN_DIGIT = {"零": "0", "幺": "1", "一": "1", "二": "2", "两": "2", "三": "3",
            "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
FILLER = "呃嗯啊哦就呢吧是我把这那个有"
# 开场转写常见误听 -> 正字（自我介绍职务）
ROLE_ALIAS = {"教室": "教师", "除师": "厨师", "老帅": "老师", "张伤": "张叔",
              "教帅": "教师", "厨帅": "厨师", "宝安": "保安"}


def load_rttm(path: Path) -> dict[str, list[tuple[float, float]]]:
    """rttm -> {spk: [(start_s, dur_s)]}"""
    segs: dict[str, list[tuple[float, float]]] = {}
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            segs.setdefault(p[7], []).append((float(p[3]), float(p[4])))
    return segs


def transcribe_intro(wav: str, sec: float) -> list[dict]:
    """GPU /meeting_chunk 转写开场 N 秒 -> [{start,end,text}]（只要文字和时间）。"""
    with wave.open(wav, "rb") as w:
        sr, sw, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        if sr != 16000 or sw != 2 or ch != 1:
            raise SystemExit(f"wav 需 16kHz/int16/mono（实际 {sr}/{sw * 8}bit/{ch}ch）")
        frames = w.readframes(int(sec * sr))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(frames)
    r = httpx.post(f"{GPU}/meeting_chunk", files={"file": ("intro.wav", buf.getvalue(), "audio/wav")},
                   timeout=300)
    r.raise_for_status()
    return r.json().get("segs", [])


def parse_intro_name(texts: list[str]) -> str | None:
    """从一个人开场话语里解析「编号+职务」-> "010-教师"；失败返回 None。

    归一化中文数字（零幺一..九）后找 3 位数字串，取其后 1~4 个汉字作职务。
    """
    joined = "".join(texts)
    norm = "".join(CN_DIGIT.get(c, c) for c in joined)
    norm = re.sub(r"[，。、！？\s]", "", norm)
    m = re.search(r"(?<!\d)(\d{3})(?!\d)([%s]{0,3}[一-龥]{1,4})" % FILLER, norm)
    if not m:
        return None
    role = m.group(2).strip(FILLER)[:5]
    role = ROLE_ALIAS.get(role, role)
    return f"{m.group(1)}-{role}" if role else m.group(1)


def pick_clip(wav: str, start: float, dur: float, min_len: float, max_len: float) -> bytes | None:
    """从 [start, start+dur] 取中段 min(dur-0.2, max_len) 秒（≥min_len 才取），返回 PCM。"""
    take = min(dur - 0.2, max_len)
    if take < min_len:
        return None
    s = start + (dur - take) / 2  # 居中，避开边界
    with wave.open(wav, "rb") as w:
        w.setpos(int(s * 16000))
        return w.readframes(int(take * 16000))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("rttm")
    ap.add_argument("--intro-sec", type=float, default=60.0, help="开场自我介绍时长上限（0=不解析，直接用 rttm id）")
    ap.add_argument("--min-len", type=float, default=3.0)
    ap.add_argument("--max-len", type=float, default=6.0)
    ap.add_argument("--skip-after", type=float, default=2.0, help="跳过开场 N 秒内的段（开场常有点名/杂音）")
    args = ap.parse_args()

    rttm_path = Path(args.rttm)
    segs = load_rttm(rttm_path)
    print(f"rttm 说话人 {len(segs)} 个: {sorted(segs)}", flush=True)

    # 1) 开场转写 + 按时间对齐 -> 每人的自我介绍文本
    intro_texts: dict[str, list[str]] = {k: [] for k in segs}
    if args.intro_sec > 0:
        print(f"转写开场 {args.intro_sec:.0f}s 解析自我介绍…", flush=True)
        intro = transcribe_intro(args.wav, args.intro_sec)
        for spk, spans in segs.items():
            for s, d in spans:
                if s >= args.intro_sec:
                    continue
                for g in intro:
                    ov = min(s + d, g["end"]) - max(s, g["start"])
                    if ov > (g["end"] - g["start"]) * 0.5:
                        intro_texts[spk].append(g.get("text", ""))

    # 2) 每人定名字 + 抽注册段
    names: dict[str, str] = {}
    ok, fail = 0, []
    for spk in sorted(segs):
        name = parse_intro_name(intro_texts[spk]) or spk.replace("_", "-")
        names[spk] = name
        cands = sorted(
            ((s, d) for s, d in segs[spk] if s >= args.skip_after),
            key=lambda x: -x[1],
        )
        clip = None
        for s, d in cands:
            clip = pick_clip(args.wav, s, d, args.min_len, args.max_len)
            if clip:
                break
        if not clip:
            fail.append(name)
            print(f"  {spk} -> {name}: 无 ≥{args.min_len}s 的可用段，跳过", flush=True)
            continue
        r = httpx.post(f"{API}/api/speakers/enroll", params={"name": name}, content=clip, timeout=60)
        if r.status_code == 200 and r.json().get("ok"):
            ok += 1
            print(f"  {spk} -> {name}: 已注册（{len(clip) / 32000:.1f}s 中段）", flush=True)
        else:
            fail.append(name)
            print(f"  {spk} -> {name}: 注册失败 {r.status_code} {r.text[:120]}", flush=True)

    # 3) 映射表落盘（DER 评分/排查用）
    out = Path("eval") / rttm_path.stem / "enroll_map.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rttm_to_name": names}, ensure_ascii=False, indent=2), encoding="utf-8")

    lst = httpx.get(f"{API}/api/speakers", timeout=10).json()
    print(f"\n成功 {ok}，失败 {fail}")
    print(f"映射表：{out}")
    print(f"当前库：{lst.get('speakers')}")


if __name__ == "__main__":
    main()
