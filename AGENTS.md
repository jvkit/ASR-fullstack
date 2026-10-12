# AGENTS.md — 会议纪要系统（开发指南）

> 给在这个仓库工作的 AI/人。**动手前必读：本文件 + `KNOWLEDGE.md`（经验地图）+ `EXPERIENCE.md`（踩坑速查）。**
> 开发机是 **gpu2**（192.168.30.105，bo2），项目 `~/workspace/asr/{service,fullstack}`。

## 0. 你是谁、你在哪

- 你在 gpu2 上开发。**gpu（172.16.16.120）和本地电脑是生产环境，没有用户明确指令不要动。**
- 两个仓库：
  - `~/workspace/asr/fullstack`（= GitHub `jvkit/ASR-fullstack`）：FastAPI 后端 + Vue3 前端 + 双版本管道编排
  - `~/workspace/asr/service`（= GitHub `jvkit/ASR-service`）：GPU diarize_svc（降噪/聚类/声纹/ASR）

## 1. 黄金命令

```bash
# === 环境（每个新 shell）===
V=~/workspace/asr/venv/bin          # python venv
export PATH=~/workspace/asr/tools/node-v20.18.0-linux-x64/bin:$PATH   # node/npm

# === 启动（详见 ~/workspace/asr/DEPLOY.md）===
# diarize_svc（注意三件套环境变量，漏了声纹库为空！）
export ASR_ROOT=~/workspace/asr/service
export SPEAKER_ENROLL_DIR=~/workspace/asr/service/speaker_enrolls
export THIRD_PARTY_3DSPEAKER=~/workspace/asr/service/third_party_3dspeaker
cd ~/workspace/asr/service && $V/python -m uvicorn diarize_svc.service:app --host 0.0.0.0 --port 10096 --app-dir ~/workspace/asr/service &

# 后端
cd ~/workspace/asr/fullstack/server && $V/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --app-dir . &

# 前端（已 build 过；改完前端要重新 npm run build）
cd ~/workspace/asr/fullstack/web && npx vite preview --port 5174 --host &

# === 健康检查 ===
curl -s localhost:10096/health            # diarize_svc
curl -s -o /dev/null -w '%{http_code}' localhost:8010/api/meetings
curl -sk -o /dev/null -w '%{http_code}' https://localhost:5174

# === 端到端冒烟（改完认人逻辑必跑）===
F=~/workspace/asr/service/speaker_enrolls/冯忠俊_1791613490_0.pcm
curl -s -X POST localhost:10096/speaker_identify -H 'Content-Type: application/octet-stream' --data-binary @$F
# 期望 {"ok":true,...,"best":"冯忠俊","best_sim":0.9x}

# === GPU 服务重启（改 service.py 后）===
pkill -f diarize_svc.service
# 再按上面启动命令拉起（模型已缓存，~60s 健康）

# === 会末重跑验证（改聚类/认人后）===
curl -s -X POST localhost:8010/api/meetings/<meeting_id>/reannotate
# 然后 tail -f fullstack/server/backend.log 看 reannotate/final pass 日志
```

## 2. 改代码的安全流程

1. **改前**：`KNOWLEDGE.md` 第 1 节查有没有相关经验文档；`eval/tuning_log.md` 查有没有做过类似实验
2. **改 GPU 服务（service.py）**：先 `cp service.py service.py.bak_$(date +%Y%m%d)`，改完 `$V/python -c "import ast; ast.parse(open('diarize_svc/service.py').read())"` 验语法，再重启
3. **追加代码到 service.py 用 Edit 工具**，不要 `cat >>`（踩过重复追加的坑）
4. **改后验证**：冒烟测试（第 1 节）+ 用真实会议重跑会末版看日志
5. **不要动**：`speaker_enrolls/` 里的 pcm（声纹库是生产数据）；除非你就是在做声纹注册功能且用户知情
6. **用户正在开会时不要重启服务**（用户可能随时开会测试，重启前确认）

## 3. 配置在哪

| 配置 | 位置 |
|---|---|
| 后端全部配置 | `fullstack/server/app/config.py`（.env 覆盖，键名=字段名大写） |
| gpu2 的 .env | `fullstack/.env`（LLM=newapi glm-5.3-flash、SQLite、diarize=本机） |
| 认人阈值 | `config.py: reannotate_name_threshold = 0.50` |
| 实时版 margin 拒识 | `fullstack/server/app/core/session.py`（0.15） |
| 聚类参数 | `service/diarize_svc/service.py: _diar3ds()`（mer_cos=0.8, min_cluster_size=4 等） |
| 热词 | `fullstack/server/hotwords.txt` |

## 4. 红线（用户拍板过的共识）

1. **实时版与会末版互不纠正**（见 KNOWLEDGE.md 第 2 节）
2. **不走自研聚类**：3D-Speaker 是定档方案，别再引入 diart 之类（评测报告在 docs/）
3. **不拍脑袋调阈值**：改阈值要有实验数据，记进 `eval/tuning_log.md`
4. **无声纹统一"未识别"**，不区分"陌生人1/2"（实时版）
5. **手机/劣质音频不进声纹库**（只作 ASR）
6. 冯忠俊↔蒋泽鹏是近邻对（cos 0.85），见到这两人互标不要惊讶，先查段质量
7. ere 嵌入路径有 bug，**生产用 cam**

## 5. 排障速查

| 症状 | 第一步 |
|---|---|
| 会末版没结果 | backend.log 找 `reannotate ... failed`；常见=diarize_svc 500（看 service 日志） |
| diarize_svc 日志 `No module named pyannote` | pyannote 全家桶没装（见 KNOWLEDGE 5.3） |
| 声纹全"未识别" | 检查 SPEAKER_ENROLL_DIR 是否生效；`curl /speaker_names` |
| 大量低分 reject | 正常的 margin 拒识，先看分数分布再说话 |
| LLM 失败 | 从 gpu2 `curl 172.16.16.110:3002/v1/chat/completions` 直连测；glm-5.3-flash 是 thinking 模型，max_tokens 要给够 |
| 服务莫名其妙 down | `pkill -f diarize_svc` 后重启；gpu 机同款毛病，load 高会自杀 |

## 6. 经验维护义务

- 解决新坑后：把教训写进 `EXPERIENCE.md` 或对应模块的 `docs/experience/*.md`（四条质量门：实际探索才发现的/未来有用的/有触发条件的/已验证的），别只留在对话里
- 调参实验：记 `eval/tuning_log.md`（日期、改什么、数据、结论）
- 改了本文涉及的任何约定（架构/红线/启动方式）：**同步更新本文和 KNOWLEDGE.md**
- 文档与代码不一致时，以代码为准并改文档

## 7. 与用户协作

- 方案分叉时摆选项+证据，等拍板，不要替他决定（auto 模式下选最保守的：只读诊断+报告）
- 大改动前先 git commit 当前状态（本地仓库，不 push）
- 汇报带数据（分数/段数/日志行），不带"我觉得"
