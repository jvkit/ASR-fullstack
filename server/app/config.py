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

    # LLM（内网 Qwen3.5 vLLM）
    llm_base_url: str = "http://192.168.236.13:47501/v1"
    llm_api_key: str = ""
    llm_model: str = "Qwen-Qwen3.5-35B-A3B-GPTQ-Int4-tool-image-4080s"
    llm_timeout_sec: int = 120

    # Langfuse
    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # 服务
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./meeting.db"

    # 总结调度
    summary_interval_sec: int = 60       # 定时总结间隔（秒）
    summary_min_utterances: int = 3      # 窗口内最少句子数，不足则跳过
    summary_max_chars: int = 1500        # 单次送入 LLM 的文本上限（字）
    attr_trigger_chars: int = 800        # 属性提取触发阈值（字）


settings = Settings()
