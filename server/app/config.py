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

    # 会末重标注（gpu diarize 服务）
    diarize_http_url: str = "http://192.168.30.87:10096"  # 阿里 pipeline 切分 + ERes2NetV2 声纹
    reannotate_oracle_extra: int = 1      # 人数估计 = 注册声纹数 + 此值（宁多勿少，按名合并自愈）
    reannotate_max_oracle: int = 6        # 人数上限
    reannotate_min_sec: int = 10          # 短于此秒数不做重标注

    # LLM（内网 vLLM / mymodule 网关）
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    llm_api_key: str = ""
    llm_model: str = "chat"
    llm_timeout_sec: int = 120
    llm_correction_enabled: bool = True   # 实时文字纠错开关

    # 滚动打标 + 滚动纠错（实时增强）
    roll_min_new_sec: float = 6           # 新音频累积达到此秒数，句终时触发一轮滚动打标+纠错
    roll_max_sec: float = 15              # 距上一轮超过此秒数，下个句终强制触发
    roll_min_window_sec: float = 3        # 当前窗口短于此秒数且未到上限时，攒着不触发
    roll_keep_windows: int = 2            # 纠错上下文保留的已纠窗口数（当前 + 前 N 个）

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

    # 总结调度
    summary_interval_sec: int = 60       # 定时总结间隔（秒）
    summary_min_utterances: int = 3      # 窗口内最少句子数，不足则跳过
    summary_max_chars: int = 1500        # 单次送入 LLM 的文本上限（字）
    attr_trigger_chars: int = 800        # 属性提取触发阈值（字）

    # 热词
    hotwords_file: str = "./hotwords.txt"  # 大词库文件（每行一词，空行/#注释忽略）
    hotwords_extra: str = ""               # 额外热词（逗号分隔），公司人名/项目名

    # VAD 激进切分（发给 gpu FunASR，按连接生效；改后新会议即生效，无需重启 gpu）
    vad_max_end_silence_ms: int = 350    # 静音多久判"说完"（原模型默认 800，正常换气约 300-600ms）
    vad_max_segment_sec: int = 8         # 单段最长秒数，超时强制切（原模型默认 60，长段必混多人）
    spk_min_seg_sec: float = 1.0         # 短于此秒数的段不做声纹（碎段 embedding 不稳）


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
