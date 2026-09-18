"""从 AISHELL-4 会议音频切前 N 秒小样（soundfile，16k 单声道直切）。

用法: python scripts/cut_wav.py <in.wav> <out.wav> <seconds>
"""
import sys

import soundfile as sf


def main():
    in_wav, out_wav, seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
    data, sr = sf.read(in_wav, dtype="float32")
    n = int(seconds * sr)
    assert data.ndim == 1, "期望单声道"
    sf.write(out_wav, data[:n], sr, subtype="PCM_16")
    info = sf.info(out_wav)
    print(f"written {out_wav}: {info.duration:.3f}s, {info.samplerate}Hz, {info.channels}ch")


if __name__ == "__main__":
    main()
