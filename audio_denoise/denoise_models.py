"""三家降噪模型的统一封装。输入任意采样率 wav 路径，输出 16k wav 路径。

- gtcrn: torch 推理（checkpoints/model_trained_on_dns3.tar，16kHz，按仓库 infer.py 配方）
- dfnet: DeepFilterNet3（模型已缓存 ~/.cache/DeepFilterNet）
- frcrn: 阿里 ClearerVoice FRCRN_SE_16K（modelscope 下载模型）
"""
import os
import sys
import numpy as np
import soundfile as sf
import torch

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "gtcrn_src"))

_resampler_cache = {}

def _to_mono_16k(wav_path, tmp_path):
    import torchaudio
    wav, sr = sf.read(wav_path, dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    t = torch.from_numpy(wav)
    if sr != 16000:
        key = (sr, 16000)
        if key not in _resampler_cache:
            _resampler_cache[key] = torchaudio.transforms.Resample(sr, 16000)
        t = _resampler_cache[key](t)
    sf.write(tmp_path, t.numpy(), 16000)
    return tmp_path

# ---------------- GTCRN (torch, 按仓库 infer.py) ----------------
_gtcrn_model = None

def _get_gtcrn():
    global _gtcrn_model
    if _gtcrn_model is None:
        from gtcrn import GTCRN
        model = GTCRN().eval()
        ckpt = torch.load(os.path.join(BASE, "gtcrn_src/checkpoints/model_trained_on_dns3.tar"),
                          map_location=torch.device("cpu"))
        model.load_state_dict(ckpt["model"])
        _gtcrn_model = model
    return _gtcrn_model

def denoise_gtcrn(wav_path, out_path, _tmp):
    model = _get_gtcrn()
    mix, fs = sf.read(_tmp, dtype="float32")
    assert fs == 16000
    win = torch.hann_window(512).pow(0.5)
    inp = torch.stft(torch.from_numpy(mix), 512, 256, 512, win, return_complex=False)
    with torch.no_grad():
        output = model(inp[None])[0]
    enh = torch.istft(torch.view_as_complex(output.contiguous()),
                      512, 256, 512, win, return_complex=False)
    sf.write(out_path, enh.detach().cpu().numpy(), 16000)

# ---------------- DeepFilterNet3 ----------------
_df = None

def _get_df():
    global _df
    if _df is None:
        from df import init_df
        _df = init_df()  # (model, df_state, suffix)
    return _df

def denoise_dfnet(wav_path, out_path, _tmp):
    from df.enhance import enhance as df_enhance
    import torchaudio
    model, df_state, _ = _get_df()
    audio, sr = torchaudio.load(_tmp)  # 16k mono
    enhanced = df_enhance(model, df_state, audio)
    sf.write(out_path, enhanced.squeeze(0).numpy(), 16000)

# ---------------- FRCRN (ClearerVoice) ----------------
_cv = None

def _get_cv():
    global _cv
    if _cv is None:
        from clearvoice import ClearVoice
        _cv = ClearVoice(task="speech_enhancement", model_names=["FRCRN_SE_16K"])
    return _cv

def denoise_frcrn(wav_path, out_path, _tmp):
    cv = _get_cv()
    results = cv(input_path=_tmp, online_write=False, output_path=os.path.dirname(out_path))
    if isinstance(results, np.ndarray):
        audio = results.squeeze(0)
    else:
        key = list(results.keys())[0]
        audio = results[key]["audio"]
    sf.write(out_path, audio, 16000)

DENOISERS = {"gtcrn": denoise_gtcrn, "dfnet": denoise_dfnet, "frcrn": denoise_frcrn}

def run_all(wav_path, out_dir, prefix):
    """统一入口：归一化到 16k 后跑三家，返回 {name: out_path}"""
    os.makedirs(out_dir, exist_ok=True)
    import shutil
    tmp = os.path.join(out_dir, prefix + "_orig16k.wav")
    _to_mono_16k(wav_path, tmp)
    outs = {"orig": os.path.join(out_dir, prefix + "_orig.wav")}
    shutil.copy(tmp, outs["orig"])
    for name, fn in DENOISERS.items():
        out = os.path.join(out_dir, f"{prefix}_{name}.wav")
        fn(wav_path, out, tmp)
        outs[name] = out
    return outs

if __name__ == "__main__":
    wav = os.path.join(BASE, "samples/_prewarm.wav")
    if not os.path.exists(wav):
        sf.write(wav, (np.random.rand(16000) * 0.01).astype(np.float32), 16000)
    outs = run_all(wav, os.path.join(BASE, "samples"), "_prewarm")
    for k, v in outs.items():
        print(k, "OK" if os.path.exists(v) else "FAIL", v)
