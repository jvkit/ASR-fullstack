"""全局配置：从项目根 .env / 环境变量加载。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → app/ → server/ → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # GPU 非实时一体化管线（VAD 切句 + SeACo Paraformer 转写 + ERes2NetV2 认人）
    diarize_http_url: str = "http://192.168.30.87:10096"
    # 声纹注册管理（enroll/list/delete）仍走 GPU FunASR websocket 服务的控制通道；
    # 转写已不走它（非实时管线全走 diarize_http_url）
    funasr_ws_url: str = "wss://192.168.30.87:10095"
    pipeline_tick_sec: float = 4.0          # 批次调度检查间隔
    pipeline_batch_min_sec: float = 8.0     # 攒够这么多音频才尝试成批（VAD 句尾切点）
    pipeline_batch_max_sec: float = 15.0    # 超过强制硬切（防长句/长静音拖死批次）
    pipeline_chunk_timeout_sec: float = 120  # /meeting_chunk 超时（实测 30s 音频约 17s）
    pipeline_vad_timeout_sec: float = 30    # 批次切点 VAD 超时
    pipeline_vad_max_end_silence_ms: int = 250  # 与 GPU /vad 默认一致

    # 说话人认人阈值（会议中批次 + 会末重标注 + 手动重标注共用此一个旋钮；
    # 注册人真实匹配分落在 0.44~0.61，0.30 与 /diarize_sent 端点默认一致）
    reannotate_name_threshold: float = 0.30
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
