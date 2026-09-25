"""Server configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8888
    data_dir: str = "data"
    seed_file: str = "seed.json"

    model_config = {"env_prefix": "MOCK_"}


settings = Settings()
