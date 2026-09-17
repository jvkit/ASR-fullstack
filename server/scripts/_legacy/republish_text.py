"""重填基准块的子段文字：TextGrid 标注按字时间均摊到子段。

chunk_eval 的旧逻辑按「重叠最多」整段贴，长标注(10-20s)压垮单个子段、
其余全空。本脚本：标注 [x0,x1] 内第 i 个字的时间 = x0+(i+0.5)/n*(x1-x0)，
落到包含该时刻的子段。先打印验证，--apply 才写库。

用法: python scripts/republish_text.py <meeting> <start_min> [--apply]
"""
import asyncio
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval" / "aishell4"


def parse_textgrid_words(tg_path: Path, t0: float) -> list[tuple[str, float, float, str]]:
    words = []
    cur_tier = None
    lines = [l.strip() for l in tg_path.read_text(encoding="utf-8", errors="ignore").splitlines()]
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("name = "):
            cur_tier = l.split('"')[1]
        elif l.startswith("xmin = ") and i + 2 < len(lines) and "text = " in lines[i + 2]:
            try:
                x0 = float(l.split("=")[1]) - t0
                x1 = float(lines[i + 1].split("=")[1]) - t0
                txt = lines[i + 2].split('"')[1].strip()
                if txt and cur_tier:
                    words.append((cur_tier, x0, x1, txt))
            except (ValueError, IndexError):
                pass
            i += 2
        i += 1
    return words


def distribute(words: list, segs: list[dict]) -> list[str]:
    """segs: [{start_ms,end_ms}] -> 每段文字（按字时间均摊，无重复无遗漏）"""
    texts = [[] for _ in segs]
    for _, x0, x1, txt in words:
        n = len(txt)
        if n == 0 or x1 <= x0:
            continue
        for i, ch in enumerate(txt):
            ct = x0 + (i + 0.5) / n * (x1 - x0)  # 字中心时刻
            for j, s in enumerate(segs):
                if s["start_ms"] / 1000 <= ct < s["end_ms"] / 1000:
                    texts[j].append(ch)
                    break
    return ["".join(t) for t in texts]


def main() -> None:
    mid, start_m = sys.argv[1], float(sys.argv[2])
    apply_ = "--apply" in sys.argv
    chunk_id = f"chunk_{mid}_{int(start_m)}m"
    cdir = EVAL_DIR / "chunks" / f"{mid}_{int(start_m)}m"
    segs = json.loads((cdir / "segs.json").read_text())
    words = parse_textgrid_words(EVAL_DIR / "test" / "TextGrid" / f"{mid}.TextGrid", start_m * 60)
    texts = distribute(words, segs)

    print("=== 验证（前 24 段）===")
    for s, t in list(zip(segs, texts))[:24]:
        print("  %3d-%3ds %-6s %s" % (s["start_ms"] // 1000, s["end_ms"] // 1000, s["speaker"], t[:30]))
    cov = sum(1 for t in texts if t.strip())
    print(f"有字子段 {cov}/{len(segs)}")

    if not apply_:
        print("\n(未写库，验证无误后加 --apply)")
        return

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import ReAnnotateUtterance

    async def _w() -> None:
        async with SessionLocal() as db:
            rows = (await db.execute(
                select(ReAnnotateUtterance).where(ReAnnotateUtterance.meeting_id == chunk_id)
                .order_by(ReAnnotateUtterance.seq))).scalars().all()
            for r, t in zip(rows, texts):
                r.text = t
            await db.commit()
    asyncio.run(_w())
    print("已写库:", chunk_id)


if __name__ == "__main__":
    main()
