"""全局配置：从项目根 .env / 环境变量加载。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → app/ → server/ → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # FunASR（gpu8）
    funasr_ws_url: str = "wss://192.168.30.87:10095"
    funasr_hotwords: str = ""

    # 会末重标注（gpu diarize 服务，v3 句子级声纹：/diarize_sent）
    diarize_http_url: str = "http://192.168.30.87:10096"  # ERes2NetV2 逐句声纹命名
    reannotate_min_sec: int = 10          # 短于此秒数不做重标注
    reannotate_name_threshold: float = 0.30  # 重标注声纹匹配阈值。注册人真实匹配分落在 0.44~0.61，0.50 会拒掉 0.47~0.49 的真匹配（被平滑继承成上一句说话人）；0.30 与 /diarize_sent 端点默认一致

    # LLM（内网 vLLM / mymodule 网关）
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    llm_api_key: str = ""
    llm_model: str = "chat"
    llm_timeout_sec: int = 120
    llm_correction_enabled: bool = True   # 实时文字纠错开关

    # 滚动增强（实时）：有效语音成窗 + 合并纠错/纪要
    roll_min_speech_sec: float = 8        # 窗口内有效语音（句时长累加）≥此秒数才成窗；不足并入下轮
    roll_max_sec: float = 15              # 墙钟超过此秒数强制成窗（防长静音期断流）
    roll_lookback_sec: float = 3          # 打标回看历史音频秒数（帮 pipeline 看全说话人特征）
    roll_llm_timeout_sec: float = 45      # 每轮 LLM 合并调用超时（网关抽风秒失败，窗口滚入下轮）
    roll_diarize_timeout_sec: float = 120 # gpu 打标超时

    # Langfuse
    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

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

    # VAD 激进切分（发给 gpu FunASR，按连接生效；改后新会议即生效，无需重启 gpu）
    vad_max_end_silence_ms: int = 350    # 静音多久判"说完"（原模型默认 800，正常换气约 300-600ms）
    vad_max_segment_sec: int = 8         # 单段最长秒数，超时强制切（原模型默认 60，长段必混多人）
    spk_min_seg_sec: float = 1.0         # 短于此秒数的段不做声纹（碎段 embedding 不稳）

    # 说话人聚类上限（发给 gpu FunASR；0=不限制，>0 则匿名聚类最多 N 人，超出并入最相近簇）
    spk_max_speakers: int = 0

    # 声纹匹配阈值（实时端注册声纹命中线，发给 gpu FunASR；gpu 启动参数 --speaker_match_threshold 的运行时覆盖）
    spk_match_threshold: float = 0.40


def load_hotwords() -> str:
    """读取热词文件 + 额外热词，合并为逗号分隔串（FunASR 协议格式）。

    文件每行一词；空行与 # 注释忽略。文件缺失时静默返回额外词。
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
    if settings.funasr_hotwords:  # 兼容旧配置
        for w in settings.funasr_hotwords.split(","):
            w = w.strip()
            if w and w not in words:
                words.append(w)
    return ",".join(words)


settings = Settings()
