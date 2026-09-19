# 盲测调参实验记录（空声纹库纯聚类，M会5min 重放）

口径：完整重放（replay_wav --speed 2）→ 会末终版 utterances_re → replay_der（最优映射，
**2026-09-16 已修映射 bug：旧版只映射字典序前 6 个 pred 标签，陌生人多于 GT 时 DER 虚高**）。
GT = M_R003S01C01 rttm 前 300s，GT 语音 290s。miss 43s 为 VAD 远场漏检（各轮稳定，硬下限 14.8%）。

## 有效数据（修 scorer 后，完整重放口径）

| # | meeting_id | split_gap | anon_threshold | DER | conf | 备注 |
|---|---|---|---|---|---|---|
| 1 | 0266868ad958 | 0.35 | 0.45(GPU默认) | 24.9% | 27s | 6 主簇高纯度；001 拆两簇(14s)；碎段簇 ~13s |
| 2 | 26eb26c5ddda | 0.20 | 0.45 | 24.3% | 26s | split_gap 0.12→0.20 无显著差异 |
| 3 | 1d84423923cc | 0.20 | 0.40 | 21.4% | 17s | 001 两簇合并，126s 单簇全对 |
| 4 | **6347cbdb2895** | 0.20 | **0.35** | **19.1%** | **11s** | 当前最优；001 128s 100%纯，006 98.5%纯 |
| 5 | 4c03b3c4e9f4 | 0.20 | 0.30 | 19.8% | 13s | **过合并拐点**：002/006 簇被他人语音污染(188f/272f)，002 分出 6s 孤簇 |

## 曲线结论

- anon_threshold 0.45→0.35 单调改善（24.3%→19.1%），**0.30 开始过合并**（用户亲见：一人拆两人 + 两人合一人，与帧数据吻合）→ 拐点在 0.30~0.35，定 0.35；
- split_gap 在 0.12~0.35 对完整重放路径影响很小（分批渐进聚类天然抗桥接）；
- **reannotate（会末重标注 API）与完整重放聚类行为不一致**（一次性全量贪心，桥接污染严重，
  同会议 0.20/0.60 均 55.5%）——**调参禁用该捷径，只认完整重放**。

## 作废数据（勿引用）

- 7f2c5013d239 / bf30446a0e33 / 837cd93c8eeb：修 scorer 前测得（DER 99%+ 为虚高），DB 已清空；
- 空库无 embedding bug（每场全陌生人）修复前数据全部无效。

## 标定参考（2026-09-16 seg_sim_dist.py 实测）

同人会内段间相似度 p10=0.658/中位=0.819，<0.45 仅 3~4%；跨人 max=0.58（女女）。
| 6 | bf11b9e31e4a | 0.20 | 0.35 | 19.1% | 11s | 0.35 复跑复现（用户验收场，历史保留未清） |

## v3（2026-09-16 两次确认制+尾音归属+显示降级，GPU 0e8e653 / 本地 08440e8）

参数：split_gap=0.20 anon_threshold=0.35 confirm_count=2 tail_attach=true tail_min_sim=0.25 display_min_sec=3.0

| meeting_id | 场景 | DER | conf | 备注 |
|---|---|---|---|---|
| 0274725ea401 | M会5min | **18.5%** | 9s | 标签 23→5（碎簇被尾音吸收/显示降级收编）；尾音贴附4例+不贴16例全程有日志 |
| daa8e917b02b | 三人1.5s交替 | 64.0% | 61s | 基本无变化（65.4→64.0）：快交替向量ambiguous是主因，两次确认治不了 |
| 2a9c48829413 | 三人~12s交替 | 32.0% | 29s | 略改善（32.4→32.0） |

结论：碎段/尾音问题按设计解决；快交替污染残留 = 短轮次向量区分度问题（24/28错配段embedding模糊，已诊断），
候选方向=簇心加固（攒段定形/离群剔除）或接受为模型极限。

## v4（2026-09-16 簇心加固：sim*dur 加权更新 + 稳健定形 + 升级踢出，GPU b7a155a）

| meeting_id | 场景 | DER | conf | 踢出触发次数 |
|---|---|---|---|---|
| 3d90842afc83 | M会5min | 18.7% | 9s | 0 |
| b1ea4725cee7 | 三人1.5s交替 | 64.0% | 61s | 0 |
| 9b6d8df15dd3 | 三人~12s交替 | 32.4% | 30s | 0 |

结论：收益为零（差异均在噪声内）。机制从未触发——错配段两两相似度 0.64 vs 0.65 都高于 0.35 地板，
"真心互像"不算离群。证实快交替残留 = ERes2NetV2 对 ~1.5s 短轮的向量区分度极限（模型层），
聚类层加固无解。防御性价值保留（防未来雪崩），不再在此投入。

## 热词专题（2026-09-17，eval_cer.py 口径，M会/S会各300s）

机制修复：FunASR SeACo 的 kwargs 名是 hotword(单数)，此前传 hotwords 被静默忽略=从未生效（GPU 2876cb2）。
修复验证（开卷翻转测试）：教室→教师✓ 除师→厨师✓ 只掰声学歧义处，符合原理。

CER×延迟扫描（修复后）：

| 词表 | M会 CER | S会 CER | 延迟 |
|---|---|---|---|
| 无热词 | 23.70% | 9.53% | 62s |
| OA378 企业词 | 23.70% | 9.61% | 78s |
| THUOCL 5k(新闻语域) | 25.76% | — | 236s(×4) |
| 日常+英文 2206词(jieba日常1500+口语380+EN110+OA) | 24.42% | 9.94% | 121s(×2) |

结论：
1. 词表域不匹配/过大 → CER 反升 + 延迟倍增（SeACo 对每个词编码+逐帧偏置，硬成本）。
2. 评测会零英文零公司术语 → 整体 CER 量不出热词真正价值（术语保护）。
3. 定案：热词=术语保镖，小而准（公司词+英文词+人名，300~600词），验收用针对性翻转测试，不做全局CER承诺。

## 热词权重×词表矩阵（2026-09-17 完整，eval_cer 口径）

基线(无热词): M 23.70 / S 9.53 · 62s

| 词表 \ λ | 1.0 | 0.3 | 0.1 |
|---|---|---|---|
| OA378 | 23.70/9.61 · 78s | 23.70/9.53 · 81s | — |
| 日常2206 | 24.42/9.94 · 121s | 23.97/9.61 · 124s | 23.97/9.44 · 125s |
| 日常5000(jieba域) | — | 23.79/9.61 · 191s | 23.70/9.53 · 183s |

定案：日常5000词 + λ=0.1（用户假设"权重太大而非词太多"被证实；λ0.1 下 5k 词质量零代价，代价延迟×3）。
机制修复记录：hotword 单数 kwargs（2876cb2）、seaco_weight 旋钮（4e699cd，λ=1 恒默认且 inference 不透传已 monkeypatch）。
标点+ITN 接入（0014c01 + 本地 af213a6）：CT-Transformer 逐句标点，WeTextProcessing ITN（零幺四->014、百分之五十->50%）。
注意：ITN 默认去儿化和去语气词（啊/呃），产品可接受但已留意。

## 文本层纠错基准（2026-09-17，GT 文本误纠实验，GPU /tmp/hw_bench/bench.py）

方法：在 M 会 300s 的 **TextGrid 标准文本**（本来就正确）上跑 FunASR postprocess_hotwords，
则每次替换=一次误纠。三档词表×三档阈值：

| 词表 | th=0.85 | th=0.90 | th=0.95 | 耗时(300s会) |
|---|---|---|---|---|
| 5k | **437 误纠** | 293 | 162 | 5.8s |
| 20k | 455 | 263 | 117 | 22s |
| 80k | 525 | 351 | 220 | 85s |

误纠样例（全是把对的改错）：校长→晓庄、幼儿→豫尔、大家→单价、儿园→二院、会因→会阴、
叫过→酵粿、<sil>→Word、这次啊→这次。

结论：
1. 机制是**无调拼音模糊匹配**，同音/近音词必然互纠（教室/教师拼音相同=1.0 分），
   词表越大误纠越多，提高阈值只缓解不治本（0.95 仍 117~220 次/场）。
2. 暴力滑窗 O(词表×文本)：80k 词 85s/场 CPU，线性恶化。
3. **判决：FunASR 原生模糊纠错不可用**。大词表直接全用的设想在此机制下不成立。
活路两条：A) 显式"错词=>对词"对表（精确匹配零误伤，需人工/测试积累）；
B) LLM 上下文纠错（词表塞 prompt，管 5k 词无压力，理解上下文不会误纠，
   可并入现有纪要 LLM 调用，增量成本≈0）。待用户拍板。

### 补充实验（同日，证明非误用）：缩小到"官方合理规模"词表

| 词表 | th=0.85 | th=0.90 | th=0.95 |
|---|---|---|---|
| OA378 领域词 | **531 误纠** | 413 | 406 |
| EN110 英文词 | 35 | 35 | 35 |
| 仅显式对表（教室=>教师） | 0 | 0 | 0 |

误纠样例：零→梁、幺→姚、幼→有、校→萧、长→张、要→姚、家→贾、过→郭、来→赖、园→袁。
关键发现：
1. **词越小越糟**——OA378 全是 1~2 字短词，误纠数反超 5k 词表；1 字窗对 1 字词拼音相同即 1.0 分，
   提高阈值完全无效（0.85→0.95 只降 20%）。
2. 结论坐实：模糊模式=无语境的拼音同音字替换器，任何规模都不可用；
   **唯一可用模式=显式"错词=>对词"对表（结构上零误伤）**。LLM 上下文纠错为另一条活路。

## VAD 尾端静音实验（2026-09-17，M会300s重放，降噪开）

| VAD end_silence | meeting_id | DER | 终版子段数 | 结论 |
|---|---|---|---|---|
| 250ms（旧） | a09b6918322d（门禁-降噪开启，他AI测） | **12.3%**（AGENTS记录） | 多（1-2s碎片） | 归属准，展示碎 |
| 800ms（本次） | bb3cd75f14e8 | **48.8%**（miss10+FA2+conf130 /290s） | 29 | 灾难：预测只剩陌生人1 |

诊断（diag_opening_sim.py，生产同路径 降噪→VAD→embed）：
- 800ms 下开场 4-14s 的 7 人自我介绍（人间停顿仅~300ms）被揉成一段 7.45s 多人混合段
  → embedding 是多人平均向量 → 聚类只能贴一个标签。跨人相似度 0.34 与地板 0.35 贴边是
  混合段的假象，不是降噪的锅（raw vs denoise 矩阵几乎一致，干净会 GTCRN 近透明）。
- 800ms 对连读讲话友好（22-81s 段合并完整），但归属原子被毁，DER 不可接受。

结论：VAD 放宽方向错误。正解 = VAD 回 250ms（保 DER 12.3% 的归属原子）
+ 连续同说话人短段显示层合并（解决展示碎）。待用户拍板后实施。

## "全场陌生人1"根因定位（2026-09-17，bb3cd75f14e8，VAD800）

GT 结构：001 独白 14.5-168.6s（156s），006 独白 170-295s（124s），开场 4-14s 六人轮流。
全场只出陌生人1 = 006 的 124s 全被吸进陌生人1（映射 001）。

diag_two_speaker.py（GT 段直算，排除 VAD 因素，降噪路径）：
- 006 段 vs 006 质心：0.885~0.964（mean 0.937）
- 006 段 vs 001 质心（陌生人1）：0.317~0.465（mean 0.374），**12/15 段 ≥ 地板 0.35 → 全部被吸收**
- 两质心互似仅 0.399；同人/跨人 margin ≈ 0.57，极大

结论：入簇地板 0.35 正好压在跨人相似度带(0.32~0.47)中间。800ms 长段向量稳，
跨人相似度 confidently 0.37+ → 新说话人永远进不了候选（被贪婪吸收，两次确认制永不触发）。
250ms 时短段向量抖、大量落在 0.35 以下 → 能进候选成簇，所以旧配置 DER 12.3%。
数据支持把地板大幅上调（跨人 max 0.465 vs 同人 p10 0.66+，0.50~0.55 都安全），
或段长自适应地板（≥3s 长段用高地板）。待用户拍板。

### 降噪对声纹分布的影响（diag_denoise_margin.py，同音频 GT 段 raw vs 降噪）

| 指标 | RAW | GTCRN |
|---|---|---|
| 001 同人 min | 0.902 | 0.859 |
| 006 同人 min | 0.890 | 0.885 |
| 006→001 跨人 max / mean | **0.520 / 0.447** | **0.465 / 0.374** |
| margin（同人min-跨人max） | 0.370 | **0.394** |

用户假设证实：降噪主要把**跨人**相似度压下来（噪声驱动的假性相似被消除），
同人几乎不动 → 区分度拉大。推论：地板 0.35 在降噪后更离谱（跨人带 0.37~0.46 整体悬空
在地板上方），上调地板的安全边界更宽。

## 聚类地板标定（2026-09-17，calib_floor_gpu.py，3场会×前300s×GT标签×250ms段×GTCRN路径）

| 组 | n | p95/max（跨人） | p5/min（同人） |
|---|---|---|---|
| 男男 MM | 532 | 0.424 / **0.573** | — |
| 女女 FF | 224 | 0.428 / 0.520 | — |
| 男女 MF | 733 | 0.400 / 0.583 | — |
| 同人 M | 312 | — | 0.564 / 0.180 |
| 同人 F | 133 | — | 0.518 / 0.354 |

合并：同人 p5=0.548，跨人 p95=0.415 → **中点=0.481**。
关键读数：
1. 现地板 0.35 < 跨人 p95 0.415 → 结构上必然过合并，标定实锤。
2. 跨人 max 0.52~0.58 超过中点——尾部重叠无法靠单一阈值根治，0.48 是最小错误率工作点。
3. 男男 max 0.573 偏高（M会002仅1段、S会003-M为主，男男样本仍薄），DER 验证重点盯。
4. 同人 min 0.18 离群段存在（疑似错标/极短段），p5 更可信。

## 说话人分离擂台赛 阶段1：3D-Speaker 用法研究（2026-09-18，scd_bench/，未跑正式评测）

背景：拍板评估 3D-Speaker（ModelScope 分发，VAD+CAM++embedding+谱聚类 管线，
官方 AISHELL-4 DER 10.30% w/ overlap、CPU RTF 0.03）能否替代自研分离管线。
试验田 `meeting-asr/scd_bench/`（自包含，README/NOTES 在其中），阶段1=用法研究。

环境（WSL2 本机，uv venv --python /usr/bin/python3 3.10.12）：
- torch 2.6.0+cpu / torchaudio 2.6.0+cpu、modelscope 1.24.1、funasr 1.4.15、
  pyannote-audio 4.0.7、numpy 2.2.6、scikit-learn 1.7.2、scipy 1.15.3、
  fastcluster/umap-learn/hdbscan（speakerlab 隐式依赖）、addict/datasets（modelscope 运行期缺包）。
- MODELSCOPE_CACHE 重定向 scd_bench/models/cache（CAM++ 28M + FSMN VAD 1.7M 已缓存）。

踩坑（按 安装/用法/能力 三分类）：
1. [安装] infer_diarization.py 模块级 import pyannote.audio，requirements.txt 未声明
   → uv pip install pyannote.audio（顺带把 numpy 升 2.x、sklearn 升 1.7.2，
   官方钉的 numpy<1.24/sklearn==1.0.2 被打破，按"示例能跑通"准则放行并记录）。
2. [安装] speakerlab/process/cluster.py 模块级 import fastcluster/umap/hdbscan，未声明 → 补装。
3. [安装] torchaudio≥2.11 的 torchaudio.load 硬走 torchcodec（需系统 ffmpeg7+CUDA 库，
   CPU 环境无解）→ torch/torchaudio 退 2.6.0（自带 sox 后端），卸载 torchcodec。
4. [安装] modelscope VAD pipeline 需 modelscope[audio]（funasr），且运行期缺 addict/datasets。
5. [安装] modelscope[audio] 会把 torch 换成 CUDA 版 → 装完 --reinstall --no-deps 回 CPU 版。

官方示例复现（成功，EXIT=0）：
```
export MODELSCOPE_CACHE=$PWD/models/cache
.venv/bin/python 3D-Speaker/speakerlab/bin/infer_diarization.py \
    --wav out/M_R003S01C01_ch0_head60s.wav --out_dir out/diar_head60s
```
小样本核对（前 60s，M_R003S01C01，GT 11 段 6 人）：
- 系统 8 段 3 标签。短轮次 5 人（002-M/003-F/004-F/005-F/006-F，各 1.3~1.7s）
  被合并为 1 簇（段内五人重叠 17~23%）；主持人 001-M 被劈成 2 簇交替。
- 边界：首段完美（Δ+0.01s）；长段起点系统性偏晚 +1.2~1.5s，内部有无中生有切换。
- [能力] 归因：子段<40 走 AHC 短段分支（fix_cos_thr=0.4），谱聚类参数不生效；
  且 --speaker_num oracle 在该分支被静默忽略（cluster.py:192 不透传）。
- [用法] 全曲 38min 计时 147.9s（RTF≈0.065，官方标称 0.03）；全曲输出 6 标签 519 段
  （GT 6 人 641 段）。60s 小窗 RTF 0.10~0.19。
- 阶段2建议：必须全曲评（勿用 <40 段短窗）；DER 复用 replay_der greedy 映射；
  默认档（自动估人数）为主、oracle 档作上限参考。详见 scd_bench/NOTES-用法研究.md。

## 说话人分离擂台赛 阶段2：正式评测（2026-09-18，进行中）

### EEND 分支核查（评测前第一件事）
- 3D-Speaker 仓库 `grep -ri eend` 零命中；官方发布文（阿里语音AI 2024-12，
  aliyun article/1647034 同源镜像 nxrte.com/jishu/54860.html）自述"EEND分支=
  pyannote/segmentation-3.0（SincNet），职责=局部重叠语音检测，与聚类分支对齐"。
- ModelScope 一体化卡片 speech_campplus_speaker-diarization_common 自述
  "分段-聚类模块化框架"。→ EEND 无独立切分路径。
- pyannote/segmentation-3.0 为 HF gated 模型，实测 "Cannot access gated repo"（代理可达），
  无 token 跑不通。ModelScope 另有 EEND-OLA（iic/speech_diarization_eend-ola-en-us-callhome-8k）
  但英文 8k callhome 域不匹配。
- 归因：不存在（独立路径）+ 用法受限（overlap 分支需 HF token）。评测只跑聚类分支；
  对"无静音换人切开"诉求：聚类分支=同范式，改进而非根治。

### 评测集
- 5min 切片 M_R003S01C01_0m（GT=0-300s 截断 40 行 6 人）
- M 全曲 2282s / L 全曲 2362s（完整 rttm）
- 无缝拼接件 splice6.wav（72s）：L_R003S01C02, M_R003S02C01, S_R003S01C01,
  L_R004S02C01, M_R003S04C01, S_R004S01C01 各前 12s 零间隙拼接；
  GT 25 段 24 说话人（ID 已加会议名前缀，scripts/make_splice.py）
- DER 口径：scripts/der_eval.py，100ms 帧、GT 重叠按说话人帧计、scipy 匈牙利最优映射、
  collar 0.25（标准）与 0（strict）双档。

### 已完成结果（der_eval 口径）
- M_0m 聚类分支：collar0.25 DER 26.21%（miss 24.93+conf 1.28），strict 27.56%
  ——系统只得 2 标签（0→001-M, 1→006-F），而 0-300s GT 由 001-M(156s)+006-F(130s)
  两个主述者主导（微轮次 002-005 各仅 1-2s）。对比自研基线同件 12.3%（降噪后）。
  归因： miss 主导=边界/覆盖侵蚀+微轮次丢失；人数估成 2 未崩但不覆盖。
- 拼接件自研基线（replay meeting 9dbe0cd487ae，21 段仅 2 标签）：
  replay_der 原生口径 DER 119.7%；der_eval 口径 collar0.25 85.71% / strict 88.79%
  （纯 confusion：全部并给陌生人1）。跨缝合并实锤。

### 阶段2 全部 DER 结果（der_eval 口径：100ms 帧/匈牙利映射/GT 重叠按说话人帧）
| 音频 | 时长 | collar0.25 | strict(0) | miss/FA/conf (c0.25) | 系统标签/GT人 |
|---|---|---|---|---|---|
| M_0m 5min 切片 | 300s | 26.21% | 27.56% | 24.93/0/1.28 | 2 / 6（基线同件 12.3% 胜出） |
| M 全曲 | 2282s | 29.24% | 34.23% | 25.94/0/3.30 | 6 / 6 |
| L 全曲 | 2362s | 17.83% | 21.47% | 16.58/0/1.24 | 7 / 7 |
| splice6 拼接件 3D-Speaker | 72s | 62.76% | 69.62% | 3.06/0/59.69 | 5 / 24 |
| splice6 拼接件 自研基线 | 72s | 85.71% | 88.79% | 0/0/85.71 | 2 / 24 |

- 基线原生口径（replay_der，10ms 无 collar）：DER 119.7%。两口径并列报告。
- 拼接缝检查（scripts/splice_check.py）：3D-Speaker 3/5 缝跨缝合并（36/48/60s，
  全发生在"左侧人声顶到缝"的缝上=有人声衔接的缝 3/3 全中）；自研基线 5/5 全合并。
  3D-Speaker 合并段更短（1.6-3.2s vs 基线 3.1-5.3s），且聚出 5 标签 vs 基线 2 标签。
- 失败归因三分类：能力问题——聚类分支=同范式（VAD 切段），无静音换人不能根治
  （3/3 人声衔接缝全合并）；miss 主导（M 会 25.9%）=边界/覆盖侵蚀，与自研 VAD 漏检同类。
- 全曲 RTF 实测：M_full 141.0s/2282s=0.062，L_full 161.3s/2362s=0.068
  （官方标称 0.03；小件含模型加载固定开销：5min 81s、72s 拼接件 51s）。
- 听感台：scd_bench/out/bench/index.html（5 块：拼接件×2 系统 + 切片/M全曲/L全曲；
  每块=整段+按说话人合并试听 0.2s beep+段表+GT/系统时间线，相对路径）。
- 基线 replay meeting_id=9dbe0cd487ae（后端 replay 前确认无进行中会议）。
- 结论：3D-Speaker 聚类分支 4 件全面差于或接近基线（拼接件 62.8% vs 85.7% 是唯一
  明显胜出项，但绝对值仍不可用）；无 EEND 独立路径。待用户听感验收定夺（阶段3）。

## 擂台赛追加实验 A/B（2026-09-18，用户听感台后拍板）

### 实验A：无缝打散正面对决（eval/shuffled/m_rr_gap0_s7，872s）
- 造件：shuffle_meeting.py --gap 0 --mode roundrobin --max-seg 8 --min-seg 0.8 --seed 7
  --max-sec 900（M会前15min，6人整句零间隙轮转，136段/118次换人=8.1次/min，相邻静音0）
- 3D-Speaker：238段 3标签，DER collar0.25 36.01% / strict 38.25%（miss 34.6 conf 1.4）
- 自研基线：replay meeting fb7e9ef3f895（★基线运行时段 2026-09-18 11:10:35~11:26:35，
  期间勿用网页录音；开始前已确认无进行中会议），110段 **1标签**，DER 52.58% / 52.78%
  （miss 5.5 conf 47.1——VAD覆盖好但全员并入陌生人1）
- 换人点切开率：3D-Speaker 82/118=69%（74段横跨换人点）；同尺同工具对比 3D-Speaker
  胜 16.6pp，但绝对值不可用。病灶对称性：基线=切得开聚不动，3D-Speaker=聚得准盖不住。
- 对比拼接坟场(splice6 62.8%)：打散件更难（无缝轮转 vs 缝间天然静音），3D-Speaker
  在小标签数(3 vs 5)下 confusion 仅1.4%——其聚类器本身稳，输在覆盖。

### 实验B：降噪公平赛（5min 切片，项目 GTCRN 经 preprocess.denoise_pcm 处理）
- 3D-Speaker+GTCRN：46段 2标签，DER collar0.25 **4.77%** / strict 6.33%
  （miss 3.49 conf 1.28）vs 生肉 26.21% / 27.56%（miss 24.93 conf 1.28）
- **miss 24.9→3.5（-21.4pp）**，confusion 不变——收益全部来自 VAD 覆盖；
  结构(2标签)不变。注意口径：自研基线同件 12.3% 为 replay_der 口径（10ms无collar），
  与 der_eval collar0.25 不可直比；strict 6.33% 与 replay_der 口径更接近，仍显著低于 12.3%。
- 归因[能力]：3D-Speaker 生肉的 miss 病灶=FSMN VAD 对噪声敏感，降噪可解——
  与自研管线当年"DER 19→12.3"同源。

## 擂台赛补充：同尺复评基线 + 全曲降噪赛（2026-09-18）

### 同尺复评 5min 切片基线（meeting 49385009f6fd，原 12.3% 为 replay_der 口径）
- 导出 utterances_re → out/bench/diar_M_0m_baseline/M_0m_baseline.rttm（167 段 2 标签）
- der_eval 同尺：collar0.25 **8.86%**（miss 2.10 conf 6.76）/ strict 10.55%
- 与 3D-Speaker+GTCRN 同尺对照：4.77% / 6.33% —— 3D-Speaker 胜 ~4pp（同工具同口径）
- 结构对比：基线 miss 更低（2.1 vs 3.5，覆盖好）但 conf 更高（6.8 vs 1.3，微轮次错并多）

### 全曲降噪赛（GTCRN via preprocess.denoise_pcm → 3D-Speaker）
| 件 | 生肉 c0.25 / strict | 降噪 c0.25 / strict | miss 变化 |
|---|---|---|---|
| M 全曲 | 29.24 / 34.23 | **9.71 / 14.79** | 25.9→5.2 |
| L 全曲 | 17.83 / 21.47 | **4.88 / 8.32** | 16.6→3.0 |
- 标签数 M 6→7、L 7→8（轻微过聚，conf +1~3pp），miss 崩塌式下降主导净收益。
- 结论：全曲降噪条件同样碾压生肉，与 5min 切片实验B 规律一致——GTCRN 是 3D-Speaker
  的前置刚需，不是可选项。

## 裁判件 rotate40_s3 构造（2026-09-18，只造件+自检，未跑任何识别）

- 脚本 scd_bench/scripts/make_rotate_meeting.py（--seed 3 --rounds 15，规格见 docstring）
- 产物：eval/shuffled/rotate40_s3.{wav,rttm,recipe.json}
- 阵容：001-M(M)/L_001-M(L补,M_002-M素材105s不足)/L_005-F(L补,M_003-F素材64s不足)/
  004-F/005-F/006-F——3男3女优先，M会仅2男声且L会可用男声均<170s，实际2男4女（recipe 记录）
- 自检：总时长 2400.0s（正中目标）| 语音 1081s 占 45.0% | 15 轮 75 次换人
  | 每人 176.6~182.7s 全在 [170,190] | 换人气口 0.83~2.00s(均1.41) | 轮间 52.9~90.0s(均83.3)
  | 素材复用 0 | rttm 无重叠、独立复算与 recipe 一致 | 语音区/静默区 RMS 对比 8e6 倍
- 构造评价：说话节奏（1~3句/轮、气口0.8-2s）拟真；但 45% 语音密度+20~90s 长静默
  明显比 aishell4 原生会议（~90% 密度、抢话重叠）温和——是"中等负荷裁判件"，
  测"切分+聚类基本功"，不测极端密度。

## 聚类地板三件套定档（2026-09-18：VAD 回 250 + 地板 0.48 + 显示层合并）

实施（用户拍板"设置为0.48并确保欧克"）：
- VAD 800→250：800 证伪（开场自我介绍揉团，DER 48.8% vs 12.3%，bb3cd75f14e8）
- 地板 0.35→0.48：GT 标定同人 p5=0.548/跨人 p95=0.415 中点 0.481
- 显示层合并：详情接口同 speaker 相邻段间隙 ≤2s（display_merge_gap_sec）并一行；
  DB 保原子段，纠错/评测不受影响

DER 验证（M 会 300s 重放，GTCRN 降噪路径，replay_der 口径）：
| 地板 | meeting_id | DER | 分解 |
|---|---|---|---|
| 0.48（定档） | a631a822c911 | **10.1%** | miss 14s + FA 2s + conf 13s / 290s |

对比基线：地板0.35+250ms+降噪 = 12.3%。映射全对：陌生人1=001（156s独白）、
陌生人3=006（124s独白），006 被吸收进陌生人1 的过合并消除。
0.45/0.50 两档用户要求自己跑补测。

环境事故记录：GPU diarize 服务于 2026-09-18 被静默杀死 2 次（无报错、无 OOM
迹象、无内核日志权限可查）；已在 GPU 部署 run_supervised.sh 监督自启
（3s 拉起，死亡时间线 diarize_svc_respawn.log）。

## rotate40_s3 裁判件首测（2026-09-18，3D-Speaker+GTCRN，公平条件；未跑自研基线——8010 被救援AI占用）

- 流程：denoise_wav.py（GTCRN，2400s）→ infer_diarization（wall 144.4s，RTF 0.060）
  → der_eval vs eval/shuffled/rotate40_s3.rttm
- DER：collar0.25 **19.93%**（miss 3.25 conf 16.68 FA 0）/ strict 20.94%
- **标签 6/6 GT 全中**；换人点切开 97%（85/88）；覆盖 89~99%/人
- 病灶定位：双男声（001-M 与 L_001-M）被合并进同一簇（标签1 含 001-M 96% 帧），
  另产 1 个 10.7s 垃圾簇（标签5 未映射）——conf 16.7% 几乎全部来自这一次男男合并。
  若拆开，DER 将 ~3-4%。其余 4 女 1 男各归各簇、命中 90-97%。
- 对比：raw shuffle 36.0% → 裁判件+降噪 19.9%（密度温和+GTCRN 双重收益）；
  miss 3.25% 证明"降噪接入"后覆盖问题在常规密度件上基本解决。
- 听感台新增"rotate40 裁判件"区块（红框，9 块）。json: out/bench/der_rotate40_c*.json

## 冷知识核查：aishell4 L 会与 M 会同编号是否同人（2026-09-18，GPU 声纹验证）

- 工具：生产同款 iic/speech_eres2netv2_sv_zh-cn_16k-common（GPU funasr 1.4.13 venv），
  脚本 GPU:ai-script/check_cross_meeting_spk.py（本地副本 scd_bench/scripts/）
- 三对余弦（各取 5~8s 段）：
  ① M_001-M vs L_001-M = **0.6863** → 同人(>0.60) ★用户冷知识为真
  ② M_005-F vs L_005-F = 0.4131 → 不同人
  ③ M_001-M vs M_004-F = 0.1834 → 不同人（对照组，方法学正常）
- 推论：rotate40 裁判件上 3D-Speaker 把 001-M 与 L_001-M 合一簇**是对的**，
  是 GT 把同一人拆成两人。修正 GT（001-M 合并）重评：
  **DER collar0.25 5.79%（miss 3.25 conf 2.54）/ strict 6.97%**，6 标签全映射 5 GT 人。
  rotate40 真实成绩从 19.9% 修正为 5.8%——"双男声合并"病灶撤销，残留=垃圾簇+微 conf。

## rotate_m30_s5 短裁判件（2026-09-18，任务2/3；M 原生 6 人，未跨会议）

- 构造：make_rotate_meeting.py --roster m6 --seed 5 --target-speech-sec 30 --rounds 6
  --seg-max 15 --max-sents 2 --round-pause 0 3（v2 参数化版；v1 选择逻辑见脚本历史）
- 自检：240.0s | 语音 184s 76.7% | 每人 30.2~32.2s | 6 轮 30 次换人 | 气口 0.81~1.99s
  轮间 1.0~3.0s | 复用 0 | 独立复算一致
- 3D-Speaker+GTCRN：DER collar0.25 **7.66%**（miss 4.85 conf 2.81）/ strict 10.03%
  | **6 标签/6 GT/6 全映射/无垃圾簇** | 每人均攒够独立簇（标签命中 77~98%，覆盖 86~99%）
  ——核心考点"30 秒配角能否成簇"：6/6 全过（006-F 最低 77% 但簇独立）。
- 听感台第 10 块（红框置顶）。json: out/bench/der_rotate_m30_c*.json

## 换芯定裁：3D-Speaker 接入一期 A 阶段（2026-09-19，只新增文件，未改任何现有代码）

### 依赖并入项目 venv（.venv，uv 管理；torch 保住 2.8.0+cpu）
- 装法按 scd_bench README 踩坑指南：torchaudio 2.8.0 走 CPU 索引先装（与 torch 2.8 匹配）；
  3D-Speaker requirements 的 numpy<1.24/sklearn==1.0.2 钉**未采用**（scd_bench 已证
  numpy 2.2.6+sklearn 1.7.2 全通，生产 venv 也已是 numpy 2.2.6，不可降级）。
- 清单：torchaudio 2.8.0+cpu、scipy、scikit-learn 1.7.2、tqdm、kaldiio、matplotlib、pandas、
  openpyxl、pyannote-audio 4.0.7、fastcluster、umap-learn、hdbscan、modelscope[audio]
  → modelscope 1.24.1 + funasr 1.4.16、addict、datasets。
- 与 scd_bench 不同的坑：因 torchaudio 2.8.0+cpu 预装到位，modelscope[audio] 这次**没换**
  torch（无 nvidia/triton 残留可清）；uv pip uninstall 空列表报 usage（无害）。
- 装后核对：torch 2.8.0+cpu / torchaudio 2.8.0+cpu / numpy 2.2.6 未动；
  app.core.preprocess.denoise_pcm 冒烟通过。

### third_party 落位
- server/scripts/fetch_3dspeaker.sh（代理 127.0.0.1:7897，死了直连；tarball 解压到
  server/third_party/3D-Speaker/）。坑：校验用 `tar tzf | head` 在 pipefail 下吃 SIGPIPE
  中断，改 `tar tzf > /dev/null`。
- .gitignore 追加 server/third_party/（本次唯一改动的现有文件）。

### wrapper server/app/core/diarize3ds.py（新文件）
- diarize(pcm: bytes, sr=16000) -> [{start,end,cluster_id}]（秒）；懒加载单例；
  sys.path 注入 third_party；MODELSCOPE_CACHE 默认 server/models/cache（setdefault）；
  聚类参数模块常量 + TODO(B 阶段接 config.py)。docstring 写明来源/契约/限制(<40段AHC、
  max_num_spks=15、无滑窗、CPU RTF≈0.06)。

### 等价性验收（项目 venv 跑 wrapper，denoise_pcm 降噪，der_eval 同口径）
| 件 | collar 0.25 | strict | scd_bench 目标 | 偏差 |
|---|---|---|---|---|
| rotate_m30_s5 | 7.66% | 10.03% | 7.66 / 10.03 | **+0.00pp 全等** |
| M_R003S01C01_0m | 4.77% | 6.33% | 4.77 / 6.33 | **+0.00pp 全等** |
- 输出 rttm：scd_bench/out/equiv/*.rttm；验收脚本 scd_bench/scripts/equiv_check_3ds.py
  （两档 collar 各对各自目标，>1pp 报警）。
