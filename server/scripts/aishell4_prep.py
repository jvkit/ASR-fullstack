"""AISHELL-4 测试声纹准备：按 rttm 标注切出每个说话人的干净语音，注册到 GPU 测试目录。

从指定会议提取每个说话人累计 N 秒的语音（取该人最长的段，避免碎段），
写成 {spk}.pcm 上传 GPU：~/jvkit/asr/eval/aishell4_enrolls/<meeting>/<spk>.pcm
（enroll_dir 以会议分目录，各场会议说话人独立，互不串）。

用法：
  python scripts/aishell4_prep.py S_R003S01C01 [S_R003S02C01 ...] [--sec 15]

注意：AISHELL-4 说话人编号（001-M 等）是全局的（同一人跨会议同号），
但不同会议的 001-M 未必同一人，故按会议隔离目录。
"""

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval" / "aishell4"
GPU_ENROLL_BASE = "/home/bo1/jvkit/asr/eval/aishell4_enrolls"


def load_rttm(path: Path) -> dict[str, list[tuple[float, float]]]:
    by_spk: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            by_spk[p[7]].append((float(p[3]), float(p[4])))
    return by_spk


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("meetings", nargs="+")
    ap.add_argument("--sec", type=float, default=15.0, help="每人提取秒数")
    args = ap.parse_args()

    for mid in args.meetings:
        wav = EVAL_DIR / "test" / "wav" / f"{mid}.flac"
        rttm = EVAL_DIR / "test" / "TextGrid" / f"{mid}.rttm"
        audio, sr = sf.read(wav)
        if audio.ndim > 1:
            audio = audio[:, 0]

        by_spk = load_rttm(rttm)
        remote_dir = f"{GPU_ENROLL_BASE}/{mid}"
        subprocess.run(["ssh", "gpu", f"mkdir -p {remote_dir}"], check=True)

        manifest = {}
        for spk, segs in sorted(by_spk.items()):
            # 按段长降序取，累计够 sec 秒
            segs.sort(key=lambda x: -x[1])
            clips, total = [], 0.0
            for st, dur in segs:
                if total >= args.sec:
                    break
                clip = audio[int(st * sr):int((st + dur) * sr)]
                if len(clip) < sr * 1.0:
                    continue
                clips.append(clip)
                total += dur
            if not clips:
                print(f"{mid}/{spk}: 无足够语音，跳过")
                continue
            pcm = np.concatenate(clips)
            pcm16 = (pcm * 32767).astype(np.int16)
            local = Path(f"/tmp/_enroll_{mid}_{spk}.pcm")
            local.write_bytes(pcm16.tobytes())
            subprocess.run(
                ["scp", "-q", str(local), f"gpu:{remote_dir}/{spk}.pcm"], check=True)
            local.unlink()
            manifest[spk] = round(len(pcm16) / 16000, 1)

        print(f"{mid}: 注册 {json.dumps(manifest, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
