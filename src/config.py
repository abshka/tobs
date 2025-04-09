import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Union, Dict
from dotenv import load_dotenv
from src.utils import logger

# Default cache location (e.g., alongside the script or in user data)
DEFAULT_CACHE_PATH = Path("./telegram_obsidian_cache.json").resolve()
DEFAULT_OBSIDIAN_PATH = Path("./obsidian_export").resolve()

@dataclass
class ExportTarget:
    """Represents a single channel or chat to export."""
    id: Union[str, int]
    name: str = ""  # Will be populated after resolving the entity
    type: str = "unknown" # Can be 'channel', 'chat', 'group', 'user'

    def __post_init__(self):
        # Keep ID as string initially for flexibility (usernames, links),
        # resolution logic will handle conversion if needed.
        self.id = str(self.id).strip()

        # Basic type guess based on common patterns (can be refined after resolving)
        if isinstance(self.id, str):
            if self.id.startswith('@'):
                self.type = "channel" # Or user, group - needs resolution
            elif self.id.startswith('-100'):
                self.type = "channel" # Likely supergroup/channel
            elif self.id.startswith('-') and self.id[1:].isdigit():
                 self.type = "chat" # Likely basic group
            elif self.id.isdigit():
                 self.type = "user" # Likely user ID (or maybe private channel/group ID)
            elif 't.me/' in self.id:
                 self.type = "channel" # Or user/group - needs resolution

@dataclass
class Config:
    # Telegram
    api_id: int
    api_hash: str
    phone_number: str | None = None
    session_name: str = "telegram_obsidian_session"
    export_targets: List[ExportTarget] = field(default_factory=list)

    # Obsidian / Export Paths
    obsidian_path: Path = field(default=DEFAULT_OBSIDIAN_PATH)
    media_subdir: str = "_media" # Subdirectory within each entity's folder for media
    use_entity_folders: bool = True # Organize notes/media in entity-specific folders

    # Processing
    only_new: bool = False
    media_download: bool = True
    verbose: bool = True
    max_workers: int = 8 # General purpose workers (threads mostly)
    max_process_workers: int = 4 # Specific limit for ProcessPoolExecutor
    concurrent_downloads: int = 10 # Concurrent media downloads limit
    cache_save_interval: int = 50 # Save cache every N messages processed

    # API Limits
    request_delay: float = 0.5 # Reduced delay, rely more on Telethon's flood wait handling
    message_batch_size: int = 100 # Messages per API fetch request

    # Media Optimization
    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "fast" # Faster preset

    # Caching
    cache_file: Path = field(default=DEFAULT_CACHE_PATH)

    # UI Options
    interactive_mode: bool = False

    # Derived paths (calculated after validation)
    # Dictionary mapping target ID (str) to its specific base export Path
    export_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    # Dictionary mapping target ID (str) to its specific media Path
    media_paths: Dict[str, Path] = field(default_factory=dict, init=False)

    def __post_init__(self):
        """Validate and derive paths after initialization."""
        if not self.api_id or not self.api_hash:
            raise ConfigError("API_ID and API_HASH must be set in .env or environment variables.")

        self.obsidian_path = Path(self.obsidian_path).resolve()
        self.cache_file = Path(self.cache_file).resolve()

        # Ensure base export directory exists
        try:
            self.obsidian_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
             raise ConfigError(f"Failed to create base Obsidian path '{self.obsidian_path}': {e}") from e

        # Ensure cache directory exists
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigError(f"Failed to create cache directory '{self.cache_file.parent}': {e}") from e

        # Populate export_paths and media_paths for initially defined targets
        self._update_target_paths()

        if not self.export_targets and not self.interactive_mode:
            logger.warning("No export targets specified (EXPORT_TARGETS in .env is empty or missing) "
                           "and interactive mode is disabled. No chats will be processed.")
            # Or force interactive mode:
            # logger.warning("No export targets specified. Forcing interactive mode.")
            # self.interactive_mode = True

        logger.debug(f"Configuration loaded. Base export path: {self.obsidian_path}")
        logger.debug(f"Cache file path: {self.cache_file}")
        logger.debug(f"Initial targets: {[t.id for t in self.export_targets]}")

    def _update_target_paths(self):
        """(Re)calculates export and media paths for all current targets."""
        self.export_paths = {}
        self.media_paths = {}
        for target in self.export_targets:
            target_id_str = str(target.id)
            entity_folder_name = self._get_entity_folder_name(target)

            if self.use_entity_folders:
                base_path = self.obsidian_path / entity_folder_name
                media_path = base_path / self.media_subdir
            else:
                # All notes in obsidian_path, all media in obsidian_path/media_subdir
                base_path = self.obsidian_path
                media_path = self.obsidian_path / self.media_subdir

            self.export_paths[target_id_str] = base_path.resolve()
            self.media_paths[target_id_str] = media_path.resolve()

            # Ensure directories exist (idempotent)
            try:
                base_path.mkdir(parents=True, exist_ok=True)
                media_path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.error(f"Failed to create directory for target {target_id_str}: {e}")
                # Decide how to handle: maybe remove target or raise error?
                # For now, log and continue. Export might fail later.


    def _get_entity_folder_name(self, target: ExportTarget) -> str:
        """Generates a safe directory name for an entity."""
        from src.utils import sanitize_filename # Avoid circular import at top level
        # Use resolved name if available, otherwise sanitized ID
        name_part = target.name if target.name else f"id_{target.id}"
        # Sanitize heavily for filesystem safety
        sanitized_name = sanitize_filename(name_part, max_length=100)
        # Add type prefix for clarity if using entity folders
        if self.use_entity_folders:
             type_prefix = target.type if target.type != "unknown" else "entity"
             return f"{type_prefix}_{sanitized_name}"
        else:
            return sanitized_name # Not used if use_entity_folders is False


    def add_export_target(self, target: ExportTarget):
        """Adds a new export target and updates paths."""
        if str(target.id) not in [str(t.id) for t in self.export_targets]:
            self.export_targets.append(target)
            self._update_target_paths() # Recalculate paths for the new target
            logger.info(f"Added export target: {target.id}. Paths updated.")
        else:
            logger.debug(f"Target {target.id} already exists.")

    def get_export_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Get the base export path for a specific entity ID."""
        path = self.export_paths.get(str(entity_id))
        if not path:
            # This might happen if a target was added dynamically without updating paths,
            # or if an invalid ID is requested. Log a warning and return default.
            logger.warning(f"Export path not found for entity ID '{entity_id}'. Defaulting to base Obsidian path.")
            return self.obsidian_path
        return path

    def get_media_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Get the media storage path for a specific entity ID."""
        path = self.media_paths.get(str(entity_id))
        if not path:
            logger.warning(f"Media path not found for entity ID '{entity_id}'. Defaulting to base media path.")
            # Fallback: base_obsidian_path / media_subdir
            return self.obsidian_path / self.media_subdir
        return path

def _parse_bool(value: str, default: bool = False) -> bool:
    """Parses a string to a boolean (case-insensitive)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes', 'y', 'on')
    return default

def load_config(env_path: Union[str, Path] = ".env") -> Config:
    """Loads configuration from .env file and environment variables."""
    env_path = Path(env_path)
    if env_path.exists():
        logger.info(f"Loading configuration from {env_path}...")
        load_dotenv(dotenv_path=env_path)
    else:
        logger.info(f".env file not found at {env_path}. Using environment variables only.")

    try:
        config_dict = {
            # Telegram
            "api_id": int(os.getenv("API_ID", 0)),
            "api_hash": os.getenv("API_HASH", ""),
            "phone_number": os.getenv("PHONE_NUMBER"), # Optional
            "session_name": os.getenv("SESSION_NAME", "telegram_obsidian_session"),

            # Obsidian / Export Paths
            "obsidian_path": Path(os.getenv("OBSIDIAN_PATH", DEFAULT_OBSIDIAN_PATH)),
            "media_subdir": os.getenv("MEDIA_SUBDIR", "_media"),
            "use_entity_folders": _parse_bool(os.getenv("USE_ENTITY_FOLDERS"), default=True),

            # Processing
            "only_new": _parse_bool(os.getenv("ONLY_NEW"), default=False),
            "media_download": _parse_bool(os.getenv("MEDIA_DOWNLOAD"), default=True),
            "verbose": _parse_bool(os.getenv("VERBOSE"), default=True),
            "max_workers": int(os.getenv("MAX_WORKERS", 8)),
            "max_process_workers": int(os.getenv("MAX_PROCESS_WORKERS", 4)),
            "concurrent_downloads": int(os.getenv("CONCURRENT_DOWNLOADS", 10)),
            "cache_save_interval": int(os.getenv("CACHE_SAVE_INTERVAL", 50)),

            # API Limits
            "request_delay": float(os.getenv("REQUEST_DELAY", 0.5)),
            "message_batch_size": int(os.getenv("MESSAGE_BATCH_SIZE", 100)),

            # Media Optimization
            "image_quality": int(os.getenv("IMAGE_QUALITY", 85)),
            "video_crf": int(os.getenv("VIDEO_CRF", 28)),
            "video_preset": os.getenv("VIDEO_PRESET", "fast"),

            # Caching
            "cache_file": Path(os.getenv("CACHE_FILE", DEFAULT_CACHE_PATH)),

            # UI Options
            "interactive_mode": _parse_bool(os.getenv("INTERACTIVE_MODE"), default=False),
        }

        # Handle export targets from env var (comma-separated string)
        export_targets = []
        targets_str = os.getenv("EXPORT_TARGETS", "") # e.g., "target1, @username, -100123456"
        if targets_str:
            target_ids = [t.strip() for t in targets_str.split(',') if t.strip()]
            for target_id in target_ids:
                 # Create ExportTarget object, type will be guessed initially
                 export_targets.append(ExportTarget(id=target_id))

        config_dict["export_targets"] = export_targets

        # Legacy support for TELEGRAM_CHANNEL
        legacy_channel = os.getenv("TELEGRAM_CHANNEL")
        if legacy_channel and not export_targets:
            logger.warning("Using legacy TELEGRAM_CHANNEL variable. Please switch to EXPORT_TARGETS.")
            config_dict["export_targets"] = [ExportTarget(id=legacy_channel)]

        return Config(**config_dict)

    except ValueError as e:
        logger.error(f"Configuration Error: Invalid value format - {e}")
        raise ConfigError(f"Invalid configuration value: {e}") from e
    except Exception as e:
        logger.error(f"Unexpected error loading configuration: {e}", exc_info=True)
        raise ConfigError(f"Failed to load configuration: {e}") from e

# Custom Exception for Config errors
class ConfigError(Exception):
    pass
