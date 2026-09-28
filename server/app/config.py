"""全局配置：从项目根 .env / 环境变量加载。

双配置（2026-09-28 用户拍板）：
- `.env`        本地开发用（gitignored，不入库）
- `.env.server` 服务器部署用（入库，内网地址/端口无密钥，pull 下来即能用）
切换：环境变量 APP_ENV=server|local（缺省 local）。两边键名完全同构，差异只在值，
防"本地能跑服务器跑不了、改了服务器本地跑不了"。
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → app/ → server/ → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ENV = os.environ.get("APP_ENV", "local")
ENV_FILE = PROJECT_ROOT / (".env.server" if APP_ENV == "server" else ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # GPU 非实时一体化管线 + 声纹库管理（VAD 切句 + SeACo Paraformer 转写 +
    # ERes2NetV2 认人；声纹注册/列表/删除/检测全部走这里，唯一权威库）
    # ⚠️ GPU 机器 IP 会变（2026-09-22 由 192.168.30.87 迁到 172.16.16.120，
    # hostname gpu8）。改 IP 优先动 .env 的 DIARIZE_HTTP_URL，不用改代码。
    diarize_http_url: str = "http://172.16.16.120:10096"
    pipeline_tick_sec: float = 4.0          # 批次调度检查间隔
    # L1 批次（ASR + 说话人标签，走 GPU 不碰 LLM 网关）：30s 下限 / 45s 硬切。
    # 2026-09-21 由 8/15 放大：转写准确度与批次大小解耦（GPU 内部 FSMN VAD 按
    # max_end_silence_ms 切句 + SeACo 逐子段离线转写，30s 与 8s 看到的子段相同）；
    # 放大只影响会中匿名聚类质量（批内段数更多更稳，但会末 3D-Speaker 全场重跑
    # 会覆盖，会中标签本就是草稿）。代价：GPU duty cycle 57%（实测 30s 音频约
    # 17s），容错余量小于 8s 批次，跟不上就退回 45/60。
    pipeline_batch_min_sec: float = 30.0    # 攒够这么多音频才尝试成批（VAD 句尾切点）
    pipeline_batch_max_sec: float = 45.0    # 超过强制硬切（防长句/长静音拖死批次）
    pipeline_chunk_timeout_sec: float = 120  # /meeting_chunk 超时（实测 30s 音频约 17s）
    pipeline_vad_timeout_sec: float = 30    # 批次切点 VAD 超时
    pipeline_vad_max_end_silence_ms: int = 250  # VAD 句尾静音判停阈值。
    # 250 vs 800 的取舍（2026-09-18 定档 250）：800 会把开场自我介绍这类
    # 人间停顿仅 ~300ms 的轮流发言揉成多人混合段 → embedding 多人平均 →
    # 整段只能贴一个标签（DER 48.8% vs 250 的 12.3%，bb3cd75f14e8 实测证伪 800）。
    # 250 保归属原子；碎段展示问题由 display_merge_gap_sec 显示层合并解决，
    # 不在 VAD 层妥协。旧 .env 键 VAD_MAX_END_SILENCE_MS 是
    # 死键（无对应字段，从未生效），已改名 PIPELINE_VAD_MAX_END_SILENCE_MS。
    # 匿名聚类簇内切簇落差（split_gap）：同人段间相似度自然分布宽度 0.15+，
    # 注册场景的 0.12 会把聚好的簇拆成单段簇（盲测 6 真人出 30 陌生人）。
    # 0.35 起激进调参验证中（2026-09-16）
    # ---------- 匿名聚类（方案 B 前旧参数，2026-09-22 全部废弃保留文档） ----------
    # 以下参数已无代码引用（会中匿名聚类已杀，未命中统一"未识别"）；
    # 保留为死键待清理，注释保留标定历史供回滚参考。
    anon_split_gap: float = 0.20
    anon_cluster_threshold: float = 0.48
    anon_confirm_count: int = 2
    anon_tail_attach: bool = True
    anon_tail_min_sim: float = 0.25
    anon_display_min_sec: float = 3.0
    # 显示层合并：详情接口序列化时，同说话人且间隙 ≤ 此秒数的相邻段并成一行展示
    # （2026-09-18，配合 VAD 回 250 的碎段治理；只影响展示/阅读，不动 DB 原子段
    # 与聚类逻辑，LLM 纠错仍按原 seq 落各自的行，合并视图拼接 corrected 文本）
    display_merge_gap_sec: float = 2.0
    # ASR 后处理：CT-Transformer 标点模型 + WeTextProcessing ITN（百分之五十->50%）
    pipeline_punc_enabled: bool = True
    pipeline_itn_enabled: bool = True
    # SeACo 热词偏置强度 λ（2026-09-17 矩阵定档：5000词@λ0.1 质量零代价，λ1.0 满格会帮倒忙）
    pipeline_hw_weight: float = 0.1

    # 降噪预处理层（2026-09-17 降噪决赛当选：西工大 GTCRN，DNS3 权重）。
    # 集中处理：会末 3D-Speaker 分离、GPU 声纹注册/识别、会中批次统一走
    # preprocess.denoise_pcm；原始 PCM 存档不受影响，关闭即回到未降噪链路。
    denoise_enabled: bool = True
    denoise_model_path: str = "models/gtcrn_dns3.tar"  # 相对 server/ 目录

    # 说话人认人阈值（会议中批次 + 会末重标注 + 手动重标注共用此一个旋钮；
    # 注册人真实匹配分落在 0.44~0.61。2026-09-15 由 0.30 收紧到 0.40：
    # 实测同事说话误匹配 0.389、咳嗽误匹配 0.5+（均贴到冯忠俊——其合并质心
    # 混有远场弱质录音，过于普适，重录后有望再收）。DER 验证（AISHELL M会
    # 5min，replay_der 别名合并口径）：0.30→16.8% / 0.40→17.8%，混淆 4s→7s，
    # miss 不变 43s——收紧的代价在噪声内）
    reannotate_name_threshold: float = 0.40
    reannotate_min_sec: int = 10          # 短于此秒数不做重标注

    # ---------- 会末说话人分离：3D-Speaker（换芯一期 B，2026-09-20） ----------
    # 唯一生产会末分离路径：会末完整录音 → 本地 GTCRN → diarize3ds.diarize 出匿名分段
    # → GPU /identify_segments 逐段 ERes2NetV2 认人 + SeACo 转写。
    # 旧自研 VAD+匿名聚类（/meeting_chunk）不再作为会末生产路径，代码保留用于会中批次
    # 与回滚（见 AGENTS.md §5）。此处参数对应 3D-Speaker CommonClustering，默认值与
    # scd_bench 评测/等价性验收（rotate_m30 7.66%、M5min 4.77%）完全一致。
    diar3ds_cluster_type: str = "spectral"
    diar3ds_mer_cos: float = 0.8          # 簇心 cos≥此值再合并
    diar3ds_min_num_spks: int = 1
    diar3ds_max_num_spks: int = 15        # 谱聚类选 k 上限（>15 人场景不可用）
    diar3ds_min_cluster_size: int = 4     # ≤此段数的簇重指派到最近大簇
    diar3ds_cluster_line: int = 40        # 子段数 < 此值走 AHC 短段分支（谱聚类参数不生效）
    diar3ds_pval: float = 0.012           # 相似度矩阵剪枝阈值
    diar3ds_oracle_num: int = 0           # 0=不指定人数（自动估 k）；>0 强制人数
    # /identify_segments 超时：长会议逐段 CPU ASR 分钟级，给足余量
    identify_segments_timeout_sec: float = 900

    # ---------- L2 波段纪要（2026-09-21 改：段纪要不再累计改写） ----------
    # 一波 = summary_every_n_batches 个 L1 批次（按批次计数驱动，不用墙上时钟，
    # 避免 VAD 切点浮动导致时间窗漂移）。每波一次 LLM 调用同时干三件事：
    # 纠错本波 + 订正上一波 + 出本波段纪要（标题/要点/待办）。
    # 旧设计（每批改写同一份 3-8 条滚动纪要）已废：40min 与 6h 会议输出同样
    # 一小段，长会议信息必然丢失。现在每波独立成段、append 不覆盖，会末大总结
    # 由全部段纪要归约而来（不碰原始全文，故窗口与时长解耦）。
    summary_every_n_batches: int = 4        # 每 4 个 L1 批次触发一波（30s×4 ≈ 2min）
    summary_context_segments: int = 5       # 每波带前 5 波纪要做上下文（滑动窗口，防漂移）
    # 会议结束时不足一波的尾巴强制跑一次（否则最后 1-2 分钟永不纠错、不进纪要）
    summary_flush_tail: bool = True

    # ---------- 会末版（final）波次纪要（双版本架构 2026-09-28） ----------
    # 会末版 = 会议结束后全录音重跑（reannotate）的 utterances_re 按固定时间窗分波，
    # 逐波走与实时版同一套波次 LLM（纠错写回 utterances_re + 段纪要 scope='final'）。
    # 用 120s 固定窗而非对齐 OCR/批次边界：固定窗简单可靠、VisualNote 按时间范围
    # 关联不需要边界对齐。
    final_wave_sec: float = 120.0

    # LLM（内网 vLLM / mymodule 网关）
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    llm_api_key: str = ""
    llm_model: str = "chat"          # 256k 上下文；网关另有 new(1M)，6h 会议
                                     # 会末输入约 94k token，chat 足够，不占 1M 模型
    llm_timeout_sec: int = 300
    llm_correction_enabled: bool = True   # 批次文字纠错开关

    # 波段 LLM 增强（每波一次调用：纠错本波 + 订正上一波 + 出本波段纪要）
    # 实测内网网关同请求延迟 19s~230s 波动（共享 vLLM 排队），45s 会导致
    # 会上纠错批量超时死亡（日志 CancelledError、原文永不纠正）——放宽到 300s，
    # 宁可纪要滞后也不丢纠错；波在飞期间新批次自动累积，不丢批。
    # 单次调用输入恒定 ≈4.2k token（5 波纪要 + 2 波原文），与会议时长无关。
    # 失败不兜底（2026-09-21 用户决定）：某波 LLM 失败即记日志跳过，该波无段纪要、
    # 文字留草稿态；下一波仍会拿到本波原文（2 波窗口），纠错自动延后一波补上。
    roll_llm_timeout_sec: float = 300

    # Langfuse
    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # 声纹智能注册（GPU /speaker_enroll；标定依据见 docs/声纹录入质量设计.md）
    enroll_min_total_sec: float = 5.0      # 录音总时长硬下限（更早拦截误触）
    enroll_min_seg_sec: float = 2.0        # 单段合格下限（短段是相似度拖尾主因）
    enroll_min_segments: int = 3           # 合格段数下限（1→3 段质心收益最大）
    enroll_min_effective_sec: float = 10.0 # 有效语音总时长下限（文献 30s 饱和，闭集认人 10s 起）
    enroll_max_keep: int = 5               # 多段质心保留段数（5 段后收益饱和）
    enroll_consistency: float = 0.50       # 段间一致性阈值（会话内标定：女女跨人中位 0.419）
    enroll_timeout_sec: float = 180.0      # 注册处理超时（VAD+多段 embedding）
    identify_match_threshold: float = 0.40 # 录入检测"确认命中"线（>=此值且领先即匹配本人）

    # 服务
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./meeting.db"
    recordings_dir: str = "./recordings"    # 会议录音 wav 存放目录

    # 会末收尾（总结/标题/标签/属性在 _finalize 一次完成）
    final_tags_count: int = 5             # 会末生成的标签数量上限

    # 热词
    hotwords_file: str = "./hotwords/dict_daily5k.txt"  # 大词库文件（每行一词，空行/#注释忽略）
    hotwords_extra: str = ""               # 额外热词（逗号分隔），公司人名/项目名

    # ---------- 线上会议视觉通道（2026-09-22 视觉侧交接，代码地图见
    # docs/achit/视频侧接口与代码地图-20260922.md） ----------
    visual_enabled: bool = True
    visual_frame_interval_sec: float = 10.0   # 前端抽帧间隔（10s 甜点：写字/遮挡均覆盖）
    visual_frame_width: int = 960             # 前端缩略宽度（960 实测小字全可读；白板读不清再升 1280）
    visual_frame_quality: int = 80            # JPEG 质量
    visual_llm_model: str = "new"             # 多模态池（chat 池对图片请求报错，唯一可用 new）
    visual_llm_timeout_sec: int = 300         # 多帧读图分钟级，给足余量
    visual_prompt_app: str = "online"         # online | offline（提示词模板选择）
    visual_max_buffer_frames: int = 24        # 帧缓冲上限（≈4min 积压容量，防雪球）
    # final-only 模式的视觉 OCR 节拍（2026-09-28）：final 模式没有会中波次，OCR 缺节拍器，
    # 由 session 每 120s 定时 take_group → OCR → 落 VisualNote（会末版按时间复用）
    visual_note_interval_sec: float = 120.0


def load_hotwords() -> str:
    """读取热词文件 + 额外热词，合并为逗号分隔串。

    文件每行一词；空行与 # 注释忽略。文件缺失时静默返回额外词。
    （逗号格式为服务端存储格式；发给 GPU /meeting_chunk 时由调用方转空格分隔，
    FunASR SeACo 按空白切分热词。）
    """
    words: list[str] = []
    path = Path(settings.hotwords_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            w = line.strip()
            if w and not w.startswith("#"):
                words.append(w)
    except FileNotFoundError:
        pass
    for w in settings.hotwords_extra.split(","):
        w = w.strip()
        if w and w not in words:
            words.append(w)
    return ",".join(words)


settings = Settings()
