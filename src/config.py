import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from dotenv import load_dotenv

from src.exceptions import ConfigError
from src.utils import logger, sanitize_filename

DEFAULT_CACHE_PATH = Path("./telegram_obsidian_cache.json").resolve()
DEFAULT_OBSIDIAN_PATH = Path("./obsidian_export").resolve()

@dataclass
class ExportTarget:
    """Represents a single channel or chat to export."""
    id: Union[str, int]
    name: str = ""
    type: str = "unknown"

    def __post_init__(self):
        self.id = str(self.id).strip()

        # Auto-detect type from ID format
        if self.id.startswith('@') or self.id.startswith('-100') or 't.me/' in self.id:
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
    obsidian_path: Path = field(default=DEFAULT_OBSIDIAN_PATH)
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

    # Path mappings
    export_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    media_paths: Dict[str, Path] = field(default_factory=dict, init=False)

    def __post_init__(self):
        """Validate config and setup paths."""
        if not self.api_id or not self.api_hash:
            raise ConfigError("API_ID and API_HASH must be set")

        # Normalize paths
        self.obsidian_path = Path(self.obsidian_path).resolve()
        self.cache_file = Path(self.cache_file).resolve()

        # Create required directories
        for path in [self.obsidian_path, self.cache_file.parent]:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigError(f"Failed to create path {path}: {e}")

        # Setup target paths
        self._update_target_paths()

        # Warn if no targets and not interactive
        if not self.export_targets and not self.interactive_mode:
            logger.warning("No export targets and interactive mode disabled. Nothing to process.")

    def _update_target_paths(self):
        """Setup export and media paths for all targets."""
        self.export_paths = {}
        self.media_paths = {}

        for target in self.export_targets:
            target_id = str(target.id)

            # Get base directory name for this target
            target_name = self._get_entity_folder_name(target) if self.use_entity_folders else ""

            # Configure paths
            if self.use_entity_folders:
                base_path = self.obsidian_path / target_name
                media_path = base_path / self.media_subdir
            else:
                base_path = self.obsidian_path
                media_path = base_path / self.media_subdir

            # Store paths
            self.export_paths[target_id] = base_path.resolve()
            self.media_paths[target_id] = media_path.resolve()

            # Create directories
            for path in [base_path, media_path]:
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    logger.error(f"Failed to create directory for target {target_id}: {e}")

    def _get_entity_folder_name(self, target: ExportTarget) -> str:
        """Generate a safe directory name for an entity."""
        name = target.name or f"id_{target.id}"
        clean_name = sanitize_filename(name, max_length=100)

        if self.use_entity_folders:
            type_prefix = target.type if target.type != "unknown" else "entity"
            return f"{type_prefix}_{clean_name}"
        return clean_name

    def add_export_target(self, target: ExportTarget):
        """Add a new export target and update paths."""
        if str(target.id) not in [str(t.id) for t in self.export_targets]:
            self.export_targets.append(target)
            self._update_target_paths()
            logger.info(f"Added export target: {target.id}")

    def get_export_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Get export path for an entity, with fallback to base path."""
        return self.export_paths.get(str(entity_id), self.obsidian_path)

    def get_media_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Get media path for an entity, with fallback to base media path."""
        entity_id = str(entity_id)
        if entity_id in self.media_paths:
            return self.media_paths[entity_id]
        return self.obsidian_path / self.media_subdir

def _parse_bool(value: Optional[Union[str, bool]], default: bool = False) -> bool:
    """Convert various inputs to boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes', 'y', 'on')
    return default

def load_config(env_path: Union[str, Path] = ".env") -> Config:
    """Load configuration from .env and environment variables."""
    env_path = Path(env_path)
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

    try:
        # Parse all config values from environment variables
        config_dict = {
            # Telegram
            "api_id": int(os.getenv("API_ID", 0)),
            "api_hash": os.getenv("API_HASH", ""),
            "phone_number": os.getenv("PHONE_NUMBER"),
            "session_name": os.getenv("SESSION_NAME", "telegram_obsidian_session"),

            # Paths
            "obsidian_path": Path(os.getenv("OBSIDIAN_PATH", DEFAULT_OBSIDIAN_PATH)),
            "media_subdir": os.getenv("MEDIA_SUBDIR", "_media"),
            "use_entity_folders": _parse_bool(os.getenv("USE_ENTITY_FOLDERS"), True),

            # Processing
            "only_new": _parse_bool(os.getenv("ONLY_NEW"), False),
            "media_download": _parse_bool(os.getenv("MEDIA_DOWNLOAD"), True),
            "verbose": _parse_bool(os.getenv("VERBOSE"), True),
            "max_workers": int(os.getenv("MAX_WORKERS", 8)),
            "max_process_workers": int(os.getenv("MAX_PROCESS_WORKERS", 4)),
            "concurrent_downloads": int(os.getenv("CONCURRENT_DOWNLOADS", 10)),
            "cache_save_interval": int(os.getenv("CACHE_SAVE_INTERVAL", 50)),
            "request_delay": float(os.getenv("REQUEST_DELAY", 0.5)),
            "message_batch_size": int(os.getenv("MESSAGE_BATCH_SIZE", 100)),

            # Media
            "image_quality": int(os.getenv("IMAGE_QUALITY", 85)),
            "video_crf": int(os.getenv("VIDEO_CRF", 28)),
            "video_preset": os.getenv("VIDEO_PRESET", "fast"),
            "hw_acceleration": os.getenv("HW_ACCELERATION", "none"),
            "use_h265": _parse_bool(os.getenv("USE_H265"), False),

            # Cache and UI
            "cache_file": Path(os.getenv("CACHE_FILE", DEFAULT_CACHE_PATH)),
            "interactive_mode": _parse_bool(os.getenv("INTERACTIVE_MODE"), False),
        }

        # Parse export targets
        export_targets = []
        targets_str = os.getenv("EXPORT_TARGETS", "")
        legacy_channel = os.getenv("TELEGRAM_CHANNEL")

        if targets_str:
            # Parse from EXPORT_TARGETS setting
            for target_id in [t.strip() for t in targets_str.split(',') if t.strip()]:
                export_targets.append(ExportTarget(id=target_id))
        elif legacy_channel:
            # Fallback to legacy setting
            export_targets = [ExportTarget(id=legacy_channel)]

        config_dict["export_targets"] = export_targets
        return Config(**config_dict)

    except ValueError as e:
        raise ConfigError(f"Invalid configuration value: {e}") from e
    except Exception as e:
        raise ConfigError(f"Failed to load configuration: {e}") from e
