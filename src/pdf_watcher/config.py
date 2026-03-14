from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    watch_dir: Path
    output_dir: Path
    rejected_dir: Path
    log_file: Path = Path("logs/app.log")
    log_level: str = "INFO"
    max_workers: int = 4
    use_process_pool_for_zstd: bool = False
    max_process_workers: int = 2
    file_stable_interval: float = 0.5
    file_stable_retries: int = 10
    queue_max_size: int = 100

    @field_validator("watch_dir", "output_dir", "rejected_dir", mode="before")
    @classmethod
    def create_dir(cls, v: str | Path) -> Path:
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @field_validator("log_file", mode="before")
    @classmethod
    def create_log_dir(cls, v: str | Path) -> Path:
        path = Path(v)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
