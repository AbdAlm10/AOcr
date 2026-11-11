from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from waraq.quality import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    INFERENCE_MAX_PIXELS,
    SAFE_DECODE_PIXELS,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("./data")
    chandra_engine: str = "auto"
    datalab_api_key: str = ""
    datalab_api_base: str = "https://www.datalab.to/api/v1"
    vllm_api_base: str = "http://127.0.0.1:8000/v1"
    vllm_model_name: str = "chandra"
    vllm_api_key: str = "EMPTY"
    model_checkpoint: str = "datalab-to/chandra-ocr-2"
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    chunk_size: int = 8 * 1024 * 1024
    max_pages: int = 5000
    access_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8791
    web_port: int = 8787
    advertised_hosts: str = ""
    torch_device: str = ""
    mock_page_delay: float = 0.0
    datalab_poll_timeout_s: int = 3 * 60 * 60
    allow_cpu: bool = False
    inference_max_pixels: int = INFERENCE_MAX_PIXELS
    safe_decode_pixels: int = SAFE_DECODE_PIXELS

    def ensure_dirs(self) -> None:
        self.data_dir = Path(self.data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        (self.data_dir / "jobs").mkdir(exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
