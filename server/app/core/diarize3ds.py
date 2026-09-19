"""3D-Speaker 分人 wrapper（说话人分离换芯，接入一期 A 阶段，2026-09-19）。

来源：modelscope/3D-Speaker@main（server/third_party/3D-Speaker，fetch_3dspeaker.sh 拉取；
与 scd_bench 试验田同源同版本，等价性已验收：rotate_m30_s5 DER 7.66% / M5min 4.77%）。
管线 = FSMN VAD → 1.5s/0.75s 子段 → CAM++(iic/speech_campplus_sv_zh_en_16k-common_advanced)
嵌入 → 谱聚类（段数<40 降级 AHC）。

输入契约：16kHz 单声道 int16 PCM bytes（生产同 preprocess 上游格式；降噪请在调用前经
app.core.preprocess.denoise_pcm 处理——本 wrapper 不做降噪，保持职责单一）。
输出契约：[{start, end, cluster_id}]（秒，float/int；cluster_id 为每场从 0 起的匿名标签，
不含注册认人——认人仍走声纹库比对）。

已知限制（沿袭 3D-Speaker）：
- 子段数 <40 走 AHC 短段分支（fix_cos_thr=0.4），谱聚类参数不生效，--speaker_num 类
  oracle 人数在该分支被静默忽略 → 短音频（<~1min 稀疏语音）人数估计不可靠
- max_num_spks=15（谱聚类选 k 上限）；>15 人场景不可用
- 无滑窗，整段输入整段聚类；长会议建议按场调用（会末终版场景）
- CPU 推理，38min 会议约 2.5min（RTF≈0.06）

TODO(B 阶段)：聚类参数（cluster_type/mer_cos/min_cluster_size/max_num_spks/pval 等）与
MODELSCOPE_CACHE 现走本模块常量/环境默认，接入 app.config.py 统一管理；reannotate 与
GPU diarize 端点接线同属 B 阶段（待救援 AI 完工后动工）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[2]  # server/
_THIRDPARTY = _SERVER_ROOT / "third_party" / "3D-Speaker"
if str(_THIRDPARTY) not in sys.path:
    sys.path.insert(0, str(_THIRDPARTY))

# 模型缓存重定向（server/models/cache），不覆盖外部既有设定
os.environ.setdefault("MODELSCOPE_CACHE", str(_SERVER_ROOT / "models" / "cache"))

# TODO(B 阶段): 接入 app.config.py。现沿用 3D-Speaker 官方默认（与 scd_bench 评测一致）。
CLUSTER_PARAMS = {
    "cluster_type": "spectral",
    "mer_cos": 0.8,          # 簇心 cos≥0.8 再合并
    "min_num_spks": 1,
    "max_num_spks": 15,      # 选 k 上限
    "min_cluster_size": 4,   # ≤4 段的簇重指派到最近大簇
    "oracle_num": None,
    "pval": 0.012,
}
DEVICE = "cpu"

_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        os.makedirs(os.environ["MODELSCOPE_CACHE"], exist_ok=True)
        from speakerlab.bin.infer_diarization import Diarization3Dspeaker
        _pipeline = Diarization3Dspeaker(device=DEVICE)
    return _pipeline


def diarize(pcm: bytes, sr: int = 16000) -> list[dict]:
    """int16/16k/mono PCM → 分人段列表 [{start, end, cluster_id}]（秒）。"""
    if sr != 16000:
        raise ValueError(f"仅支持 16kHz，收到 {sr}")
    import numpy as np
    import torch

    wav = torch.from_numpy(np.frombuffer(pcm, dtype=np.int16))[None, :]  # [1,T] int16
    labels = _get_pipeline()(wav, wav_fs=sr)
    return [
        {"start": float(st), "end": float(ed), "cluster_id": int(cid)}
        for st, ed, cid in labels
    ]
