"""
Configuration module — Pydantic Settings loaded from environment and .env file.

All environment variables are validated at startup. Missing fields are loaded
from .env or assigned production defaults.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration parameters."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram Configuration ────────────────────────────────────────────────
    BOT_TOKEN: str = "YOUR_TELEGRAM_BOT_TOKEN"
    WEBHOOK_URL: str = "https://your-domain.com"
    WEBHOOK_SECRET: str = "your_random_32_character_secret_token"
    WEBHOOK_PATH: str = "/webhook"

    # ── External Model & Transcription APIs ───────────────────────────────────
    OPENROUTER_API_KEY: str = "sk-or-v1-your-openrouter-key"
    DEEPGRAM_API_KEY: str = "your_deepgram_api_key"

    # ── Chess Engine Configuration ────────────────────────────────────────────
    STOCKFISH_PATH: str = "stockfish"

    # ── SQLite Database ───────────────────────────────────────────────────────
    DATABASE_PATH: str = "bot_data.db"
    DATABASE_POOL_SIZE: int = 5

    # ── Webhook HTTP Server (Uvicorn / FastAPI) ───────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8443
    LOG_LEVEL: str = "INFO"


settings: Settings = Settings()
