"""
Central configuration — loaded once at startup.
All secrets come from environment variables / .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(..., min_length=20)
    admin_telegram_id: int = Field(...)
    telegram_channel_id: int = Field(...)
    telegram_webhook_secret: str = Field(default="", min_length=0)

    # Editors — may review/edit/approve posts, but NOT manage bloggers/config.
    # Comma-separated Telegram user IDs.
    editor_telegram_ids: str = Field(default="")

    # ── Claude AI ─────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., min_length=10)

    # ── YouTube ──────────────────────────────────────────────────────────────
    youtube_api_key: str = Field(..., min_length=10)

    # ── Threads ──────────────────────────────────────────────────────────────
    threads_user_id: str = Field(default="")
    threads_access_token: str = Field(default="")

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./content_bot.db"

    # ── Security ─────────────────────────────────────────────────────────────
    secret_key: str = Field(..., min_length=32)
    rate_limit_per_minute: int = 20

    # ── Sentry ───────────────────────────────────────────────────────────────
    sentry_dsn: str = ""

    # ── Scheduler ────────────────────────────────────────────────────────────
    scrape_interval_hours: int = 12
    max_posts_per_run: int = 5
    min_views_threshold: int = 30_000      # lower: fresh uploads have fewer views
    post_interval_hours: int = 24
    # Only consider videos published within this window (days). Combined with
    # order="date" this is what keeps the bot finding NEW content instead of
    # re-scanning the same all-time-top videos forever.
    scrape_recent_days: int = 30
    # Skip a candidate if a post with the same topic was created in the last
    # N days (cross-blogger topic de-duplication).
    topic_dedup_days: int = 45
    # Minimum source content (transcript + description) in characters. Videos
    # below this are ads/shorts with nothing to rewrite — skip them so the
    # model never writes a post "from thin air".
    min_source_chars: int = 220

    # ── App ──────────────────────────────────────────────────────────────────
    bot_mode: Literal["polling", "production"] = "polling"
    webhook_url: str = ""

    # ── Bloggers (default list, editable via bot commands) ───────────────────
    # Format: "channel_id:display_name" comma-separated
    default_bloggers: str = (
        "UCF5TJYJHoEL9LVGSHiDDBlg:Humphrey Yang,"
        "UCJEnQMBLz3EJGl9XRxiCPrw:Vivian Tu (Your Rich BFF)"
    )

    @field_validator("telegram_channel_id", mode="before")
    @classmethod
    def coerce_channel_id(cls, v):
        return int(v)

    @property
    def editor_ids(self) -> set[int]:
        """Parse editor_telegram_ids into a set of ints."""
        ids: set[int] = set()
        for part in self.editor_telegram_ids.split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                ids.add(int(part))
        return ids

    @property
    def authorized_ids(self) -> set[int]:
        """Everyone allowed to interact with the bot (admin + editors)."""
        return {self.admin_telegram_id} | self.editor_ids

    @property
    def blogger_list(self) -> list[dict]:
        """Parse default_bloggers string into structured list."""
        result = []
        for item in self.default_bloggers.split(","):
            item = item.strip()
            if ":" in item:
                channel_id, name = item.split(":", 1)
                result.append({"channel_id": channel_id.strip(), "name": name.strip()})
        return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
