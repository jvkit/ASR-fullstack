"""无缝拼接压力件生成器：N 场不同会议各取前 K 秒，零间隙首尾相接。

GT 构造：源 rttm 在 [0,K) 内的行，截断到 K、加累计时间偏移、说话人 ID 加
源会议名前缀（aishell4 说话人编号跨会议重复，不前缀会把不同人合成一个 GT 人）。

用法: python scripts/make_splice.py <out_wav> <out_rttm> <K_sec> <stem1> <stem2> ...
源音频: ../eval/aishell4/test/wav/<stem>.flac  源 GT: ../eval/aishell4/test/TextGrid/<stem>.rttm
"""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval" / "aishell4"


def main():
    out_wav, out_rttm, k_sec = sys.argv[1], sys.argv[2], float(sys.argv[3])
    stems = sys.argv[4:]
    assert len(stems) >= 2

    pieces, gt_lines = [], []
    sr_ref = None
    for i, stem in enumerate(stems):
        wav_path = EVAL_DIR / "test" / "wav" / f"{stem}.flac"
        rttm_path = EVAL_DIR / "test" / "TextGrid" / f"{stem}.rttm"
        data, sr = sf.read(wav_path, dtype="float32")
        if data.ndim == 2:
            data = data[:, 0]  # aishell4 flac 为 8 通道，取 ch0（与 *_ch0.wav 约定一致）
        if sr_ref is None:
            sr_ref = sr
        assert sr == sr_ref, f"{stem} 采样率不一致 {sr} != {sr_ref}"
        n = int(k_sec * sr)
        piece = data[:n]
        assert len(piece) == n, f"{stem} 不足 {k_sec}s（{len(piece)/sr:.2f}s）"
        pieces.append(piece)

        offset = i * k_sec
        for line in rttm_path.read_text().splitlines():
            p = line.split()
            if len(p) < 8 or p[0] != "SPEAKER":
                continue
            st, dur = float(p[3]), float(p[4])
            ed = st + dur
            if st >= k_sec:
                continue
            st_c, ed_c = max(st, 0.0), min(ed, k_sec)
            spk = f"{stem}_{p[7]}"
            gt_lines.append(
                f"SPEAKER splice6 1 {st_c + offset:.4f} {ed_c - st_c:.4f} <NA> <NA> {spk} <NA> <NA>"
            )
        print(f"[{i+1}/{len(stems)}] {stem}: {len(piece)/sr:.1f}s 加入，偏移 {offset:.0f}s")

    merged = np.concatenate(pieces)
    sf.write(out_wav, merged, sr_ref, subtype="PCM_16")
    Path(out_rttm).write_text("\n".join(gt_lines) + "\n")
    print(f"written {out_wav}: {len(merged)/sr_ref:.2f}s | {out_rttm}: {len(gt_lines)} 行")
    print("选用会议:", ", ".join(stems))


if __name__ == "__main__":
    main()
