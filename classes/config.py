"""
Модуль с классом конфигурации.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    """Конфигурация скрипта."""

    api_id: str
    api_hash: str
    channel: int
    obsidian_path: str
    max_video_size_mb: int = 50
    max_concurrent_downloads: int = 5
    cache_ttl: int = 86400
    skip_processed: bool = True
    batch_size: int = 50
    max_retries: int = 3
    retry_delay: int = 5
    rate_limit_pause: float = 0.5  # Пауза между загрузками в секундах
    flood_wait_multiplier: float = 1.5  # Множитель времени ожидания при flood wait

    @classmethod
    def from_env(cls) -> "Config":
        """Загрузка конфигурации из переменных окружения."""
        load_dotenv()
        channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
        if channel_id and channel_id.startswith("-100"):
            channel = int(channel_id)
        else:
            channel = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
        return cls(
            api_id=os.getenv("TELEGRAM_API_ID", ""),
            api_hash=os.getenv("TELEGRAM_API_HASH", ""),
            channel=channel,
            obsidian_path=os.getenv("OBSIDIAN_PATH", ""),
            max_video_size_mb=int(os.getenv("MAX_VIDEO_SIZE_MB", "50")),
            max_concurrent_downloads=int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "5")),
            cache_ttl=int(os.getenv("CACHE_TTL", "86400")),
            skip_processed=os.getenv("SKIP_PROCESSED", "True").lower() == "true",
            batch_size=int(os.getenv("BATCH_SIZE", "50")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_delay=int(os.getenv("RETRY_DELAY", "5")),
        )
