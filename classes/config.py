"""
Модуль конфигурации для Telegram-Obsidian.
"""

class Config:
    """Класс для хранения настроек приложения."""

    def __init__(self,
                 api_id: int,
                 api_hash: str,
                 channel: str,
                 obsidian_path: str,
                 max_concurrent_downloads: int = 5,
                 batch_size: int = 10,
                 max_retries: int = 3,
                 retry_delay: int = 1,
                 rate_limit_pause: float = 0.5,
                 flood_wait_multiplier: float = 1.1,
                 max_video_size_mb: float = 50.0,
                 cache_ttl: int = 604800,  # 7 дней в секундах
                 skip_processed: bool = True,
                 optimize_images: bool = False,
                 optimize_videos: bool = False):  # Новый параметр
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel = channel
        self.obsidian_path = obsidian_path
        self.max_concurrent_downloads = max_concurrent_downloads
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limit_pause = rate_limit_pause
        self.flood_wait_multiplier = flood_wait_multiplier
        self.max_video_size_mb = max_video_size_mb
        self.cache_ttl = cache_ttl
        self.skip_processed = skip_processed
        self.optimize_images = optimize_images
        self.optimize_videos = optimize_videos  # Сохраняем новый параметр
