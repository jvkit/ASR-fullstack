# meeting-asr / voice2sum

实时会议转写 + 滚动纪要系统。浏览器录音 → FunASR（说话人分离）→ 本地 LLM 滚动总结 → 落库。

## 架构

```
浏览器 Vue3 ──WS(16k PCM)──▶ FastAPI ──WS──▶ FunASR 2pass (gpu8, CPU 模式)
                                │─HTTP──▶ Qwen3.5 vLLM (内网已有) / DeepSeek (后续)
                                │─SDK──▶ Langfuse (本地 :3000，可选)
                                └───▶ SQLite (meeting.db)
```

## 快速开始

```bash
# 0. 配置（从模板复制，填入 Langfuse keys）
cp .env.example .env

# 1. 后端
cd server
uv venv ../.venv && source ../.venv/bin/activate   # 或 python3 -m venv
uv pip install -e ../  # 或 pip install fastapi uvicorn[standard] websockets openai langfuse sqlalchemy pydantic-settings aiosqlite
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. 前端
cd web
npm install
npm run dev   # http://localhost:5173

# 3. 打开浏览器，允许麦克风，点「开始记录」
```

## FunASR 服务端（gpu8）

```bash
# 安装（已完成记录）
python3 -m venv --without-pip venv && venv/bin/python get-pip.py
venv/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install funasr modelscope

# 下载模型
venv/bin/python download_models.py

# 启动 2pass 服务（端口 10095；--spk-model 开启说话人分离）
funasr-server --host 0.0.0.0 --port 10095 \
  --model iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch \
  --online-model iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online \
  --vad iic/speech_fsmn_vad_zh-cn-16k-common-pytorch \
  --punc iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch \
  --spk-model iic/speech_campplus_sv_zh-cn-16k-common
```

## 测试

```bash
# FunASR 连通性（gpu8 本地）
venv/bin/python scripts/test_asr_client.py test.wav ws://127.0.0.1:10095

# LLM 连通性
curl http://192.168.236.13:47501/v1/chat/completions -H "Authorization: Bearer $LLM_API_KEY" ...
```

## 目录

```
server/app/
├── api/       # WS 接入 + REST（OA 集成）
├── core/      # FunASR 客户端 / 句子缓冲 / 定时总结 / 会话编排
├── llm/       # OpenAI 客户端 + 提示词（Langfuse 自动埋点）
└── db/        # SQLite 模型与会话
web/src/
├── components/  # Recorder / LiveTranscript / SummaryPanel
└── ws/          # AudioWorklet 采集 + WS 客户端
```

## 二期方向

- 智能话题边界（VAD 静音 + LLM 判断，替代固定 1 分钟窗口）
- 多采集端（参会人各自设备 → 完美说话人分离）
- OA 集成（REST 已有：`/api/meetings/{id}/report` 导出 Markdown 纪要）
- FunASR 热词表（专业术语，`FUNASR_HOTWORDS`）
