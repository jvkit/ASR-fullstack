"""听感台生成器：为每个（音频 × 系统分支）生成 按说话人合并试听 wav + index.html。

- 每个块：原始整段 wav（可播）+ 每个系统标签的合并试听 wav（该标签所有段时间序拼接，
  段间插 0.2s 提示音 beep）+ 段列表表 + GT 时间线条。
- 全部相对路径，Windows 直接打开 out/bench/index.html 可听。
用法: python scripts/make_bench_html.py   （块配置在 BLOCKS，路径相对 scd_bench/）
"""
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]  # scd_bench/
BENCH = ROOT / "out" / "bench"
AUDIO = BENCH / "audio"
FRAME_SR = 16000
BEEP_SEC = 0.2
COLORS = ["#e94560", "#0f7b6c", "#e67e22", "#3498db", "#9b59b6", "#f1c40f",
          "#1abc9c", "#e74c3c", "#2980b9", "#8e44ad", "#d35400", "#27ae60"]

BLOCKS = [
    {
        "id": "rotate_m30", "title": "rotate_m30_s5 短裁判件（M 原生 6 人×30s，240s，GTCRN 降噪版）× 3D-Speaker",
        "wav": BENCH / "rotate_m30_s5_denoised.wav",
        "sys": BENCH / "diar_rotate_m30" / "rotate_m30_s5_denoised.rttm",
        "gt": ROOT.parent / "eval" / "shuffled" / "rotate_m30_s5.rttm",
        "highlight": True, "sysname": "3D-Speaker+GTCRN（DER 7.66%，6/6 标签 6/6 映射，每人独立簇）",
    },
    {
        "id": "rotate40", "title": "rotate40 裁判件（15 轮 6 人轮换，2400s，GTCRN 降噪版）× 3D-Speaker",
        "wav": BENCH / "rotate40_s3_denoised.wav",
        "sys": BENCH / "diar_rotate40" / "rotate40_s3_denoised.rttm",
        "gt": ROOT.parent / "eval" / "shuffled" / "rotate40_s3.rttm",
        "highlight": True, "sysname": "3D-Speaker 聚类分支 + GTCRN（DER 19.9%，6/6 标签，双男声合并）",
    },
    {
        "id": "splice6", "title": "无缝拼接压力件（6 场 × 12s 零间隙，坟场测试）",
        "wav": BENCH / "splice6.wav",
        "sys": BENCH / "diar_splice6" / "splice6.rttm",
        "gt": BENCH / "splice6_gt.rttm", "highlight": True, "sysname": "3D-Speaker 聚类分支",
    },
    {
        "id": "splice6_baseline", "title": "无缝拼接压力件 × 自研管线（基线对照）",
        "wav": BENCH / "splice6.wav",
        "sys": BENCH / "diar_splice6_baseline" / "splice6_baseline.rttm",
        "gt": BENCH / "splice6_gt.rttm", "highlight": True, "sysname": "自研管线（基线）",
    },
    {
        "id": "shuffle", "title": "无缝打散正面对决 m_rr_gap0_s7（6 人整句零间隙轮转，872s/118 次换人）× 3D-Speaker",
        "wav": ROOT.parent / "eval" / "shuffled" / "m_rr_gap0_s7.wav",
        "sys": BENCH / "diar_shuffle" / "m_rr_gap0_s7.rttm",
        "gt": ROOT.parent / "eval" / "shuffled" / "m_rr_gap0_s7.rttm",
        "highlight": True, "sysname": "3D-Speaker 聚类分支",
    },
    {
        "id": "shuffle_baseline", "title": "无缝打散正面对决 × 自研管线（基线）",
        "wav": ROOT.parent / "eval" / "shuffled" / "m_rr_gap0_s7.wav",
        "sys": BENCH / "diar_shuffle_baseline" / "shuffle_baseline.rttm",
        "gt": ROOT.parent / "eval" / "shuffled" / "m_rr_gap0_s7.rttm",
        "highlight": True, "sysname": "自研管线（基线，整片只出 1 标签）",
    },
    {
        "id": "M_0m_denoised", "title": "5min 切片 GTCRN 降噪版（实验B：降噪公平赛，DER 4.77% vs 生肉 26.21%）",
        "wav": BENCH / "M_R003S01C01_0m_denoised.wav",
        "sys": BENCH / "diar_M_0m_denoised" / "M_R003S01C01_0m_denoised.rttm",
        "gt": BENCH / "gt_M_R003S01C01_0m.rttm", "highlight": False, "sysname": "3D-Speaker 聚类分支",
    },
    {
        "id": "M_0m", "title": "5min 切片 M_R003S01C01_0m（基线 DER 12.3% 同件）",
        "wav": ROOT / "eval_src_dummy",  # 占位，下面替换
        "sys": BENCH / "diar_M_0m" / "M_R003S01C01_0m.rttm",
        "gt": BENCH / "gt_M_R003S01C01_0m.rttm", "highlight": False, "sysname": "3D-Speaker 聚类分支",
    },
    {
        "id": "M_full", "title": "M 会全曲 M_R003S01C01_ch0（38min）",
        "wav": ROOT / "eval_src_dummy",
        "sys": BENCH / "diar_M_full" / "M_R003S01C01_ch0.rttm",
        "gt": ROOT.parent / "eval" / "aishell4" / "test" / "TextGrid" / "M_R003S01C01.rttm",
        "highlight": False, "sysname": "3D-Speaker 聚类分支",
    },
    {
        "id": "L_full", "title": "L 会全曲 L_R004S01C01_ch0（39min）",
        "wav": ROOT / "eval_src_dummy",
        "sys": BENCH / "diar_L_full" / "L_R004S01C01_ch0.rttm",
        "gt": ROOT.parent / "eval" / "aishell4" / "test" / "TextGrid" / "L_R004S01C01.rttm",
        "highlight": False, "sysname": "3D-Speaker 聚类分支",
    },
]
BLOCKS[7]["wav"] = ROOT.parent / "eval" / "aishell4" / "chunks" / "M_R003S01C01_0m.wav"
BLOCKS[8]["wav"] = ROOT.parent / "eval" / "aishell4" / "M_R003S01C01_ch0.wav"
BLOCKS[9]["wav"] = ROOT.parent / "eval" / "aishell4" / "L_R004S01C01_ch0.wav"


def load_rttm(path):
    segs = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            st, dur = float(p[3]), float(p[4])
            segs.append((st, st + dur, p[7]))
    return sorted(segs)


def beep():
    t = np.arange(int(BEEP_SEC * FRAME_SR)) / FRAME_SR
    w = 0.25 * np.sin(2 * np.pi * 880 * t)
    fade = int(0.005 * FRAME_SR)
    w[:fade] *= np.linspace(0, 1, fade)
    w[-fade:] *= np.linspace(1, 0, fade)
    return w


def speaker_wav(wav_path, segs, spk, out_path):
    data, sr = sf.read(wav_path, dtype="float32")
    if data.ndim == 2:
        data = data[:, 0]
    assert sr == FRAME_SR
    b = beep()
    parts = []
    for st, ed, s in segs:
        if s != spk:
            continue
        parts.append(data[int(st * sr):int(ed * sr)])
        parts.append(b)
    if not parts:
        return None
    sf.write(out_path, np.concatenate(parts), sr, subtype="PCM_16")
    return out_path


def timeline_html(segs, total, gt=True):
    """水平堆叠条：每段一个色块，宽度=时长占比。"""
    body = []
    for st, ed, spk in segs:
        left, w = st / total * 100, (ed - st) / total * 100
        body.append(
            f'<div title="{spk} {st:.1f}-{ed:.1f}s" style="position:absolute;'
            f'left:{left:.2f}%;width:{max(w,0.15):.2f}%;top:0;height:100%;'
            f'background:{COLORS[hash(spk) % len(COLORS)]};"></div>'
        )
    label = "GT" if gt else "系统"
    return (f'<div style="margin:4px 0"><span style="font-size:12px;color:#9ad">{label} '
            f'{len(segs)}段</span><div style="position:relative;height:14px;background:#0a0a1a;'
            f'border-radius:4px;overflow:hidden">{"".join(body)}</div></div>')


def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    cards = []
    for blk in BLOCKS:
        if not Path(blk["sys"]).exists():
            print(f"[skip] {blk['id']}: 缺系统输出 {blk['sys']}")
            continue
        bdir = AUDIO / blk["id"]
        bdir.mkdir(parents=True, exist_ok=True)
        # 原始整段（复制进 bench，保证相对路径自包含）
        orig_dst = bdir / "orig.wav"
        if not orig_dst.exists():
            shutil.copyfile(blk["wav"], orig_dst)
        sys_segs = load_rttm(blk["sys"])
        gt_segs = load_rttm(blk["gt"])
        total = max([e for _, e, _ in sys_segs] + [e for _, e, _ in gt_segs])

        spks = sorted({s for _, _, s in sys_segs})
        items = []
        for spk in spks:
            dur = sum(ed - st for st, ed, s in sys_segs if s == spk)
            out = bdir / f"spk_{spk}.wav"
            speaker_wav(blk["wav"], sys_segs, spk, out)
            items.append(
                f'<div class="item"><span class="tag" style="background:{COLORS[hash(spk) % len(COLORS)]}">'
                f'{spk}</span> 累计 {dur:.1f}s <audio controls src="audio/{blk["id"]}/spk_{spk}.wav"></audio></div>'
            )
        rows = "".join(
            f"<tr><td>{st:.2f}</td><td>{ed:.2f}</td><td>{ed-st:.2f}</td><td>{spk}</td></tr>"
            for st, ed, spk in sys_segs[:400]
        )
        hl = 'style="border:2px solid #e94560"' if blk["highlight"] else ""
        badge = '<span class="tag">⚠️ 坟场测试：跨缝是否合并</span>' if blk["highlight"] else ""
        cards.append(f"""
<div class="card" {hl}>
  <b>{blk['title']}</b> {badge} <span style="color:#789;font-size:12px">{blk['sysname']}</span>
  <div class="item"><span class="tag" style="background:#444">整段</span>
    <audio controls src="audio/{blk['id']}/orig.wav"></audio></div>
  {''.join(items)}
  <details><summary>段列表（{len(sys_segs)} 段，前 400）</summary>
    <table><tr><th>起</th><th>止</th><th>时长</th><th>标签</th></tr>{rows}</table></details>
  {timeline_html(gt_segs, total, gt=True)}
  {timeline_html(sys_segs, total, gt=False)}
</div>""")
        print(f"[ok] {blk['id']}: {len(sys_segs)} 段, {len(spks)} 标签")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>说话人分离擂台赛 听感台</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 900px;
         margin: 0 auto; padding: 16px; background: #1a1a2e; color: #eee; }}
  .card {{ background: #16213e; border-radius: 12px; padding: 16px; margin: 12px 0; }}
  .item {{ background: #0f3460; border-radius: 8px; padding: 10px 12px; margin-top: 10px; }}
  .tag {{ display: inline-block; background: #e94560; border-radius: 4px;
          padding: 1px 8px; font-size: 12px; margin-right: 8px; }}
  audio {{ width: 100%; margin-top: 6px; }}
  table {{ border-collapse: collapse; font-size: 12px; margin-top: 8px; width: 100%; }}
  td, th {{ border: 1px solid #345; padding: 2px 8px; text-align: left; }}
  h1 {{ font-size: 20px; }} summary {{ cursor: pointer; color: #9ad; margin-top: 8px; }}
</style></head><body>
<h1>🎙️ 说话人分离擂台赛 听感台（3D-Speaker 聚类分支 vs 自研基线）</h1>
<p style="color:#9ad;font-size:13px">每块：整段音频 → 按说话人合并试听（段间 0.2s beep）→ 段列表 →
GT 时间线（上） vs 系统时间线（下）。红框 = 无缝拼接坟场测试。EEND 分支核查不存在，未参评。</p>
{''.join(cards)}
</body></html>"""
    (BENCH / "index.html").write_text(html, encoding="utf-8")
    print(f"-> {BENCH / 'index.html'}")


if __name__ == "__main__":
    main()
