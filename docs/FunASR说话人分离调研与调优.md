# FunASR 说话人分离：调研结论与调优经验

> 2026-09-07 · 本文档沉淀「FunASR 分人能力」的调研结论，指导配置面板参数调整。
> 来源：FunASR 官方博客/教程、GitHub issues、社区实战文章、以及我们自己 gpu 部署的实测。

## 一、FunASR 到底负责什么

FunASR 是**可组合的框架**，不是单一模型。一次 `AutoModel.generate()` 可同时输出
「转写 + 说话人标签 + 句级时间戳 + 标点」：

```python
model = AutoModel(
    model="paraformer-zh",   # ASR：语音→文字
    vad_model="fsmn-vad",    # VAD：端点检测（切句）
    punc_model="ct-punc",    # 标点
    spk_model="cam++",       # 说话人分离（声纹 + 聚类）
)
res = model.generate(input="meeting.wav", batch_size_s=300)
# res[0]["sentence_info"] = [{spk, start, end, sentence}, ...]
```

结论：**FunASR 本身确实自带说话人分离**，无需 pyannote+Whisper 拼装。
我们系统用的就是这套（gpu:10095 的 2pass 服务，spk 部分换成了更强的 ERes2NetV2）。

参考：
- [用 FunASR 做说话人分离：一次调用输出「谁在何时说了什么」](https://www.funasr.com/blog/funasr-speaker-diarization.html)（官方实测：227s 录音自动分出 11 人）
- [FunASR 使用教程](https://modelscope.github.io/FunASR/zh/tutorial.html)

## 二、为什么官方分得准、我们分不准

官方博客是**离线整段**跑（`batch_size_s=300`，VAD 不激进）；我们是**实时流式**，
为出字快把 VAD 切得很碎。碎段的声纹 embedding 不稳 → 同一人不同段的相似度
跨不过阈值 → 反复新建说话人（"说话人1/2/3 反复改名"的根源）。

**核心矛盾：实时出字速度 ⟷ 声纹稳定性，二者靠 VAD 参数平衡。**

## 三、可调参数全景（已在配置面板透传）

| 参数 | 面板名 | 默认 | 作用 | 调整方向 |
|---|---|---|---|---|
| `spk_threshold` | 聚类阈值 | 0.55 | 声纹 cosine ≥ 此值判同一人 | **调高**=更不易并人（分出的人变多）；**调低**=更易并人。社区推荐 0.3–0.7，ERes2NetV2 配 0.5–0.65 |
| `max_speakers` | 人数上限 | 0(不限) | 匿名聚类最多 N 人，超出**强制并入最相近簇**（宁混不裂） | 实际 2 人却分出 5 人时，填 2 或 3 可硬性兜底 |
| `vad_max_end_silence_ms` | 断句静音 | 350 | 静音多久判"说完"（模型原默认 800） | **调大**=句子更完整、声纹更稳、出字更慢；调小反之 |
| `vad_max_segment_sec` | 单段上限 | 8 | 单段最长秒数强制切（模型原默认 60） | **调大**=段更长声纹更稳，但长段可能混多人 |
| `spk_min_seg_sec` | 最短声纹段 | 1.0 | 短于此的段不做声纹（碎段 embedding 不可信） | 分人乱时可提高到 1.5–2，代价是短句全标"发言人" |
| `speaker_count` | 参会人数 | 0(自动) | 传给阿里 pipeline 的 oracle（确切人数）| 填真实人数是**单参数收益最大**的调法 |

调参顺口溜：
- **人分多了**（幻影说话人）→ 聚类阈值调低 / 填人数上限 / 填真实人数
- **人分少了**（两人并一人）→ 聚类阈值调高 / 断句静音调大 / 最短声纹段调大
- **文字碎**（一句话拆几段）→ 断句静音调大（500–800）
- **出字慢** → 断句静音调小 / 单段上限调小

## 四、关键认知：三层分人体系（我们的现状）

1. **FunASR 实时层**（gpu:10095，ERes2NetV2 + 会话内匿名聚类）：
   出字即带初步标签，阈值/人数上限就是这一层的参数。
2. **阿里 pipeline 滚动层**（gpu:10096，campplus diarization + ERes2NetV2 投票）：
   每轮窗口（有效语音≥8s）重新打标，覆盖第一层的错误。`oracle_num` 优先用前端填的
   参会人数；没填则自动在 2..`diarize_max_oracle`(8) 里挑簇内相似度最高的。
3. **会末重标注层**（同上服务，整段录音）：结束后全量重打，兜底修正。

所以**实时层分不准没那么致命**（二三会修正），但实时层参数决定了用户第一眼看到的体验。

## 五、官方/社区经验要点

- **指定人数收益最大**：`preset_spk_num`（FunASR AutoModel）/ `oracle_num`（阿里 pipeline）
  都是"告诉模型确切人数"，聚类空间被硬约束后准确率显著提升。社区项目
  [OddASR](https://juejin.cn/post/7671107024913399842) 把「preset_spk_num / max_num_spks /
  merge_thr / spk_mode」做成 4 个核心配置项，思路与我们配置面板一致。
- **spk_model 选型**：官方 issue
  [#2944](https://github.com/modelscope/FunASR/issues/2944) 明确"对说话人分离效果要求高，
  推荐 Paraformer + cam++（长期优化）"。我们已把声纹升级为 ERes2NetV2（实测优于 cam++，
  见 `docs/说话人分离优化方案-v2.md`），不违背此建议。
- **VAD 是分人效果的地基**：阿里云官方问答也把"调整 VAD 阈值参数、减少误分割"列为
  提升说话人识别准确度的首选手段（与我们的结论一致）。
- **重叠语音（抢话）是公认难点**：所有开源方案（含 pyannote）在两人同时说话时都会
  出错，没有银弹。我们的缓解手段是 LLM 纠错层。

## 六、值得后续借鉴的项目

- [OddASR](https://juejin.cn/post/7671107024913399842)：基于 FunASR 的转写服务，
  把说话人分离做成一等公民配置（指定人数/上限/阈值/模式），文档细。
- [Aham Voice](https://blog.mushroom.cv/blog/aham-voice-meeting-recording-guide/)：
  Mac 本地"录音→分人→纪要"，全离线 + 纪要走自己的 LLM，数据安全立场与我们一致。
- FunASR 官方 diarization 模型族（SOND 中文 alimeeting、EEND-OLA）：
  若未来要换端到端 diarization 模型可从这里选，但需离线评估，当前组合已够用。

## 七、2026-09-07 实测对照：整段跑 vs 实时碎段

把 `OddASR`（FunASR `AutoModel(spk_model="cam++")` 离线整段）和阿里
`speech_campplus_speaker-diarization` pipeline 在 **gpu 服务器**上对同一批
真实双人录音做整段对照。核心结论：

1. **阿里 pipeline 传 `oracle=2`（确切人数）时完美分出 2 人**（45s 双人录音，
   `offline_ali_diarize.py oddasr_test_2spk.wav 2`，10 段正确交替 spk0/spk1）。
2. **阿里 pipeline 不传 oracle（自动估人数）时彻底失败**：4 条真实录音
   （66s / 255s / 270s / 61s，实际均为 2 人）自动模式下分别分出 **3 / 3 / 1 / 1** 人，
   0/4 全错。短音频自动估人数过于保守（45s 那次连 2 都没试出来）。
   → **这就是我们「前端必须填参会人数」的根本原因**：阿里 pipeline 的
   `oracle_num` 是单参数收益最大的一项。
3. **OddASR 的流式 WS 不做说话人分离**：`odd_asr_stream.py` 里 `spk_model="cam++"`
   被注释掉，实时路径只出文字；分人只在其**离线文件**模块
   （`odd_asr_file.py`，`AutoModel` + `sentence_info` 的 `spk` 字段）里做。
   → OddASR 的取舍与我们相反：**它放弃实时分人、专注离线整段分人**；
   我们则是「实时碎段分人弱 + 滚动重标注 + 会末重标注」补齐。

**对照脚本**（gpu 上跑，模型已缓存）：
- `scripts/offline_whole_segment.py <wav> [preset_spk_num]` —— FunASR AutoModel 整段
- `scripts/offline_ali_diarize.py <wav> [oracle_num]` —— 阿里 pipeline 整段

## 八、项目实测结论（为什么两个都"没用"）

| 项目 | 技术栈 | 与我们的差异 | 结论 |
|---|---|---|---|
| **OddASR** | FunASR + cam++，`preset_spk_num/max_num_spks/merge_thr/spk_mode` 四配置 | 离线整段分人准；流式 WS 不分人 | 分人思路与我们一致，但它**没有实时分人**，流式只出文字。其配置面板（指定人数/上限/阈值）与我们的配置面板思路重合，可互相印证 |
| **Aham Voice** | macOS 本地：FunASR 离线转写 + CAM++ 分人 + emotion2vec 情绪，全离线 | 同样是 FunASR+cam++ 离线路线，无实时、无 pyannote | 印证「FunASR+cam++ 离线整段分人」可行；但不解决我们**实时多人**需求，仓库不可达，无借鉴价值 |

**最终认知**：我们"实时分人弱"不是 bug，是**设计取舍**——官方/社区项目都靠
**离线整段**换取分人准确率，没有一个开源项目同时做好"实时出字 + 实时分人"。
我们的解法（滚动打标 + LLM 纠错 + 会末重标注）正是这些项目缺失的部分，
方向是对的，不需要换框架。

## 九、遗留问题

- gpu FunASR 服务目前跑在 **CPU**（`--ngpu 0`），重启时注意参数保持一致。
- `max_speakers` patch（`~/jvkit/asr/patch_max_speakers.py`，幂等）需重启 gpu
  FunASR 才生效；实时聚类层生效，阿里 pipeline 层用的是另一条参数（`oracle_num`）。
