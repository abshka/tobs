import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from src.utils import logger

@dataclass
class Config:
    # Telegram
    api_id: int
    api_hash: str
    phone_number: str | None = None # Optional if session exists or using bot token
    session_name: str = "telegram_obsidian_session"
    telegram_channel: str | int = ""

    # Obsidian
    obsidian_path: Path = Path("./obsidian_export")
    media_subdir: str = "media" # Subdirectory within obsidian_path for media

    # Processing
    only_new: bool = False
    media_download: bool = True
    verbose: bool = True
    concurrent_downloads: int = 5

    # API Limits
    request_delay: float = 1.0
    message_batch_size: int = 100

    # Media Optimization
    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "medium"

    # Caching
    cache_file: Path = Path("./telegram_obsidian_cache.json")

    # Derived paths
    media_base_path: Path = field(init=False)

    def __post_init__(self):
        """Validate and derive paths after initialization."""
        if not self.api_id or not self.api_hash:
            raise ValueError("API_ID and API_HASH must be set in .env")
        if not self.telegram_channel:
            raise ValueError("TELEGRAM_CHANNEL must be set in .env")

        self.obsidian_path = Path(self.obsidian_path).resolve()
        self.cache_file = Path(self.cache_file).resolve()
        self.media_base_path = self.obsidian_path / self.media_subdir

        # Ensure base directories exist
        self.obsidian_path.mkdir(parents=True, exist_ok=True)
        self.media_base_path.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Configuration loaded: {self}")


def load_config(env_path: str = ".env") -> Config:
    """Loads configuration from .env file."""
    load_dotenv(dotenv_path=env_path)
    logger.info(f"Loading configuration from {env_path}")

    try:
        config_dict = {
            "api_id": int(os.getenv("API_ID", 0)),
            "api_hash": os.getenv("API_HASH", ""),
            "phone_number": os.getenv("PHONE_NUMBER"),
            "session_name": os.getenv("SESSION_NAME", "telegram_obsidian_session"),
            "telegram_channel": os.getenv("TELEGRAM_CHANNEL", ""),
            "obsidian_path": Path(os.getenv("OBSIDIAN_PATH", "./obsidian_export")),
            "only_new": os.getenv("ONLY_NEW", "false").lower() == "true",
            "media_download": os.getenv("MEDIA_DOWNLOAD", "true").lower() == "true",
            "verbose": os.getenv("VERBOSE", "true").lower() == "true",
            "concurrent_downloads": int(os.getenv("CONCURRENT_DOWNLOADS", 5)),
            "request_delay": float(os.getenv("REQUEST_DELAY", 1.0)),
            "message_batch_size": int(os.getenv("MESSAGE_BATCH_SIZE", 100)),
            "image_quality": int(os.getenv("IMAGE_QUALITY", 85)),
            "video_crf": int(os.getenv("VIDEO_CRF", 28)),
            "video_preset": os.getenv("VIDEO_PRESET", "medium"),
            "cache_file": Path(os.getenv("CACHE_FILE", "./telegram_obsidian_cache.json")),
        }
        # Convert channel ID if it's numeric
        if config_dict["telegram_channel"].lstrip('-').isdigit():
             config_dict["telegram_channel"] = int(config_dict["telegram_channel"])

        return Config(**config_dict)
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        raise
