# meeting-asr 项目约定（AGENTS.md）

语音会议纪要系统：FunASR 实时转写 + ERes2NetV2 声纹区分说话人 + LLM 纠错/纪要。
本文件是**强制约定**，改动目录结构 / 调试流程 / 配置方式时必须同步更新。

## 1. 目录结构（红线：产物不许乱丢）

```
meeting-asr/
├── server/                 # FastAPI 后端（唯一后端代码位置）
│   ├── app/
│   │   ├── config.py       # ★ 所有可调参数的唯一数据源（.env 可覆盖）
│   │   ├── api/            # REST + WS 端点
│   │   ├── core/           # session / roller(滚动增强) / reannotate(会后重标注)
│   │   └── db/             # SQLAlchemy 模型
│   ├── scripts/
│   │   ├── eval_re.py      # ★ 说话人标注评测（用户 GT 打分，改完必跑）
│   │   └── ai-script/      # 诊断脚本（本地副本，GPU 为运行环境）
│       └── score_seg.py    # ★ 任意音频段对声纹库打分（margin 判模型极限/程序bug）
│   └── recordings/         # 会议录音 wav（系统产物，勿手删）
├── web/                    # Vue3 + Vant 前端
├── eval/                   # ★ 所有评测产物唯一存放处（分人音频/segs json/对照表）
│   └── <meeting_id>/       # 按会议分目录
└── docs/                   # 方案文档、调研记录
```

**禁止事项**（用户明确要求，犯过多次）：
- ❌ 不许往 GPU `~/jvkit/asr/` 根目录、`/tmp/` 丢测试文件——那是运行环境不是仓库
- ❌ 评测产物（分人 wav、segs json、诊断输出）散落各处
- ✅ 任何诊断/评测产物：本地放 `eval/<meeting_id>/`，GPU 上放 `~/jvkit/asr/eval/<meeting_id>/`
- ✅ 一次性诊断脚本：本地 `server/scripts/ai-script/` 入 git，GPU 只放运行副本

## 2. 服务清单（重启命令）

| 服务 | 位置 | 端口 | 启动 |
|---|---|---|---|
| FastAPI 后端 | 本地 | 8010 | `cd server && ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010` |
| Vue dev | 本地 | 5174(https) | `cd web && npx vite --host 0.0.0.0 --port 5174` |
| FunASR 转写 | GPU(bo1@gpu) | 10095 | `ssh gpu "cd ~/jvkit/asr && setsid nohup bash start_server.sh > /tmp/funasr.log 2>&1 &"` |
| diarize 服务 | GPU | 10096 | `ssh gpu "cd ~/jvkit/asr && setsid nohup venv/bin/python -m uvicorn diarize_svc.service:app --host 0.0.0.0 --port 10096 --app-dir ~/jvkit/asr > /tmp/diarize.log 2>&1 &"` |

GPU 改动**不入 git**（运行环境）；代码改动以本地仓库为准，部署用 scp 同步脚本。

## 3. 调试工作流（固定套路，不许跳过）

1. **用户报「某句标错」** → `score_seg.py` 拿该句时间段对声纹库打分：
   - `margin < 0.05` → 模型极限（音频质量/声纹域差），如实告知，不硬修
   - `margin 大但判错` → 程序 bug，查代码
2. **每次改完重标注逻辑** → 必跑 `python scripts/eval_re.py <meeting_id>` 对 GT 打分，**不许只测「重跑一致」不测准度**
3. **GT 维护**：用户人工标注录入 `eval_re.py` 的 `GROUND_TRUTH`（按 meeting_id 分键），这是唯一 GT 源
4. **确定性验证**：`reannotate` 连跑两次 diff，防回归

```bash
# 常用命令
ssh gpu "cd ~/jvkit/asr && venv/bin/python ai-script/score_seg.py <wav> --seg <start>,<end>"
cd server && ../.venv/bin/python scripts/eval_re.py <meeting_id>
```

## 4. 配置约定

- 所有默认值只在 `server/app/config.py`（`.env` 按 pydantic 优先级覆盖）
- 前端默认值**必须从 `GET /api/config` 拉取**，禁止前端硬编码阈值（曾因此出现 0.50/0.30 两套值并存的事故）
- GPU 端点默认值（如 `/diarize_sent` 的 name_threshold=0.30）必须与 config 一致；上层传参覆盖时注释说明原因

## 5. 技术决策记录（勿回退）

- **会后重标注 = 实时句子边界 + 逐句 ER argmax**（`/diarize_sent`），不用阿里 pipeline 切分
  - 原因：阿里谱聚类切分非确定（同音频 3 次 13/8/8 段）+ oracle 直切会把连续对话并成整段
- **声纹模型 = ERes2NetV2**（实测比 cam++ 准很多），多段注册质心
- 实时打标（roller → `/diarize_labeled`）是独立链路，改动时注意区分
- 已知的难啃问题：**句内换人**（VAD 切句粒度，待做滑窗二次切分）、**声纹库域匹配**（注册 vs 开会声学条件差）
