"""Application configuration loaded from environment / .env (PROJECT.md §10 step 1).

Uses pydantic-settings v2. The .env file is resolved relative to this module so the
bot loads correctly regardless of the current working directory (important when
running under systemd, screen, or tmux).
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (this file lives at the repo root).
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Runtime settings.

    Attributes:
        bot_token: Telegram bot token issued by @BotFather. Required — no default.
        db_path: Path to the SQLite database file (relative or absolute).
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    db_path: str = "./bot.db"
    log_level: str = "INFO"
    web_port: int = 5000


# Module-level singleton consumed by the rest of the app.
settings = Settings()
