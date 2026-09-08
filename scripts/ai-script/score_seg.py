"""声纹打分快捷脚本：对任意 wav 的任意时间段算 ERes2NetV2 相似度。

用法（在 GPU 上，用 venv）：
  venv/bin/python ai-script/score_seg.py <wav> --seg 16.83,17.94 [--seg 45.99,50.04 ...]
  venv/bin/python ai-script/score_seg.py <wav> --sents sents.json   # [{"sents":[[s,e],...],"texts":[...]}]

输出：每段 best 名 + 到每个注册人的相似度 + margin（top1-top2）。
声纹库：funasr-src/runtime/python/websocket/speaker_enrolls/*.pcm（多段质心，与服务端一致）。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ENROLL_DIR = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/speaker_enrolls"
ERE_MODEL = "iic/speech_eres2netv2_sv_zh-cn_16k-common"
ERE_CKPT = "/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master/pretrained_eres2netv2.ckpt"


def load_refs(ere):
    by: dict[str, list[Path]] = {}
    for p in Path(ENROLL_DIR).iterdir():
        if not p.name.endswith(".pcm"):
            continue
        stem = p.stem
        name = stem.rsplit("_", 1)[0] if "_" in stem else stem
        by.setdefault(name, []).append(p)
    refs: dict[str, np.ndarray] = {}
    for name, paths in by.items():
        vecs = []
        for p in paths:
            pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
            if len(pcm) < 16000 * 1.5:
                continue
            e = ere.generate(input=pcm, fs=16000, extract_embedding=True)[0]["spk_embedding"]
            e = np.asarray(e, dtype=np.float32).ravel()
            vecs.append(e / (np.linalg.norm(e) + 1e-9))
        if vecs:
            c = np.mean(vecs, axis=0)
            refs[name] = c / (np.linalg.norm(c) + 1e-9)
    return refs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--seg", action="append", default=[], help="start,end 秒，可多次")
    ap.add_argument("--sents", help="JSON 文件 {\"sents\":[[s,e],...],\"texts\":[...]}")
    args = ap.parse_args()

    from funasr import AutoModel  # noqa: 延迟导入，--help 更快

    audio, sr = sf.read(args.wav)
    if audio.ndim > 1:
        audio = audio[:, 0]

    sents: list[list[float]] = []
    texts: list[str] = []
    for seg in args.seg:
        a, b = seg.split(",")
        sents.append([float(a), float(b)])
        texts.append("")
    if args.sents:
        d = json.load(open(args.sents))
        sents += [[float(a), float(b)] for a, b in d["sents"]]
        texts += d.get("texts", [""] * len(d["sents"]))

    ere = AutoModel(model=ERE_MODEL, init_param=ERE_CKPT, device="cpu",
                    disable_pbar=True, disable_log=True)
    refs = load_refs(ere)
    names = sorted(refs)
    print("refs:", names, flush=True)

    for i, (a, b) in enumerate(sents):
        clip = audio[int(max(0.0, a) * sr):int(b * sr)]
        if len(clip) < sr * 0.3:
            print(f"{i} {a:.2f}-{b:.2f} TINY {texts[i] if i < len(texts) else ''}", flush=True)
            continue
        e = ere.generate(input=clip, fs=sr, extract_embedding=True)[0]["spk_embedding"]
        e = np.asarray(e, dtype=np.float32).ravel()
        e /= np.linalg.norm(e) + 1e-9
        sims = {n: float(np.dot(e, refs[n])) for n in names}
        ranked = sorted(sims.items(), key=lambda kv: -kv[1])
        best, s1 = ranked[0]
        s2 = ranked[1][1] if len(ranked) > 1 else 0.0
        t = texts[i] if i < len(texts) else ""
        print(f"{i} {a:.2f}-{b:.2f} best={best} {s1:.3f} margin={s1 - s2:.3f} "
              f"sims={{{', '.join(f'{n}: {s:.3f}' for n, s in ranked)}}} {t}", flush=True)


if __name__ == "__main__":
    main()
