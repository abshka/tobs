"""
Модуль конфигурации для Telegram-Obsidian.
"""

import os
from typing import Optional
from dotenv import load_dotenv  # Импортируем функцию для загрузки .env

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
                 optimize_videos: bool = False):
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
        self.optimize_videos = optimize_videos

    @classmethod
    def from_env(cls) -> 'Config':
        """
        Загружает конфигурацию из переменных окружения.
        Сначала пытается загрузить переменные из файла .env, если он существует.
        """
        # Загружаем переменные из .env файла в окружение процесса
        # Это нужно сделать ДО того, как начнется чтение переменных через os.getenv
        load_dotenv()

        # --- Helper functions rewritten ---
        def _get_env_var_or_raise(name: str) -> str:
            """Gets a required env var or raises ValueError."""
            value = os.getenv(name)
            if value is None:
                # Improved error message as requested by the context
                raise ValueError(f"Отсутствует обязательная переменная окружения: {name}. Пожалуйста, установите эту переменную (в системе или .env файле).")
            return value

        def _get_env_var_as_int(name: str, default: Optional[int] = None, required: bool = True) -> int:
            """Gets an env var as int, uses default if optional and not set, raises if required and not set."""
            value_str = os.getenv(name)
            if value_str is None: # Env var not set
                if required:
                    raise ValueError(f"Отсутствует обязательная переменная окружения: {name}. Пожалуйста, установите эту переменную (в системе или .env файле).")
                elif default is not None:
                    return default
                else:
                    # This case means required=False, but no default was provided.
                    raise ValueError(f"Не удалось получить значение для необязательной переменной {name}, значение по умолчанию не указано.")
            try:
                return int(value_str)
            except ValueError:
                raise ValueError(f"Неверное значение для переменной окружения {name}: '{value_str}'. Ожидается целое число.")

        def _get_env_var_as_float(name: str, default: Optional[float] = None, required: bool = True) -> float:
            """Gets an env var as float, uses default if optional and not set, raises if required and not set."""
            value_str = os.getenv(name)
            if value_str is None: # Env var not set
                if required:
                    raise ValueError(f"Отсутствует обязательная переменная окружения: {name}. Пожалуйста, установите эту переменную (в системе или .env файле).")
                elif default is not None:
                    return default
                else:
                    raise ValueError(f"Не удалось получить значение для необязательной переменной {name}, значение по умолчанию не указано.")
            try:
                return float(value_str)
            except ValueError:
                raise ValueError(f"Неверное значение для переменной окружения {name}: '{value_str}'. Ожидается число с плавающей точкой.")

        def _get_env_var_as_bool(name: str, default: bool = False) -> bool:
            """Gets an env var as bool, uses default if not set."""
            value_str = os.getenv(name)
            if value_str is None:
                return default
            return value_str.lower() in ('true', '1', 'yes', 'y')
        # --- End of rewritten helpers ---

        # Get required variables
        api_id = _get_env_var_as_int("API_ID", required=True)
        api_hash = _get_env_var_or_raise("API_HASH")
        channel = _get_env_var_or_raise("CHANNEL_ID")
        obsidian_path = _get_env_var_or_raise("OBSIDIAN_PATH")

        # Get optional variables using defaults from __init__
        max_concurrent_downloads = _get_env_var_as_int("MAX_CONCURRENT_DOWNLOADS", default=5, required=False)
        batch_size = _get_env_var_as_int("BATCH_SIZE", default=10, required=False)
        max_retries = _get_env_var_as_int("MAX_RETRIES", default=3, required=False)
        retry_delay = _get_env_var_as_int("RETRY_DELAY", default=1, required=False)
        rate_limit_pause = _get_env_var_as_float("RATE_LIMIT_PAUSE", default=0.5, required=False)
        flood_wait_multiplier = _get_env_var_as_float("FLOOD_WAIT_MULTIPLIER", default=1.1, required=False)
        max_video_size_mb = _get_env_var_as_float("MAX_VIDEO_SIZE_MB", default=50.0, required=False)
        cache_ttl = _get_env_var_as_int("CACHE_TTL", default=604800, required=False)
        skip_processed = _get_env_var_as_bool("SKIP_PROCESSED", default=True)
        optimize_images = _get_env_var_as_bool("OPTIMIZE_IMAGES", default=False)
        optimize_videos = _get_env_var_as_bool("OPTIMIZE_VIDEOS", default=False)

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            channel=channel,
            obsidian_path=obsidian_path,
            max_concurrent_downloads=max_concurrent_downloads,
            batch_size=batch_size,
            max_retries=max_retries,
            retry_delay=retry_delay,
            rate_limit_pause=rate_limit_pause,
            flood_wait_multiplier=flood_wait_multiplier,
            max_video_size_mb=max_video_size_mb,
            cache_ttl=cache_ttl,
            skip_processed=skip_processed,
            optimize_images=optimize_images,
            optimize_videos=optimize_videos,
        )
