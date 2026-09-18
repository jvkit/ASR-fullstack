# Copyright 2026 scd_bench 擂台赛. 冷知识核查：aishell4 的 L 会与 M 会同编号说话人是否同一人。
# 用生产同款声纹 iic/speech_eres2netv2_sv_zh-cn_16k-common 算三对余弦：
#   ① M_001-M vs L_001-M（同编号跨会）② M_005-F vs L_005-F（同编号跨会）
#   ③ M_001-M vs M_004-F（不同人对照组，应显著低）
# 判定线：>0.60 大概率同人，<0.50 大概率不同人，中间如实报。
# 运行（GPU）：~/jvkit/asr/venv/bin/python ai-script/check_cross_meeting_spk.py
import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from funasr import AutoModel

EVAL = Path.home() / "jvkit/asr/eval/aishell4"
PAIRS = [
    ("M_001-M", "M_R003S01C01", "001-M", "L_001-M", "L_R004S01C01", "001-M"),
    ("M_005-F", "M_R003S01C01", "005-F", "L_005-F", "L_R004S01C01", "005-F"),
    ("M_001-M", "M_R003S01C01", "001-M", "M_004-F", "M_R003S01C01", "004-F"),
]
SEG_LO, SEG_HI = 5.0, 8.0


def pick_seg(stem, spk):
    """从该会 rttm 挑一条 [5,8]s 的段，返回 (wav_path, st, ed)。"""
    for line in (EVAL / f"{stem}.rttm").read_text().splitlines():
        p = line.split()
        if p[0] == "SPEAKER" and p[7] == spk:
            st, ed = float(p[3]), float(p[3]) + float(p[4])
            if SEG_LO <= ed - st <= SEG_HI:
                return EVAL / f"{stem}_ch0.wav", st, ed
    raise SystemExit(f"{stem}/{spk} 找不到 {SEG_LO}~{SEG_HI}s 的段")


def main():
    model = AutoModel(
        model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
        device="cuda",
        disable_update=True,
    )

    cache = {}
    with tempfile.TemporaryDirectory() as td:
        for name, stem, spk in [(a, b, c) for a, b, c, *_ in
                                [(x[0], x[1], x[2]) for x in PAIRS]] + \
                               [(x[3], x[4], x[5]) for x in PAIRS]:
            if name in cache:
                continue
            wav, st, ed = pick_seg(stem, spk)
            data, sr = sf.read(wav, dtype="float32", start=int(st * 16000),
                               stop=int(ed * 16000))
            f = Path(td) / f"{name}.wav"
            sf.write(f, data, sr)
            emb = model.generate(input=str(f))[0]["spk_embedding"]
            cache[name] = np.array(emb).flatten()
            print(f"{name}: {stem}/{spk} [{st:.2f}-{ed:.2f}] "
                  f"emb_norm={np.linalg.norm(cache[name]):.3f}")

    print("\n=== 余弦相似度 ===")
    for a, _, _, b, _, _ in PAIRS:
        c = float(np.dot(cache[a], cache[b]) /
                  (np.linalg.norm(cache[a]) * np.linalg.norm(cache[b])))
        verdict = ("同人(>0.60)" if c > 0.60 else
                   "不同人(<0.50)" if c < 0.50 else "中间地带(0.50~0.60)")
        print(f"{a:>8} vs {b:<8}: cos = {c:.4f}  -> {verdict}")


if __name__ == "__main__":
    main()
