# scd_bench — 说话人分离擂台赛：3D-Speaker 用法研究（阶段1 试验田）

## 目的

评估 3D-Speaker（阿里通义开源，ModelScope 分发）能否替代 meeting-asr 现有自研分离
管线（VAD 切段 + 声纹 embedding 比对 + 聚类，AISHELL-4 DER ~12.3%）。

擂台赛分两阶段：

- **阶段1（本目录，已完成）**：用法研究——装环境、复现官方示例、前 60s 小样本人工核对。
- **阶段2（未开始）**：正式评测。等后续指令。结论与参数摸底见
  [NOTES-用法研究.md](./NOTES-用法研究.md)。

## 纪律

- 试验田自包含：所有产物（venv、模型缓存、输出、第三方源码）只落在本目录内，
  唯一例外是 `meeting-asr/eval/tuning_log.md` 的试验日志。
- 第三方源码（3D-Speaker/）不入库；只有手写 wrapper 脚本（scripts/）入库。
- 成功 → 留用并入库；失败 → 整个目录删除不留痕。
- 不碰 meeting-asr 现有任何代码（server/、audio_denoise/、web/ 等）。
- 不跑正式评测。

## 目录结构

```
scd_bench/
├── README.md           # 本文件
├── .gitignore
├── NOTES-用法研究.md    # 小样本核对结论 + 关键参数摸底 + 阶段2建议
├── 3D-Speaker/         # 第三方源码（gitignore，不入库）
├── scripts/            # 手写 wrapper（入库）：cut_wav.py / compare_timeline.py
├── out/                # 运行产物（gitignore）：切分音频、rttm/json 输出、对照表
└── models/
    └── cache/          # ModelScope 模型缓存（gitignore，重定向自 ~/.cache）
```

## 环境

- Python 3.10.12（`uv venv --python /usr/bin/python3 .venv`；注意 uv 默认会选 3.12，
  而 3D-Speaker 的 requirements 钉了 numpy<1.24 / sklearn==1.0.2，只能用 3.10）
- torch / torchaudio 必须为 **CPU 版**
- 一切运行命令前必须设置（模型缓存重定向，禁止落 ~/.cache）：
  `export MODELSCOPE_CACHE=/home/jvkit/workspace/meeting/meeting-asr/scd_bench/models/cache`

## 安装命令（已验证可复现）

```bash
cd scd_bench
uv venv --python /usr/bin/python3 .venv

# 1) torch/torchaudio CPU（必须 CPU 索引；注意要装 2.6.x，见下方踩坑#3）
uv pip install --python .venv/bin/python torch==2.6.0 torchaudio==2.6.0 \
    --index-url https://download.pytorch.org/whl/cpu

# 2) 3D-Speaker 官方 requirements（numpy<1.24、sklearn==1.0.2 只在 py3.10 可装）
uv pip install --python .venv/bin/python -r 3D-Speaker/requirements.txt

# 3) 官方没声明但必须装的依赖（踩坑#1/#2/#4）
uv pip install --python .venv/bin/python pyannote.audio fastcluster umap-learn hdbscan
uv pip install --python .venv/bin/python "modelscope[audio]"   # VAD pipeline 需要 funasr
uv pip install --python .venv/bin/python addict simplejson datasets  # modelscope 运行期缺包

# 4) modelscope[audio] 会把 torch 换成 CUDA 版、numpy 换成 2.x，必须再修回 CPU 版（踩坑#5）
uv pip install --python .venv/bin/python --reinstall --no-deps torch==2.6.0 torchaudio==2.6.0 \
    --index-url https://download.pytorch.org/whl/cpu
```

## 实际装成版本（2026-09-18）

关键包：torch 2.6.0+cpu、torchaudio 2.6.0+cpu、modelscope 1.24.1（被 modelscope[audio]
从 1.40.1 降下）、funasr 1.4.15、pyannote-audio 4.0.7、numpy 2.2.6（官方要求 <1.24，
被 pyannote 4.x 打破——以"示例能跑通"为准）、scikit-learn 1.7.2（官方钉 1.0.2，同上）、
scipy 1.15.3、soundfile 0.14.0、fastcluster 1.3.0、umap-learn 0.5.12、hdbscan 0.8.44、
addict 2.4.0、simplejson、datasets、kaldiio 2.18.1。

## 官方示例复现命令（已跑通，EXIT=0）

```bash
export MODELSCOPE_CACHE=$PWD/models/cache
# 前 60s 小样本（切分用 scripts/cut_wav.py）
.venv/bin/python 3D-Speaker/speakerlab/bin/infer_diarization.py \
    --wav out/M_R003S01C01_ch0_head60s.wav --out_dir out/diar_head60s
# → out/diar_head60s/M_R003S01C01_ch0_head60s.rttm（8 段 3 标签）

# 其他探测（非评测）：json 输出 / oracle 说话人数 / 全曲计时
... --out_type json
... --speaker_num 6
... --wav ../eval/aishell4/M_R003S01C01_ch0.wav --out_dir out/diar_full   # 38min RTF≈0.065
```

首次运行自动从 ModelScope 下载模型（CAM++ 28M + FSMN VAD 1.7M，已缓存）。

## 主要踩坑（详见 NOTES 与 tuning_log.md）

1. `infer_diarization.py` 模块级 `import pyannote.audio`——requirements.txt 未声明。
2. `speakerlab/process/cluster.py` 模块级 import `fastcluster/umap/hdbscan`——未声明。
3. torchaudio≥2.11 硬依赖 torchcodec（要系统 ffmpeg7+CUDA 库，CPU 环境无解）→ 退 torch 2.6。
4. modelscope VAD pipeline 需要 `modelscope[audio]`（funasr）+ 运行期缺 addict/datasets。
5. `modelscope[audio]` 会把 torch 换成 CUDA 版 → 装完必须 --reinstall 回 CPU 版。

## 当前状态

- [x] 骨架
- [x] 环境
- [x] 官方示例复现
- [x] 小样本人工核对（前 60s）
- [x] 参数摸底
- [ ] 阶段2 正式评测（待指令）
