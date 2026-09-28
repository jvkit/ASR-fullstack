"""集中音频预处理层：降噪（西工大 GTCRN，DNS3 权重）。

设计原则（2026-09-17 降噪决赛后集成）：
- 唯一入口 denoise_pcm(pcm:int16/16k/mono bytes) -> bytes，所有"送给 GPU 做推理"的
  音频（会中批次、会末重标注、声纹注册、声纹识别）都必须过这一层，保证信号一致。
- 原始 PCM 存档（server/recordings/）不受本层影响：降噪只作用于推理副本。
- 开关在 config.denoise_enabled；模型路径 config.denoise_model_path。
- GTCRN 对干净语音近透明（决赛验证），注册/识别同处理无阈值漂移之虞。
"""
import logging
import os
import threading

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_model = None
_window = None


def _load():
    global _model, _window
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        import torch
        from app.core.gtcrn_net import GTCRN

        path = settings.denoise_model_path
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), path)
        model = GTCRN().eval()
        ckpt = torch.load(path, map_location=torch.device("cpu"))
        model.load_state_dict(ckpt["model"])
        _window = torch.hann_window(512).pow(0.5)
        _model = model
        logger.info("GTCRN 降噪模型已加载: %s", path)
    return _model


def denoise_pcm(pcm: bytes) -> bytes:
    """int16/16kHz/mono PCM → 同格式降噪后 PCM。关闭或未加载成功时原样返回。"""
    if not settings.denoise_enabled:
        return pcm
    try:
        import torch

        if len(pcm) % 2:  # 奇数字节防御（WS 帧/flush 切边可能产生）：丢尾字节
            pcm = pcm[: len(pcm) - 1]
            if not pcm:
                return pcm
        model = _load()
        wav = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(wav) < 512:
            return pcm
        inp = torch.stft(torch.from_numpy(wav), 512, 256, 512, _window, return_complex=False)
        with torch.no_grad():
            output = model(inp[None])[0]
        enh = torch.istft(torch.view_as_complex(output.contiguous()),
                          512, 256, 512, _window, return_complex=False)
        arr = enh.detach().cpu().numpy()
        # STFT/ISTFT 长度对齐：补齐/截断到与输入逐样本一致（防时间轴漂移）
        if len(arr) < len(wav):
            arr = np.concatenate([arr, np.zeros(len(wav) - len(arr), dtype=np.float32)])
        elif len(arr) > len(wav):
            arr = arr[: len(wav)]
        out = np.clip(arr * 32768.0, -32768, 32767).astype(np.int16)
        return out.tobytes()
    except Exception:
        logger.exception("denoise_pcm failed, 回退原始音频")
        return pcm
