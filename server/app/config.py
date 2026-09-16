"""全局配置：从项目根 .env / 环境变量加载。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → app/ → server/ → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # GPU 非实时一体化管线 + 声纹库管理（VAD 切句 + SeACo Paraformer 转写 +
    # ERes2NetV2 认人；声纹注册/列表/删除/检测全部走这里，唯一权威库）
    diarize_http_url: str = "http://192.168.30.87:10096"
    pipeline_tick_sec: float = 4.0          # 批次调度检查间隔
    pipeline_batch_min_sec: float = 8.0     # 攒够这么多音频才尝试成批（VAD 句尾切点）
    pipeline_batch_max_sec: float = 15.0    # 超过强制硬切（防长句/长静音拖死批次）
    pipeline_chunk_timeout_sec: float = 120  # /meeting_chunk 超时（实测 30s 音频约 17s）
    pipeline_vad_timeout_sec: float = 30    # 批次切点 VAD 超时
    pipeline_vad_max_end_silence_ms: int = 250  # 与 GPU /vad 默认一致
    # 匿名聚类簇内切簇落差（split_gap）：同人段间相似度自然分布宽度 0.15+，
    # 注册场景的 0.12 会把聚好的簇拆成单段簇（盲测 6 真人出 30 陌生人）。
    # 0.35 起激进调参验证中（2026-09-16）
    anon_split_gap: float = 0.20
    # 匿名聚类入簇地板（anon_threshold）：段对簇质心相似度 ≥ 此值才入簇。
    # 同人段间 p10=0.66、跨人 max=0.58（女女），0.45 留有 0.07 余量；
    # 降到 0.40 收碎段（激进调参验证中，2026-09-16）
    anon_cluster_threshold: float = 0.35
    # 匿名簇两次确认制：候选攒满 confirm_count 段才升级发号（防 0.5s 语气词自立簇心；
    # 2026-09-16 设计，日志 [anon] 可审计每个段的去向）
    anon_confirm_count: int = 2
    # 会末尾音归属：孤段先对成熟簇重比（>=anon_cluster_threshold 吸收），
    # 仍孤则对时间前后相邻两簇比相似度，贴像的一边；两边都 < tail_min_sim 不贴。
    # 只处理孤段候选，只作用于会末终版
    anon_tail_attach: bool = True
    anon_tail_min_sim: float = 0.25
    # 显示层：簇总时长 < 此值不显示陌生人编号，统一"未识别短语音"（身份数据不受影响）
    anon_display_min_sec: float = 3.0

    # 说话人认人阈值（会议中批次 + 会末重标注 + 手动重标注共用此一个旋钮；
    # 注册人真实匹配分落在 0.44~0.61。2026-09-15 由 0.30 收紧到 0.40：
    # 实测同事说话误匹配 0.389、咳嗽误匹配 0.5+（均贴到冯忠俊——其合并质心
    # 混有远场弱质录音，过于普适，重录后有望再收）。DER 验证（AISHELL M会
    # 5min，replay_der 别名合并口径）：0.30→16.8% / 0.40→17.8%，混淆 4s→7s，
    # miss 不变 43s——收紧的代价在噪声内）
    reannotate_name_threshold: float = 0.40
    reannotate_min_sec: int = 10          # 短于此秒数不做重标注

    # LLM（内网 vLLM / mymodule 网关）
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    llm_api_key: str = ""
    llm_model: str = "chat"
    llm_timeout_sec: int = 120
    llm_correction_enabled: bool = True   # 批次文字纠错开关

    # 批次 LLM 增强（每批完成后触发：修正上轮 + 纠错本轮 + 滚动纪要）
    roll_llm_timeout_sec: float = 45      # 每批 LLM 合并调用超时（网关抽风秒失败，批次滚入下批补纠）

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
    hotwords_file: str = "./hotwords.txt"  # 大词库文件（每行一词，空行/#注释忽略）
    hotwords_extra: str = ""               # 额外热词（逗号分隔），公司人名/项目名


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
