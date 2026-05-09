"""Application configuration, loaded from environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: str = "openai_compatible"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # Leave empty to use the provider's default endpoint.
    # openai_compatible: required (e.g. https://api.deepseek.com/v1)
    # anthropic:         optional (https://api.deepseek.com/anthropic for DeepSeek; empty = real Claude)
    llm_base_url: str = ""

    # Blender
    blender_host: str = "127.0.0.1"
    blender_port: int = 9876

    # App
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_debug: bool = False


# Module-level singleton — import this everywhere
settings = Settings()
