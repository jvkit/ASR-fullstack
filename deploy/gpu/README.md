# GPU 服务器部署（运算侧）

本目录收编了 GPU 服务器上的全部自定义代码（2026-09-30 起唯一权威版本在本仓库，
服务器只是部署目标，禁止在服务器上直接改代码不回收）。

## 组成

| 目录 | 作用 | 端口 |
|---|---|---|
| `diarize_svc/` | 核心服务：VAD 切句 / 3D-Speaker 分离 / 声纹注册·认人 / SeACo 转写 / GTCRN 降噪 | 10096 |
| `funasr/` | FunASR 官方 websocket 服务 + 我们的 patch | 10095 |

## 全新服务器部署

```bash
# 1. 装官方 FunASR 源码（modelscope 下载模型）
git clone https://github.com/modelscope/FunASR.git funasr-src   # 或 modelscope 镜像

# 2. 打我们的 patch（注册管理/分段时间戳等定制）
cd funasr-src && patch -p1 < deploy/gpu/funasr/funasr_wss_server.patch

# 3. 建 venv 装依赖
python3 -m venv venv
venv/bin/pip install -r deploy/gpu/diarize_svc/requirements.txt
venv/bin/pip install fastcluster   # 3D-Speaker 聚类需要

# 4. 放置文件
#   - deploy/gpu/diarize_svc/       → ~/jvkit/asr/diarize_svc/
#   - server/app/core/gtcrn_net.py  → diarize_svc/gtcrn_net.py（/denoise 用）
#   - server/models/gtcrn_dns3.tar  → ~/jvkit/asr/gtcrn_dns3.tar
#   - server/third_party/3D-Speaker → ~/jvkit/asr/third_party_3dspeaker/（/diarize_full 用）

# 5. 下载模型（modelscope 自动下载，或预置 ~/.cache/modelscope/models/）：
#    iic/speech_campplus_speaker-diarization_common（3D 分离）
#    iic/speech_eres2netv2_sv_zh-cn_16k-common（声纹认人）
#    iic/speech_campplus_sv_zh-cn_16k-common（注册嵌入）
#    damo/speech_fsmn_vad_zh-cn-16k-common-pytorch（VAD）
#    iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch（标点）
#    FunASR 所需 paraformer online/offline、itn 等见 start_server.sh

# 6. 启动（监督自启）
setsid nohup bash diarize_svc/run_supervised.sh > /dev/null 2>&1 &
setsid nohup bash deploy/gpu/funasr/start_server.sh > funasr_svc.log 2>&1 &
```

## 环境变量（service.py 零硬编码路径，2026-09-30 起）

| 变量 | 默认 | 含义 |
|---|---|---|
| `MS_MODELS_DIR` | `~/.cache/modelscope/models` | 模型缓存根 |
| `ASR_ROOT` | `~/jvkit/asr` | 部署根目录 |
| `SPEAKER_ENROLL_DIR` | `$ASR_ROOT/funasr-src/runtime/python/websocket/speaker_enrolls` | 声纹库 |
| `SPEAKER_DB` | `$ASR_ROOT/funasr-src/runtime/python/websocket/speaker_db.json` | 声纹元数据 |
| `GTCRN_CKPT` | `$ASR_ROOT/gtcrn_dns3.tar` | 降噪权重 |
| `THIRD_PARTY_3DSPEAKER` | `$ASR_ROOT/third_party_3dspeaker` | 3D-Speaker 源码 |

默认值即当前生产布局，目录结构一致时零配置可跑。

## 当前服务器路径约定（172.16.16.120，ssh gpu）

- 代码根：`~/jvkit/asr/`，venv：`~/jvkit/asr/venv`
- 声纹库（运行时数据，不在仓库）：`~/jvkit/asr/funasr-src/runtime/python/websocket/speaker_enrolls/`
- 日志：`~/jvkit/asr/diarize_svc.log`、`diarize_svc_respawn.log`

## 端点速查

- `POST /meeting_chunk` 一体化批次（旧会中路径，已弃用保留回滚）
- `POST /vad` VAD 切句；`POST /embed` ERes2NetV2 embedding
- `POST /diarize_full` **3D-Speaker 整场分离**（2026-09-30 新增，收编本地 diarize3ds）
- `POST /identify_segments` 外部切段认人+转写（会末与会中批次的认人搭档）
- `POST /denoise` **GTCRN 降噪**（2026-09-30 新增，收编本地 preprocess）
- `GET/POST/DELETE /speaker_*` 声纹库管理（注册/识别/段试听/删除）
- `GET /health`
