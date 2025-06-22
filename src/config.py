# src/config.py

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv

from src.exceptions import ConfigError
from src.utils import logger, sanitize_filename

# Пути по умолчанию теперь относительные
DEFAULT_CACHE_PATH = Path("./telegram_obsidian_cache.json")
DEFAULT_EXPORT_PATH = Path("./debug_exports")  # Новый путь по умолчанию для экспорта

@dataclass
class ExportTarget:
    """Представляет одну цель для экспорта (канал, чат)."""
    id: Union[str, int]
    name: str = ""
    type: str = "unknown"

    def __post_init__(self):
        self.id = str(self.id).strip()
        # Автоопределение типа по формату ID
        if self.id.startswith('@') or 't.me/' in self.id or self.id.startswith('-100'):
            self.type = "channel"
        elif self.id.startswith('-') and self.id[1:].isdigit():
            self.type = "chat"
        elif self.id.isdigit():
            self.type = "user"

@dataclass
class Config:
    # Telegram
    api_id: int
    api_hash: str
    phone_number: Optional[str] = None
    session_name: str = "telegram_obsidian_session"
    export_targets: List[ExportTarget] = field(default_factory=list)

    # Paths
    export_path: Path = field(default=DEFAULT_EXPORT_PATH)
    media_subdir: str = "_media"
    use_entity_folders: bool = True

    # Processing
    only_new: bool = False
    media_download: bool = True
    verbose: bool = True
    max_workers: int = 8
    max_process_workers: int = 4
    concurrent_downloads: int = 10
    cache_save_interval: int = 50
    request_delay: float = 0.5
    message_batch_size: int = 100

    # Media
    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "fast"
    hw_acceleration: str = "none"
    use_h265: bool = False

    # Cache and UI
    cache_file: Path = field(default=DEFAULT_CACHE_PATH)
    interactive_mode: bool = False

    # Proxy
    proxy_type: Optional[str] = None
    proxy_addr: Optional[str] = None
    proxy_port: Optional[int] = None

    # Runtime state (не задаются из .env)
    export_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    media_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    cache: Dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self):
        """Валидация и настройка путей после инициализации."""
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
        """Настраивает пути для экспорта и медиа для всех целей."""
        self.export_paths = {}
        self.media_paths = {}
        for target in self.export_targets:
            target_id = str(target.id)
            target_name = self._get_entity_folder_name(target)

            base_path = self.export_path / target_name if self.use_entity_folders else self.export_path
            media_path = base_path / self.media_subdir

            self.export_paths[target_id] = base_path.resolve()
            self.media_paths[target_id] = media_path.resolve()

            for path in [base_path, media_path]:
                path.mkdir(parents=True, exist_ok=True)

    def _get_entity_folder_name(self, target: ExportTarget) -> str:
        """Генерирует безопасное имя папки для сущности."""
        name = target.name or f"id_{target.id}"
        clean_name = sanitize_filename(name, max_length=100)
        type_prefix = target.type if target.type != "unknown" else "entity"
        return f"{type_prefix}_{clean_name}"

    def add_export_target(self, target: ExportTarget):
        """Добавляет новую цель экспорта и обновляет пути."""
        if str(target.id) not in [str(t.id) for t in self.export_targets]:
            self.export_targets.append(target)
            self._update_target_paths()
            logger.info(f"Added export target: {target.name or target.id}")

    def get_export_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Возвращает путь для экспорта для указанной сущности."""
        return self.export_paths.get(str(entity_id), self.export_path)

    def get_media_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Возвращает путь для медиа для указанной сущности."""
        return self.media_paths.get(str(entity_id), self.export_path / self.media_subdir)

def _parse_bool(value: Optional[Union[str, bool]], default: bool = False) -> bool:
    """Конвертирует строковое значение в boolean."""
    if value is None: return default
    if isinstance(value, bool): return value
    return str(value).lower() in ('true', '1', 'yes', 'y', 'on')

def load_config(env_path: Union[str, Path] = ".env") -> Config:
    """Загружает конфигурацию из .env файла и переменных окружения."""
    if Path(env_path).exists():
        load_dotenv(dotenv_path=env_path)
    try:
        proxy_port_str = os.getenv("PROXY_PORT")
        proxy_port = int(proxy_port_str) if proxy_port_str and proxy_port_str.isdigit() else None
        config_dict = {
            "api_id": int(os.getenv("API_ID", 0)),
            "api_hash": os.getenv("API_HASH", ""),
            "phone_number": os.getenv("PHONE_NUMBER"),
            "session_name": os.getenv("SESSION_NAME", "telegram_obsidian_session"),
            "export_path": Path(os.getenv("EXPORT_PATH", DEFAULT_EXPORT_PATH)),
            "media_subdir": os.getenv("MEDIA_SUBDIR", "_media"),
            "use_entity_folders": _parse_bool(os.getenv("USE_ENTITY_FOLDERS"), True),
            "only_new": _parse_bool(os.getenv("ONLY_NEW"), False),
            "media_download": _parse_bool(os.getenv("MEDIA_DOWNLOAD"), True),
            "verbose": _parse_bool(os.getenv("VERBOSE"), True),
            "max_workers": int(os.getenv("MAX_WORKERS", 8)),
            "max_process_workers": int(os.getenv("MAX_PROCESS_WORKERS", 4)),
            "concurrent_downloads": int(os.getenv("CONCURRENT_DOWNLOADS", 10)),
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
            "proxy_type": os.getenv("PROXY_TYPE"),
            "proxy_addr": os.getenv("PROXY_ADDR"),
            "proxy_port": proxy_port,
        }

        export_targets = []
        targets_str = os.getenv("EXPORT_TARGETS", "")
        if targets_str:
            for target_id in [t.strip() for t in targets_str.split(',') if t.strip()]:
                export_targets.append(ExportTarget(id=target_id))

        config_dict["export_targets"] = export_targets
        return Config(**config_dict)

    except (ValueError, TypeError) as e:
        raise ConfigError(f"Invalid configuration value: {e}") from e
