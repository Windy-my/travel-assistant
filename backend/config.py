"""全局配置。

优先级：环境变量（.env 文件）> 内置默认值。
用户在前端填写的 Key 仅随单次 /api/generate 请求传递，不写入这里、不落盘。
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Travel Assistant API"
    app_version: str = "0.1.0"

    # ---------- 可选数据服务 Key ----------
    amap_key: str = ""  # 高德地图 Web 服务 Key
    openweathermap_key: str = ""  # OpenWeatherMap Key（备选天气源）

    # ---------- 可选默认 LLM（兜底，前端请求未携带 Key 时使用）----------
    default_llm_provider: str = "openai"  # openai | anthropic | azure_openai | zhipu | qianfan
    default_llm_api_key: str = ""
    default_llm_base_url: str | None = None
    default_llm_model: str | None = None

    # ---------- 服务 ----------
    cache_ttl_seconds: int = 3600
    cors_origins: str = "*"  # 逗号分隔

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
