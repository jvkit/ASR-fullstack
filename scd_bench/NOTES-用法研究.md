# NOTES — 3D-Speaker 用法研究（阶段1）

日期：2026-09-18　试验田：`meeting-asr/scd_bench/`　负责范围：只用法研究，未跑正式评测。

## 1. 小样本核对（M_R003S01C01_ch0.wav 前 60s）

GT（rttm 窗口内 11 段、**6 人**）：001-M 为主持人长段发言（4.084s 起多段），
6.9–14.4s 间 002-M/003-F/004-F/005-F/006-F 五人各一句约 1.3–1.7s 的短轮次（自我介绍式）。

系统输出（默认参数，`out/diar_head60s/`）：**3 个标签、8 段**。逐条对照见
`out/compare_head60s.txt`（脚本 `scripts/compare_timeline.py` 生成）。三点核对结论：

1. **说话人个数：错。** 系统 3 人 vs GT 6 人。两个方向都错：
   - 5 个 1.3–1.7s 短轮次被**合并成 1 个簇**（sys spk2，6.715–14.215，段内按重叠
     占比 002-M 23%、004-F 19%、006-F 19%、003-F 18%、005-F 17%，五人混一段）；
   - 主持人 001-M 一人被**劈成 2 个簇**（sys 0/1 交替，如 15.7–23.2 标 1、23.2–33.7 标 0）。
2. **边界：部分对齐、部分系统性漂移。**
   - 首段 4.090–6.715 vs GT 4.084–6.655，几乎完美（Δ+0.01s）；
   - 001-M 长段起点系统性偏晚 +1.2~1.5s（36.360 vs 34.934、49.560 vs 48.105），
     且在 23.215 / 33.715 处出现无中生有的内部切换（同一 GT 段内 0/1 翻转）；
   - 48.08–49.56 有 1.5s 空隙被漏（GT 连续发言）。
3. **段内混入：严重。** 6.715–14.215 一段内含 5 个不同 GT 说话人；14.215–15.715
   段混入 006-F 尾巴 16%。

**初步归因**（待阶段2验证）：与 3D-Speaker 官方 Limitations 自述一致——短音频/短轮次
场景弱；1.5s 子段切分 + 短轮次 embedding 不可分 + AHC 短段分支行为所致。
60s 样本子段数 < 40，走的是 **AHC 分支**（见 §3），谱聚类参数完全不生效。

**--speaker_num 6（oracle 说话人数）探测**：输出仍 3 簇结构（001-M 劈成 0/1/5，
五人短轮次仍并成 spk2）。原因：段数 <40 时走 `cluster_for_short`（AHC），
代码里 `self.cluster_for_short(X)` **不透传 speaker_num**（cluster.py:192），
oracle 参数在短音频上被静默忽略。>40 段走谱聚类时才生效。

## 2. 输出格式

`--out_type rttm`（默认）：

```
SPEAKER M_R003S01C01_ch0_head60s 0 4.090 2.625 <NA> <NA> 1 <NA> <NA>
SPEAKER M_R003S01C01_ch0_head60s 0 6.715 7.500 <NA> <NA> 2 <NA> <NA>
...
```

`--out_type json`：`{wav_id}_{st}_{ed}: {start, stop, speaker}` 字典，键为段 id。
说话人 id 为从 0 开始的整数标签（匿名，无注册比对能力；不认具体是谁）。

## 3. 关键参数清单（默认分人推理，infer_diarization.py + cluster.py 源码）

CLI 参数：
| 参数 | 默认值 | 含义 |
|---|---|---|
| `--wav` | 必填 | wav 路径或 wav 列表文件（txt，每行一个路径） |
| `--out_dir` | 必填 | 输出目录（None 时输出到源 wav 旁） |
| `--out_type` | `rttm` | 输出格式 `rttm`/`json` |
| `--include_overlap` | 关 | 重叠检测（需 `--hf_access_token`，pyannote/segmentation-3.0，HF） |
| `--speaker_num` | None | oracle 说话人数（**段数<40 的短音频上被静默忽略**，见 §1） |
| `--nprocs` | GPU 数或 1 | 进程数（mp.spawn） |
| `--diable_progress_bar` | 关 | 关进度条（注意参数名拼写就是 diable） |

模型（ModelScope，均硬编码于源码，无 CLI 覆盖）：
| 用途 | model_id | revision |
|---|---|---|
| 声纹 embedding | `iic/speech_campplus_sv_zh_en_16k-common_advanced` | v1.0.0（ckpt `campplus_cn_en_common.pt`，192 维） |
| VAD | `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | v2.0.4 |
| 重叠分割（可选） | `pyannote/segmentation-3.0`（HF） | — |

内部管线参数（写死在 `get_cluster_backend()` / `Diarization3Dspeaker`）：
| 参数 | 默认值 | 含义 |
|---|---|---|
| `cluster_type` | `spectral` | ≥40 段主聚类；`umap_hdbscan`/`AHC` 可换（需改源码或自建 conf） |
| `cluster_line` | 40 | **段数 <40 走 AHC 短段分支**（`fix_cos_thr=0.4`），≥40 才走主聚类 |
| `mer_cos` | 0.8 | 聚类后合并：簇心 cos≥0.8 的簇再合并 |
| `min_cluster_size` | 4 | ≤4 段的簇被重新指派到最近大簇心 |
| `min/max_num_spks` | 1 / 15 | 谱聚类特征值落差选 k 的搜索范围 |
| `pval` | 0.012 | 谱聚类相似度矩阵按行剪枝比例 |
| `chunk dur/step` | 1.5s / 0.75s | VAD 段内子切分段长/步长 |
| `batchsize` | 64 | embedding 推理批大小 |
| 短段聚类阈值 | 0.4 | AHC `fix_cos_thr` |
| 嵌入模型 | CAM++ | FBank(80) → 192 维 |

能否只出切分边界不出聚类：**没有现成开关**。管线固定为 VAD→子段→embedding→聚类
→压缩合并。只要 VAD 段可拿（`pipeline.do_vad()` 返回 [[st,ed],...]），但保存的只有
聚类后 rttm/json。长音频策略：无滑窗，整段输入整段聚类（状态不跨文件），
靠 VAD 段内 1.5s 子段化控制 embedding 数量。

## 4. 性能（用法摸底，非评测）

- 官方标称 CPU RTF 0.03（含 overlap 检测版，其基准音频）。
- 本机（WSL2，CPU）实测：60s 音频推理 5.9–11.3s（模型热/冷），RTF ≈ 0.10–0.19
  （小文件含固定开销）；**完整 38min 原曲 147.9s，RTF ≈ 0.065**。全曲输出 6 标签
  519 段（`out/diar_full/`，GT 为 6 人 641 段——标签数巧合一致，准度待阶段2 DER）。

## 5. EEND 分支核查（阶段2 第一件事，2026-09-18）

结论：**3D-Speaker 仓库不存在可独立使用的 EEND 切分路径**；其宣传的"EEND 分支"
就是 `--include_overlap` 里的 pyannote/segmentation-3.0（SincNet 分割模型），
职责是**局部重叠语音检测**（帧级），与聚类分支对齐融合——不是"无缝换人切开"工具，
且本环境无法运行。

证据：
1. 源码 `grep -ri eend` **零命中**（speakerlab/ egs/ runtime/ README 全无）。
2. 官方发布文（阿里语音AI 2024-12《通义3D-Speaker多说话人日志功能》，
   developer.aliyun.com/article/1647034 同源镜像 nxrte.com/jishu/54860.html）原文：
   "EEND分支负责检测局部的 overlapping speaker……EEND采用来自于
   pyannote/segmentation 开源的 SincNet 模型"。
3. ModelScope 一体化卡片 `iic/speech_campplus_speaker-diarization_common` 自述
   "基于分段-聚类（segmentation-clustering）模块化的通用说话人日志框架"。
4. pyannote/segmentation-3.0 是 HF **gated 模型**（需账号接受协议+access token），
   实测本机（代理可达 huggingface.co）返回 "Cannot access gated repo"——无 token 跑不通。
5. ModelScope 另有独立 EEND 模型 `iic/speech_diarization_eend-ola-en-us-callhome-8k`
   （EEND-OLA，ICASSP2023），但为**英文 8k callhome** 训练域，对中文 16k 会议域不匹配，
   不纳入擂台赛。

归因：**不存在（作为独立切分路径）+ 用法受限（overlap 分支需 HF token）**。
正式评测只跑聚类分支。对核心诉求"两人无静音衔接也必须切开"：
**聚类分支=同范式（VAD 切段），是改进而非根治**；EEND 类分割模型（pyannote
segmentation 本体、EEND-EDA 系）值得列为后续单独调研对象，不在本擂台赛承诺内。

## 6. 阶段2 正式评测结果（2026-09-18）

DER 口径：scripts/der_eval.py（100ms 帧、GT 重叠按说话人帧、scipy 匈牙利最优映射、
双 collar 档）。完整 json 在 out/bench/der_*.json。

| 音频 | collar 0.25 | strict | miss/FA/conf (0.25) | 系统标签/GT人 |
|---|---|---|---|---|
| M_0m 5min 切片 | 26.21% | 27.56% | 24.9/0/1.3 | 2/6（自研基线同件 **12.3%**） |
| M 全曲 38min | 29.24% | 34.23% | 25.9/0/3.3 | 6/6 |
| L 全曲 39min | 17.83% | 21.47% | 16.6/0/1.2 | 7/7 |
| splice6 拼接件（3D-Speaker） | 62.76% | 69.62% | 3.1/0/59.7 | 5/24 |
| splice6 拼接件（自研基线） | 85.71% | 88.79% | 0/0/85.7 | 2/24 |

要点：
1. **5min 切片输给基线 2 倍**（26.2% vs 12.3%）。该件 GT 由两个主述者（001-M 156s、
   006-F 130s）主导，系统估 2 人不算错，但 miss 25%（覆盖/边界侵蚀）远差于自研管线
   的 VAD+短段策略。全曲 M 会 miss 同样 25.9%。
2. **miss 主导、FA≈0、confusion 小**：3D-Speaker 的问题是"该切开的没切开/该覆盖的
   没覆盖"，不是"乱报人"。与自研管线 VAD 漏检同类病灶。
3. **拼接件**：DER 62.8% vs 基线 85.7%，是唯一明显胜出件但绝对值不可用；
   有人声衔接的缝 3/3 全部跨缝合并（36/48/60s）——验证"聚类分支=同范式，
   对无静音换人不能根治"。合并段长 1.6-3.2s（基线 3.1-5.3s）。
4. 全曲 RTF：M 0.062 / L 0.068（官方标称 0.03；差异部分来自本机 WSL CPU）。
5. 听感台：`out/bench/index.html`（5 块；拼接件红框高亮）。

## 6A. 追加实验 A：无缝打散正面对决（2026-09-18）

件：eval/shuffled/m_rr_gap0_s7（872s，6 人整句零间隙轮转，136 段 118 次换人，
相邻静音 0——比拼接件更狠的密度）。

| 系统 | collar 0.25 | strict | miss/conf | 标签 |
|---|---|---|---|---|
| 3D-Speaker | 36.01% | 38.25% | 34.6 / 1.4 | 3 |
| 自研基线 | 52.58% | 52.78% | 5.5 / 47.1 | 1（全员并入陌生人1） |

- 基线 replay meeting fb7e9ef3f895，运行时段 2026-09-18 11:10:35~11:26:35。
- 换人点切开率 69%（82/118，74 段横跨换人点）；同尺同工具 3D-Speaker 胜 16.6pp。
- 病灶对称：基线=切得开聚不动（conf 47%）；3D-Speaker=聚得准盖不住（miss 35%）。

## 6B. 追加实验 B：降噪公平赛（2026-09-18）

5min 切片经项目 GTCRN（preprocess.denoise_pcm）处理后给 3D-Speaker：

| 版本 | collar 0.25 | strict | miss/conf |
|---|---|---|---|
| 生肉 | 26.21% | 27.56% | 24.9 / 1.3 |
| GTCRN 降噪 | 4.77% | 6.33% | 3.5 / 1.3 |

- miss -21.4pp，confusion 纹丝不动——收益全在 VAD 覆盖；结构（2 标签）不变。
- 口径警示：自研基线同件 12.3% 是 replay_der 口径（10ms 无 collar），与 der_eval
  collar0.25 不可直比；降噪版 strict 6.33% 与 replay_der 口径更接近，同降噪条件下
  仍显著优于自研。
- 补充（同尺复评）：基线 meeting 49385009f6fd 导出终版用 der_eval 复评 =
  **8.86%（c0.25）/ 10.55%（strict）**，与 3D+GTCRN 4.77/6.33 同尺可比，
  3D-Speaker 胜 ~4pp；基线结构=miss 更低(2.1 vs 3.5)、conf 更高(6.8 vs 1.3)。
- 补充（全曲降噪赛）：M 全曲 9.71/14.79（生肉 29.24/34.23），L 全曲
  4.88/8.32（生肉 17.83/21.47）；miss 全在崩塌式下降，标签数 6→7、7→8 轻微过聚。
- 补充（冷知识核查）：**L 会与 M 会的 001-M 是同一人**（ERes2NetV2 cos=0.686>0.60；
  005-F 跨会 cos=0.41 不同人；对照 0.18）。rotate40"双男声合并"是系统对了 GT 错了——
  合并 001-M 进 GT 后 rotate40 修正 DER=**5.79%/6.97%**。
- 补充（rotate_m30_s5 短裁判件）：M 原生 6 人×30s，240s；3D-Speaker+GTCRN
  DER 7.66%/10.03%，6/6 标签 6/6 映射，每人 30s 全部攒够独立簇（命中 77~98%）。

## 7. 阶段1 旧建议存档

1. **评测要用全曲不要切 60s**：<40 段走 AHC 且 oracle 失效，60s 小窗结果严重失真；
   且官方自述 <30s 表现差。完整 5min 子段数 ≫40，才走谱聚类默认配置。
2. **--speaker_num**：阶段2 DER 应测"默认（自动估人数）"档；oracle 档可作上限参考
   （已知短音频失效，全曲上可再验证一次是否透传生效）。
3. **输出是匿名整数标签**，DER 用最优映射（复用 replay_der.py 的 greedy 映射思路即可），
   无注册认人能力——若替代现有管线，"认人"环节仍需自研声纹库比对，擂台赛只比"分人"。
4. **与现有管线对比维度**：DER（replay_der 同款口径）+ CPU RTF + 重叠处理
   （默认关 overlap；开 overlap 需 HF token，评测时建议默认档先测）。
5. **环境坑已排完**，阶段2直接读本目录 README 复装即可；模型已缓存在
   `scd_bench/models/cache/`（已 gitignore）。
