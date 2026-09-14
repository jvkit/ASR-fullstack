"""声纹库两两相似度矩阵：回答"同事之间的声纹差值一般是多少"。

逻辑与生产 diarize_svc.service._load_db_multi 完全一致：
每人多段 pcm 各算 ERes2NetV2 embedding（L2 归一化）→ 平均质心 → 再归一化，
然后两两算余弦相似度。另外输出每段对其本人质心的相似度（自匹配带）。

用法(GPU):  cd ~/jvkit/asr && venv/bin/python ai-script/pairwise_sv.py [--dir <声纹目录>]
"""

import argparse
import itertools
from pathlib import Path

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/speaker_enrolls")
    ap.add_argument("--out", default="", help="导出 xlsx 路径（如 --out eval/xx.xlsx）")
    args = ap.parse_args()

    from funasr import AutoModel

    ere = AutoModel(
        model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
        init_param="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt",
        device="cpu", disable_pbar=True, disable_log=True,
    )

    by_name: dict[str, list[Path]] = {}
    for p in sorted(Path(args.dir).iterdir()):
        if p.suffix == ".pcm":
            name = p.stem.rsplit("_", 1)[0] if "_" in p.stem else p.stem
            by_name.setdefault(name, []).append(p)

    centroids: dict[str, np.ndarray] = {}
    seg_sims: dict[str, list[float]] = {}  # 每段对本人质心的相似度
    for name, paths in sorted(by_name.items()):
        vecs, sims = [], []
        for p in paths:
            pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
            if len(pcm) < 16000 * 1.5:
                print(f"  [skip] {p.name}: <1.5s")
                continue
            e = np.asarray(ere.generate(input=pcm, fs=16000, extract_embedding=True)[0]["spk_embedding"],
                           dtype=np.float32).ravel()
            e /= np.linalg.norm(e) + 1e-9
            vecs.append(e)
        if not vecs:
            continue
        c = np.mean(vecs, axis=0)
        c /= np.linalg.norm(c) + 1e-9
        centroids[name] = c
        seg_sims[name] = [float(np.dot(v, c)) for v in vecs]

    names = sorted(centroids)
    print(f"\n=== 自匹配带（每段对本人质心的相似度）===")
    for n in names:
        ss = ", ".join(f"{s:.3f}" for s in seg_sims[n])
        print(f"  {n:12s} ({len(seg_sims[n])}段): {ss}")

    print(f"\n=== 两两相似度矩阵（{len(names)} 人）===")
    header = "            " + "  ".join(f"{n[:4]:>6s}" for n in names)
    print(header)
    pairs = []
    for a, b in itertools.combinations(names, 2):
        s = float(np.dot(centroids[a], centroids[b]))
        pairs.append((s, a, b))
    for a in names:
        row = f"  {a[:10]:10s}  "
        for b in names:
            if a == b:
                row += "     - "
            else:
                s = float(np.dot(centroids[a], centroids[b]))
                row += f"{s:6.3f} "
        print(row)

    print(f"\n=== 每人一览：与库内其他人的相似度（区间/平均/中位 + 最像的人）===")
    print(f"  {'人名':12s} {'区间(min~max)':>16s} {'平均':>7s} {'中位':>7s}  最像的人(值)")
    for a in names:
        sims_a = sorted(((float(np.dot(centroids[a], centroids[b])), b) for b in names if b != a))
        vals_a = [s for s, _ in sims_a]
        top_s, top_b = sims_a[-1]
        print(f"  {a:12s} {min(vals_a):6.3f} ~ {max(vals_a):5.3f} {float(np.mean(vals_a)):7.3f} {float(np.median(vals_a)):7.3f}  {top_b}({top_s:.3f})")

    pairs.sort(reverse=True)
    print(f"\n=== 最像的 5 对（危险对，越接近自匹配带越难分）===")
    for s, a, b in pairs[:5]:
        print(f"  {s:.3f}  {a} ~ {b}")
    print(f"\n=== 分布统计 ===")
    vals = [s for s, _, _ in pairs]
    print(f"  两两相似度: min={min(vals):.3f}  median={float(np.median(vals)):.3f}  max={max(vals):.3f}")
    mm = [s for s, a, b in pairs if a.split('-')[-1] == 'M' and b.split('-')[-1] == 'M']
    mf = [s for s, a, b in pairs if (a.split('-')[-1] == 'M') != (b.split('-')[-1] == 'M')]
    ff = [s for s, a, b in pairs if a.split('-')[-1] == 'F' and b.split('-')[-1] == 'F']
    if mm: print(f"  男-男对 ({len(mm)}): median={float(np.median(mm)):.3f}  max={max(mm):.3f}")
    if mf: print(f"  男-女对 ({len(mf)}): median={float(np.median(mf)):.3f}  max={max(mf):.3f}")
    if ff: print(f"  女-女对 ({len(ff)}): median={float(np.median(ff)):.3f}  max={max(ff):.3f}")

    if args.out:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        bold = Font(bold=True)

        ws = wb.active
        ws.title = "每人一览"
        ws.append(["人名", "区间min", "区间max", "平均", "中位", "最像的人", "最像值"])
        for c in ws[1]:
            c.font = bold
        for a in names:
            sims_a = sorted(((float(np.dot(centroids[a], centroids[b])), b) for b in names if b != a))
            vals_a = [s for s, _ in sims_a]
            top_s, top_b = sims_a[-1]
            ws.append([a, round(min(vals_a), 3), round(max(vals_a), 3),
                       round(float(np.mean(vals_a)), 3), round(float(np.median(vals_a)), 3),
                       top_b, round(top_s, 3)])

        ws2 = wb.create_sheet("两两矩阵")
        ws2.append([""] + names)
        for c in ws2[1]:
            c.font = bold
        for a in names:
            ws2.append([a] + [None if a == b else round(float(np.dot(centroids[a], centroids[b])), 3)
                              for b in names])

        ws3 = wb.create_sheet("分布统计")
        ws3.append(["指标", "值"])
        for c in ws3[1]:
            c.font = bold
        ws3.append(["两两min", round(min(vals), 3)])
        ws3.append(["两两median", round(float(np.median(vals)), 3)])
        ws3.append(["两两max", round(max(vals), 3)])
        ws3.append(["男-男median", round(float(np.median(mm)), 3) if mm else None])
        ws3.append(["男-女median", round(float(np.median(mf)), 3) if mf else None])
        ws3.append(["女-女median", round(float(np.median(ff)), 3) if ff else None])
        ws3.append([])
        ws3.append(["最像的5对", "相似度"])
        for c in ws3[ws3.max_row]:
            c.font = bold
        for s, a, b in pairs[:5]:
            ws3.append([f"{a} ~ {b}", round(s, 3)])

        wb.save(args.out)
        print(f"[xlsx] saved: {args.out}")


if __name__ == "__main__":
    main()
