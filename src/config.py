import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv

from src.exceptions import ConfigError
from src.utils import logger, sanitize_filename

DEFAULT_CACHE_PATH = Path("./tobs_cache.json")
DEFAULT_EXPORT_PATH = Path("./debug_exports")

@dataclass
class ExportTarget:
    """
    Represents a single export target (channel, chat, or user).

    Args:
        id (Union[str, int]): The unique identifier for the export target.
        name (str, optional): The name of the export target. Defaults to "".
        type (str, optional): The type of the export target. Defaults to "unknown".
        message_id (Optional[int], optional): ID of a single message to export (for single post export). Defaults to None.
    """
    id: Union[str, int]
    name: str = ""
    type: str = "unknown"
    message_id: Optional[int] = None

    def __post_init__(self):
        """
        Initialize the ExportTarget and determine its type based on the id.

        Args:
            None

        Returns:
            None
        """
        self.id = str(self.id).strip()
        if self.type == "single_post":
            return
        if self.id.startswith('@') or 't.me/' in self.id or self.id.startswith('-100'):
            self.type = "channel"
        elif self.id.startswith('-') and self.id[1:].isdigit():
            self.type = "chat"
        elif self.id.isdigit():
            self.type = "user"


@dataclass
class Config:
    """
    Main configuration class for the exporter.
    Loads and validates all settings from .env and environment variables.

    Args:
        api_id (int): Telegram API ID.
        api_hash (str): Telegram API hash.
        phone_number (Optional[str], optional): Telegram phone number. Defaults to None.
        session_name (str, optional): Session name. Defaults to "tobs_session".
        export_targets (List[ExportTarget], optional): List of export targets. Defaults to empty list.
        export_path (Path, optional): Path for exports. Defaults to DEFAULT_EXPORT_PATH.
        media_subdir (str, optional): Subdirectory for media. Defaults to "_media".
        use_entity_folders (bool, optional): Whether to use entity folders. Defaults to True.
        only_new (bool, optional): Export only new messages. Defaults to False.
        media_download (bool, optional): Whether to download media. Defaults to True.
        cache_manager (Any, optional): Cache manager. Defaults to None.
        log_level (str, optional): Logging level. Defaults to "INFO".
        workers (int, optional): Number of workers. Defaults to 8.
        cache_save_interval (int, optional): Cache save interval. Defaults to 50.
        request_delay (float, optional): Delay between requests. Defaults to 0.5.
        message_batch_size (int, optional): Batch size for messages. Defaults to 100.
        image_quality (int, optional): Image quality. Defaults to 85.
        video_crf (int, optional): Video CRF. Defaults to 28.
        video_preset (str, optional): Video preset. Defaults to "fast".
        hw_acceleration (str, optional): Hardware acceleration. Defaults to "none".
        use_h265 (bool, optional): Use H265 encoding. Defaults to False.
        cache_file (Path, optional): Path to cache file. Defaults to DEFAULT_CACHE_PATH.
        interactive_mode (bool, optional): Interactive mode. Defaults to False.
        dialog_fetch_limit (int, optional): Dialog fetch limit. Defaults to 20.
        proxy_type (Optional[str], optional): Proxy type. Defaults to None.
        proxy_addr (Optional[str], optional): Proxy address. Defaults to None.
        proxy_port (Optional[int], optional): Proxy port. Defaults to None.
        throttle_threshold_kbps (int, optional): Throttle threshold in kbps. Defaults to 50.
        throttle_pause_s (int, optional): Throttle pause in seconds. Defaults to 30.
        export_comments (bool, optional): Export comments mode. Defaults to False.

    """
    api_id: int
    api_hash: str
    phone_number: Optional[str] = None
    session_name: str = "tobs_session"
    export_targets: List[ExportTarget] = field(default_factory=list)
    export_path: Path = field(default=DEFAULT_EXPORT_PATH)
    media_subdir: str = "_media"
    use_entity_folders: bool = True
    only_new: bool = False
    media_download: bool = True
    cache_manager: Any = None
    log_level: str = "INFO"
    workers: int = 8
    batch_size: Optional[int] = None
    cache_save_interval: int = 50
    request_delay: float = 0.5
    message_batch_size: int = 100
    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "fast"
    hw_acceleration: str = "none"
    use_h265: bool = False
    cache_file: Path = field(default=DEFAULT_CACHE_PATH)
    interactive_mode: bool = False
    dialog_fetch_limit: int = 20
    proxy_type: Optional[str] = None
    proxy_addr: Optional[str] = None
    proxy_port: Optional[int] = None
    throttle_threshold_kbps: int = 50
    throttle_pause_s: int = 30
    export_comments: bool = False
    download_workers: Optional[int] = None  # Количество одновременных скачиваний (по умолчанию int(workers*1.5))
    export_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    media_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    cache: Dict[str, Any] = field(default_factory=dict, init=False)

    def to_dict(self) -> dict:
        """
        Возвращает словарь только с сериализуемыми (основными) полями.
        Исключает служебные и временные атрибуты.
        """
        allowed = {f.name for f in fields(self) if f.init}
        result = {}
        for k, v in asdict(self).items():
            if k in allowed:
                # Специальная обработка для export_targets
                if k == "export_targets":
                    result[k] = [
                        asdict(t) if hasattr(t, "__dataclass_fields__") else t
                        for t in v
                    ]
                # Path сериализуем как строку
                elif isinstance(v, Path):
                    result[k] = str(v)
                else:
                    result[k] = v
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """
        Создаёт объект Config из словаря, игнорируя лишние поля.
        """
        allowed = {f.name for f in fields(cls) if f.init}
        filtered = {}
        for k, v in d.items():
            if k in allowed:
                # Восстановление export_targets
                if k == "export_targets":
                    filtered[k] = [ExportTarget(**t) if not isinstance(t, ExportTarget) else t for t in v]
                # Восстановление Path
                elif k in ("export_path", "cache_file") and not isinstance(v, Path):
                    filtered[k] = Path(v)
                else:
                    filtered[k] = v
        return cls(**filtered)

    @classmethod
    def from_env(cls, env_path: Union[str, Path] = ".env") -> "Config":
        """
        Загружает конфиг из .env и переменных окружения.
        """
        if Path(env_path).exists():
            load_dotenv(dotenv_path=env_path)
        try:
            proxy_port_str = os.getenv("PROXY_PORT")
            proxy_port = int(proxy_port_str) if proxy_port_str and proxy_port_str.isdigit() else None

            config_dict = {
                "api_id": int(os.getenv("API_ID", 0)),
                "api_hash": os.getenv("API_HASH", ""),
                "phone_number": os.getenv("PHONE_NUMBER"),
                "session_name": os.getenv("SESSION_NAME", "tobs_session"),
                "export_path": Path(os.getenv("EXPORT_PATH", DEFAULT_EXPORT_PATH)),
                "media_subdir": os.getenv("MEDIA_SUBDIR", "_media"),
                "use_entity_folders": _parse_bool(os.getenv("USE_ENTITY_FOLDERS"), True),
                "only_new": _parse_bool(os.getenv("ONLY_NEW"), False),
                "media_download": _parse_bool(os.getenv("MEDIA_DOWNLOAD"), True),
                "log_level": os.getenv("LOG_LEVEL", "INFO"),
                "workers": int(os.getenv("WORKERS", 8)),
                "download_workers": int(os.getenv("DOWNLOAD_WORKERS")) if os.getenv("DOWNLOAD_WORKERS") else int(int(os.getenv("WORKERS", 8)) * 1.5),
                "batch_size": int(os.getenv("BATCH_SIZE")) if os.getenv("BATCH_SIZE") else None,
                "cache_save_interval": int(os.getenv("CACHE_SAVE_INTERVAL", 50)),
                "request_delay": float(os.getenv("REQUEST_DELAY", 0.5)),
                "message_batch_size": int(os.getenv("MESSAGE_BATCH_SIZE", 100)),
                "image_quality": int(os.getenv("IMAGE_QUALITY", 85)),
                "video_crf": int(os.getenv("VIDEO_CRF", 28)),
                "video_preset": os.getenv("VIDEO_PRESET", "fast"),
                "hw_acceleration": os.getenv("HW_ACCELERATION", "none"),
                "use_h265": _parse_bool(os.getenv("USE_H265"), False),
                "cache_file": Path(os.getenv("CACHE_FILE", DEFAULT_CACHE_PATH)),
                "interactive_mode": _parse_bool(os.getenv("INTERACTIVE_MODE"), False),
                "dialog_fetch_limit": int(os.getenv("DIALOG_FETCH_LIMIT", 20)),
                "proxy_type": os.getenv("PROXY_TYPE"),
                "proxy_addr": os.getenv("PROXY_ADDR"),
                "proxy_port": proxy_port,
                "throttle_threshold_kbps": int(os.getenv("THROTTLE_THRESHOLD_KBPS", 50)),
                "throttle_pause_s": int(os.getenv("THROTTLE_PAUSE_S", 30)),
                "export_comments": _parse_bool(os.getenv("EXPORT_COMMENTS"), False),
            }

            export_targets = []
            targets_str = os.getenv("EXPORT_TARGETS", "")
            if targets_str:
                for target_id in [t.strip() for t in targets_str.split(',') if t.strip()]:
                    export_targets.append(ExportTarget(id=target_id))

            config_dict["export_targets"] = export_targets
            return cls(**config_dict)

        except (ValueError, TypeError) as e:
            raise ConfigError(f"Invalid configuration value: {e}") from e

    def __post_init__(self):
        """
        Initialize the Config object, validate required fields, and set up paths.

        Args:
            None

        Returns:
            None

        Raises:
            ConfigError: If required configuration is missing or paths cannot be created.
        """
        if not self.api_id or not self.api_hash:
            raise ConfigError("API_ID and API_HASH must be set in .env file")

        self.export_path = Path(self.export_path).resolve()
        self.cache_file = Path(self.cache_file).resolve()

        for path in [self.export_path, self.cache_file.parent]:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigError(f"Failed to create path {path}: {e}")

        self._update_target_paths()

        if not self.export_targets and not self.interactive_mode:
            logger.warning("No export targets defined and interactive mode is off. Nothing to do.")

    def _update_target_paths(self):
        """
        Update export and media paths for each export target.

        Args:
            None

        Returns:
            None
        """
        self.export_paths = {}
        self.media_paths = {}
        for target in self.export_targets:
            target_id = str(target.id)
            target_name = self._get_entity_folder_name(target)

            base_path = self.export_path / target_name if self.use_entity_folders else self.export_path
            media_path = base_path / self.media_subdir

            self.export_paths[target_id] = base_path.resolve()
            self.media_paths[target_id] = media_path.resolve()

    def _get_entity_folder_name(self, target: ExportTarget) -> str:
        """
        Generate a sanitized folder name for the export target.

        Args:
            target (ExportTarget): The export target.

        Returns:
            str: Sanitized folder name.
        """
        name = target.name or f"id_{target.id}"
        clean_name = sanitize_filename(name, max_length=100)
        return clean_name

    def add_export_target(self, target: ExportTarget):
        """
        Add a new export target if it does not already exist.

        Args:
            target (ExportTarget): The export target to add.

        Returns:
            None
        """
        if str(target.id) not in [str(t.id) for t in self.export_targets]:
            self.export_targets.append(target)
            self._update_target_paths()

    def get_export_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """
        Get the export path for a given entity.

        Args:
            entity_id (Union[str, int]): The entity ID.

        Returns:
            Path: The export path for the entity.
        """
        return self.export_paths.get(str(entity_id), self.export_path)

    def get_media_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """
        Get the media path for a given entity.

        Args:
            entity_id (Union[str, int]): The entity ID.

        Returns:
            Path: The media path for the entity.
        """
        return self.media_paths.get(str(entity_id), self.export_path / self.media_subdir)

def _parse_bool(value: Optional[Union[str, bool]], default: bool = False) -> bool:
    """
    Parse a boolean value from a string or boolean.

    Args:
        value (Optional[Union[str, bool]]): The value to parse.
        default (bool, optional): The default value if input is None. Defaults to False.

    Returns:
        bool: The parsed boolean value.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'y', 'on')
