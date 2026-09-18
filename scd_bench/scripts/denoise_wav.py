"""实验B：用项目集成 GTCRN（server/app/core/preprocess.denoise_pcm）给 wav 降噪。

只"运行"项目代码，不修改；用项目 .venv（torch）。
用法: python scripts/denoise_wav.py <in.wav> <out.wav>   （16k 单声道 16bit PCM）
"""
import sys
import wave
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

from app.core.preprocess import denoise_pcm  # noqa: E402  （运行期导入，不改源码）


def main():
    in_wav, out_wav = sys.argv[1], sys.argv[2]
    with wave.open(in_wav, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 16000
        pcm = w.readframes(w.getnframes())
        sr = w.getframerate()
    out = denoise_pcm(pcm)
    assert len(out) == len(pcm), "长度漂移"
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(out)
    print(f"denoised {in_wav} -> {out_wav} ({len(pcm)/2/sr:.1f}s, {len(out)} bytes)")


if __name__ == "__main__":
    main()
