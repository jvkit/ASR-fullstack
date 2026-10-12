# 会议纪要系统 · 广泛知识（KNOWLEDGE）

> 本文是"经验地图"：把散落在仓库各处的经验文档串成一张网，并补上文档里没有的口头共识。
> **新会话/新 AI 进场：先读本文 → 按"经验文档索引"按需深入 → 再动手。**
> 最后更新：2026-10-12（gpu2 部署日）

---

## 0. 一句话系统画像

浏览器录音 → FastAPI 后端 → **GPU 服务器 diarize_svc**（降噪/聚类/声纹/ASR 全部重运算）→ LLM 出文字纠错与会纪要 → 落库。
双版本管道：**实时版**（30s 批次滚动出文）与**会末版**（整场重跑）**互相独立、互不纠正**。

---

## 1. 经验文档索引（按需阅读，勿全量加载）

### 1.1 架构与流程（读代码前先看）

| 文档 | 内容 | 什么时候读 |
|---|---|---|
| `docs/achit/系统架构全景图-20260930.md` | 五张图：服务拓扑/IO流程/目录结构/会议时序/数据库表 | **任何开发前必读**，架构问题先查它 |
| `docs/achit/服务器架构与服务现状-20260920.md` | GPU 服务器服务清单 | 服务起不来、端口占用 |
| `docs/achit/数据流程图对照讲解-20261002.md` | 数据流对照代码 | 追踪一条音频的完整旅程 |
| `docs/achit/线上会议采集通道-探测与交接-20260922.md` | 线上会议（录屏+系统声+开麦门控）探测交接 | 改线上会议通道 |
| `docs/achit/视频侧接口与代码地图-20260922.md` | 视频/OCR 代码地图 | 改 OCR/视频 |
| `EXPERIENCE.md` | 踩坑速查（本仓库根目录） | 每次动手前扫一眼 |
| `DEPLOY.md`（gpu2 上有） | gpu2 启动命令 | 重启服务 |

### 1.2 算法原理（新手友好，从零讲透）

| 文档 | 内容 |
|---|---|
| `learn/001-声音与VAD.md` | 响度归一化是什么、VAD 切句、250ms 参数的含义 |
| `learn/002-认人与声纹阈值.md` | 声纹 embedding、余弦相似度、阈值 0.30 怎么定的 |
| `learn/003-换人边界与尾字问题.md` | 换人检测边界、切句尾字被吃掉的问题 |
| `learn/004-声音如何变成数字.md` | PCM/采样率/分贝直觉 |

### 1.3 调参与评测

| 文档 | 内容 |
|---|---|
| `eval/tuning_log.md` | **调参实验流水账**：每次改参数的结论和证据，调参前必读防重复踩坑 |
| `scd_bench/README.md` + `NOTES-用法研究.md` | 说话人分离 benchmark 工具用法 |
| `docs/声纹录入质量设计.md` | 声纹注册质量标准 |
| `docs/FunASR说话人分离调研与调优.md` | FunASR 分离调研原始记录 |

### 1.4 历史决策与讨论（知道"为什么现在这样"）

| 文档 | 内容 |
|---|---|
| `docs/discussion-20261008.md` | 10-08 讨论清单：声纹清洗/重跑入口/纪要对照/视频展示 |
| `docs/说话人分离优化方案-v2.md` | 聚类方案选型（3D-Speaker 定档的理由） |
| `docs/优化方向讨论总结-20260914.md` | 早期优化方向 |
| `docs/实时说话人分离方案评测汇报-20260923.md` | diart/3D-Speaker 等方案评测（为什么不用 diart） |
| `docs/技术概览.md` | 面向汇报的技术概览 |
| `docs/ops/mysql-migration-20260929.md` | SQLite→MySQL 迁移记录 |

### 1.5 经验库（AI 维护）

- `docs/experience/INDEX.md`：模块化经验库的索引和 CLI 维护方法
- 用户级通用经验：`~/.ai-memory/`（drawio、lark 等工具经验）

---

## 2. 双版本管道（最重要的架构共识）

```
实时版（live）                          会末版（final）
─────────────                          ─────────────
浏览器 30-45s 音频块                    整场录音（会后触发）
  → 降噪(/denoise)                      → 降噪(/denoise)
  → 本地 3D-Speaker 聚类                → GPU /diarize_full 整场聚类
  → GPU /identify_segments 认人+ASR     → GPU /identify_segments 认人+ASR
  → 每4批一"波"并行 LLM 纠错+段纪要     → 全文分波并行 LLM 纠错+段纪要
  → 实时展示                            → 会末总结（final）
```

**铁律：两版互不纠正、互不影响。** 不要设计"用会末纠正实时"或反向逻辑，用户明确否过。
会末版的存在意义：**聚类无敌**（整场聚类比 30s 块准得多）+ 陌生人注册入口（unknown_clusters 弹窗勾选注册）。
两版同时开时前端有开关切换展示；都失败时互不为对方兜底。

---

## 3. 认人链路的关键机制（GPU diarize_svc）

### 3.1 注册声纹 → 库

- 文件：`speaker_enrolls/{name}_{ts}_{i}.pcm`（16k int16 raw）
- **当前生产逻辑**：每段提 ERes2NetV2 embedding → **算术平均成单向量**（`_load_db_multi`）
- 增量缓存：按"每人文件名+mtime 签名"对比，只有变了的人才重提（全库重建会卡死服务，血泪教训）

### 3.2 认人（`/identify_segments`）

1. 每段（VAD 切的 2-10s）提 embedding
2. **时长加权平均 → 簇质心**
3. 簇质心 vs 库 argmax（≥ name_threshold 才贴名，否则"说话人N"）

**已知缺陷（2026-10-12 实锤）**：簇级 argmax 在混合簇上系统性出错——cluster 3 里 93% 是李炳晨，时长加权后却被标成刘恒（9d26278 灾难）。**修法已拍板未实现：簇内段投票**（每段独立 argmax，多数决定簇名）。见 EXPERIENCE.md。

### 3.3 分数口径（重要）

- 同人自相关：通常 0.44~0.97（注册质量好时 0.9+）
- 陌生人压线：0.50~0.65，margin（top1-top2）常 <0.15
- 阈值配置：`reannotate_name_threshold = 0.50`（config.py）；实时版另有 margin 拒识（session.py，0.15）
- **冯忠俊↔蒋泽鹏 cos 0.85 是著名近邻对**，CAM++/ERE 都分不清，argmax 经常互踩

### 3.4 声纹库规模（行业结论 2026-10-12 调研）

- 开集辨认（要拒识陌生人）存在公认"虚警问题"：库越大陌生人分数越右移（VoxWatch: AWS, arXiv 2307.00169）
- **几十人（≤30-50）舒适；百人级要上 score calibration；几百人需专用引擎**
- 当前 13 人，离上限很远。**误识的根源是段质量和阈值，不是人数**

### 3.5 跨设备声纹（线上/线下麦克风不一致）

- **根因**：单质心算术平均把两个信道簇揉成"中间空白向量"，自相关大降
- **修法已验证未上线**：multi-vector（每人保留逐段向量，匹配取 max）
  - 实测：刘恒 min 0.768→0.915，蒋泽鹏 0.667→0.734（`/enroll_debug` 端点对照）
- 段投票和 multi-vector 是两个独立改进，都做才完整

---

## 4. 音频质量经验

- **手机 mic 录音**：响度忽大忽小（1s 窗 rms std 16dB，是稳态的 2 倍）+ 偶发 1s 全零洞（AGC/Chrome tab 挂起）。**手机音频只作 ASR，不进声纹**
- **降噪（GTCRN）**：全程统一走 `/denoise`（注册/会中/会末同一信号域）。不要怀疑降噪扭曲特征——实时会末都走它，问题在别处
- **注册质量**：30s 高质量即可，40s 后收益边际；宁缺毋滥（脏段比少段更伤）

---

## 5. 环境与部署（2026-10-12 现状）

### 5.1 三台机器

| 机器 | 角色 | 项目位置 |
|---|---|---|
| 本地（用户电脑，弱） | 生产全栈 + 浏览器入口 | `~/workspace/meeting/meeting-asr` |
| gpu（172.16.16.120） | **生产** diarize_svc | `~/jvkit/asr` |
| **gpu2（192.168.30.105）** | **开发机**（2080 Ti×2） | `~/workspace/asr/{service,fullstack}` |

### 5.2 gpu2 专属

- venv：`~/workspace/asr/venv`（torch 2.14.1+cu130 CUDA 可用）
- node：`~/workspace/asr/tools/node-v20.18.0-linux-x64/bin`（用前加 PATH）
- 模型缓存：从原 GPU 拷贝（7 个核心模型）
- 声纹库：从原 GPU 拷贝（13 人 70 段）
- 数据库：**SQLite**（无 docker/MySQL），`fullstack/.env` 里 `DATABASE_URL=sqlite+aiosqlite:///./meeting.db`
- LLM：**newapi** `http://172.16.16.110:3002/v1`，model `glm-5.3-flash`（已不走用户电脑网关）
- 启动：`DEPLOY.md`（gpu2:~/workspace/asr/）
- **环境变量三件套必带**：`ASR_ROOT` / `SPEAKER_ENROLL_DIR` / `THIRD_PARTY_3DSPEAKER`，否则声纹库为空

### 5.3 gpu2 装机时踩过的坑（重装时防再踩）

1. 系统无 ensurepip → `get-pip.py --user --break-system-packages` 再 `pip install --user virtualenv`
2. `torch==x.x.x+cpu` 在 pypi 没有 → 装 GPU 版
3. pyarrow 最新版要 numpy 2，funasr 要 numpy 1.26 → **pin pyarrow==25.0.1**
4. **pyannote 全家桶必装**（pyannote-audio 4.0.7 等 6 个）——`third_party_3dspeaker/infer_diarization.py` import 它，缺了 `/diarize_full` 500（2026-10-12 两场会议失败的根因）
5. modelscope 缓存里 configuration.json 可能硬编码 `/home/bo1` 路径 → sed 替换成本机用户

---

## 6. 调试入口（黑盒排查用）

| 入口 | 用途 |
|---|---|
| 后端日志 | 本地 `/tmp/meeting_backend.log`；gpu2 `fullstack/server/backend.log` |
| GPU 服务日志 | gpu `~/jvkit/asr/diarize_svc.log`；gpu2 `service/diarize_svc.log` |
| `GET /api/meetings/{id}` | `utterances`（实时版）/`utterances_re`（会末版）/`final_status` |
| GPU `/enroll_debug` | 声纹库自相关对照（单质心 vs multi-vector） |
| GPU `/segments_sim` | 段级相似度听感台 |
| `eval/7444af8235c2/bench/index.html` | 段级分数内嵌 HTML 听感台（file:// 直接开） |
| GPU 日志 `[identify] cluster N -> X (score; margin)` | 簇级认人决策追踪 |
| 段试听 | `GET /api/meetings/{id}/segments/{seq}/audio` |

**排查口诀**：先看后端日志定位管道哪步 → 再看 GPU 日志 `[identify]`/`[db_multi]` → 段级存疑就拉 `/segments_sim` 或听感台 → 声纹存疑跑 `/enroll_debug`。**不要拍脑袋改阈值**。

---

## 7. 待办与已知问题（截至 2026-10-12）

1. **簇级 argmax → 段投票**：已拍板未实现（治混合簇错标）
2. **multi-vector 上生产**：已验证未上线（治跨设备自相关崩）
3. **ere 嵌入路径有 bug**（37e3 分出 1 簇），已回退 cam，修好前别用
4. 重跑/重放合并入口：设计已对齐未实现（讨论清单 #4）
5. 纪要对照展示（转写与段纪要同屏对齐）：待做
6. 旧会议无中间数据（调试页黑盒）：MySQL 化之后新数据才全
7. gpu2 的 LLM（glm-5.3-flash）是 thinking 模型，输出可能含 reasoning——消费 `choices[0].message.content` 时注意

## 8. 工作方式共识（和用户对齐过）

- **先对齐再开做**：方案有分叉先摆选项讲证据，用户拍板再动手
- 不拍脑袋自研算法：改法要有文献/实测依据
- 用户电脑弱：torch/模型运算只在 GPU 机跑
- 不主动 git push/动生产（gpu/本地），除非用户明确要求
- 汇报对象是老师：结论要有数据/证据
