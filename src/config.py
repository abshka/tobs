import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Literal
import psutil

from dotenv import load_dotenv

from src.exceptions import ConfigError
from src.utils import logger, sanitize_filename

DEFAULT_CACHE_PATH = Path("./tobs_cache.json")
DEFAULT_EXPORT_PATH = Path("./debug_exports")

PerformanceProfile = Literal["conservative", "balanced", "aggressive", "custom"]
HardwareAcceleration = Literal["none", "nvidia", "amd", "intel", "auto"]
ProxyType = Literal["socks4", "socks5", "http"]

MIN_MEMORY_GB = 2
MIN_FREE_DISK_GB = 1
RECOMMENDED_MEMORY_GB = 8

@dataclass
class ExportTarget:
    """
    Represents a single export target (channel, chat, or user).
    """
    id: Union[str, int]
    name: str = ""
    type: str = "unknown"
    message_id: Optional[int] = None

    def __post_init__(self):
        """
        Initialize the ExportTarget and determine its type based on the id.
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
class PerformanceSettings:
    """
    Настройки производительности с автоматической оптимизацией.
    """
    workers: int = 8
    download_workers: int = 12
    io_workers: int = 16
    ffmpeg_workers: int = 4
    message_batch_size: int = 100
    file_batch_size: int = 20
    media_batch_size: int = 5
    cache_batch_size: int = 50
    memory_limit_mb: int = 1024
    cache_size_limit_mb: int = 256
    telegram_cache_ttl: int = 300
    connection_pool_size: int = 100
    connection_pool_per_host: int = 20
    request_timeout: float = 60.0
    max_retries: int = 3
    retry_delay: float = 1.0
    throttle_threshold_kbps: int = 50
    throttle_pause_s: int = 30
    rate_limit_calls_per_second: float = 10.0
    batch_size: Optional[int] = None
    cache_save_interval: int = 50

    @classmethod
    def auto_configure(cls, profile: PerformanceProfile = "balanced") -> "PerformanceSettings":
        """
        Автоматическая настройка производительности на основе системных ресурсов.
        """
        cpu_count = os.cpu_count() or 4
        memory_gb = psutil.virtual_memory().total / (1024**3)
        available_memory_gb = psutil.virtual_memory().available / (1024**3)

        if memory_gb < MIN_MEMORY_GB:
            logger.warning(f"System has only {memory_gb:.1f}GB RAM, minimum {MIN_MEMORY_GB}GB recommended")

        if available_memory_gb < MIN_FREE_DISK_GB:
            logger.warning(f"Low available memory: {available_memory_gb:.1f}GB")

        if profile == "conservative":
            return cls(
                workers=min(4, cpu_count),
                download_workers=min(6, cpu_count),
                io_workers=min(8, cpu_count * 2),
                ffmpeg_workers=min(2, cpu_count // 2),
                message_batch_size=50,
                file_batch_size=10,
                media_batch_size=3,
                memory_limit_mb=int(available_memory_gb * 200),
                cache_size_limit_mb=128,
                connection_pool_size=50,
            )
        elif profile == "balanced":
            return cls(
                workers=min(8, cpu_count),
                download_workers=min(12, int(cpu_count * 1.5)),
                io_workers=min(16, cpu_count * 2),
                ffmpeg_workers=min(4, cpu_count // 2),
                message_batch_size=100,
                file_batch_size=20,
                media_batch_size=5,
                memory_limit_mb=int(available_memory_gb * 400),
                cache_size_limit_mb=256,
                connection_pool_size=100,
            )
        elif profile == "aggressive":
            return cls(
                workers=min(16, cpu_count * 2),
                download_workers=min(24, cpu_count * 3),
                io_workers=min(32, cpu_count * 4),
                ffmpeg_workers=min(8, cpu_count),
                message_batch_size=200,
                file_batch_size=50,
                media_batch_size=10,
                memory_limit_mb=int(available_memory_gb * 600),
                cache_size_limit_mb=512,
                connection_pool_size=200,
                rate_limit_calls_per_second=20.0,
            )
        else:
            return cls()


@dataclass
class Config:
    """
    Основная конфигурация экспортера с оптимизациями производительности.
    """
    api_id: int
    api_hash: str
    phone_number: Optional[str] = None
    session_name: str = "tobs_session"
    request_delay: float = 0.5
    export_targets: List[ExportTarget] = field(default_factory=list)
    export_path: Path = field(default=DEFAULT_EXPORT_PATH)
    media_subdir: str = "_media"
    use_entity_folders: bool = True
    only_new: bool = False
    media_download: bool = True
    export_comments: bool = False
    performance_profile: PerformanceProfile = "balanced"
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)
    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "fast"
    hw_acceleration: HardwareAcceleration = "none"
    use_h265: bool = False
    cache_file: Path = field(default=DEFAULT_CACHE_PATH)
    cache_manager: Any = None
    log_level: str = "INFO"
    interactive_mode: bool = False
    dialog_fetch_limit: int = 20
    proxy_type: Optional[ProxyType] = None
    proxy_addr: Optional[str] = None
    proxy_port: Optional[int] = None
    enable_performance_monitoring: bool = True
    performance_log_interval: int = 60
    max_error_rate: float = 0.1
    error_cooldown_time: int = 300
    max_file_size_mb: int = 2000
    max_total_size_gb: int = 100
    export_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    media_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    cache: Dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self._validate_required_fields()
        if hasattr(self, 'performance_profile'):
            self.performance = PerformanceSettings.auto_configure(self.performance_profile)
        self._setup_paths()
        self._validate_system_requirements()
        self._update_target_paths()
        self._log_configuration()

    def _validate_required_fields(self):
        if not self.api_id or not self.api_hash:
            raise ConfigError("API_ID and API_HASH must be set in .env file", field_name="api_credentials")
        if self.api_id <= 0:
            raise ConfigError(f"Invalid API_ID: {self.api_id}. Must be positive integer", field_name="api_id", field_value=self.api_id)
        if len(self.api_hash) < 32:
            raise ConfigError(f"Invalid API_HASH length: {len(self.api_hash)}. Must be at least 32 characters", field_name="api_hash")

    def _setup_paths(self):
        self.export_path = Path(self.export_path).resolve()
        self.cache_file = Path(self.cache_file).resolve()
        for path in [self.export_path, self.cache_file.parent]:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigError(f"Failed to create path {path}: {e}")

    def _validate_system_requirements(self):
        try:
            memory_gb = psutil.virtual_memory().total / (1024**3)
            available_memory_gb = psutil.virtual_memory().available / (1024**3)
            if memory_gb < MIN_MEMORY_GB:
                logger.warning(f"System has only {memory_gb:.1f}GB RAM, minimum {MIN_MEMORY_GB}GB recommended")
            disk_usage = psutil.disk_usage(self.export_path)
            free_space_gb = disk_usage.free / (1024**3)
            if free_space_gb < MIN_FREE_DISK_GB:
                raise ConfigError(f"Insufficient disk space: {free_space_gb:.1f}GB free, minimum {MIN_FREE_DISK_GB}GB required", context={"path": str(self.export_path), "free_space_gb": free_space_gb})
            if self.performance.memory_limit_mb > available_memory_gb * 1024 * 0.8:
                logger.warning(f"Memory limit {self.performance.memory_limit_mb}MB is high for available memory {available_memory_gb:.1f}GB")
        except Exception as e:
            logger.warning(f"Could not validate system requirements: {e}")

    def _update_target_paths(self):
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
        name = target.name or f"id_{target.id}"
        clean_name = sanitize_filename(name, max_length=100)
        return clean_name

    def _log_configuration(self):
        if not self.export_targets and not self.interactive_mode:
            logger.warning("No export targets defined and interactive mode is off. Nothing to do.")
        logger.info(f"Configuration loaded with performance profile: {self.performance_profile}")
        logger.info(f"Workers: {self.performance.workers}, Download workers: {self.performance.download_workers}")
        logger.info(f"Memory limit: {self.performance.memory_limit_mb}MB")

    def add_export_target(self, target: ExportTarget):
        if str(target.id) not in [str(t.id) for t in self.export_targets]:
            self.export_targets.append(target)
            self._update_target_paths()

    def get_export_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        return self.export_paths.get(str(entity_id), self.export_path)

    def get_media_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        return self.media_paths.get(str(entity_id), self.export_path / self.media_subdir)

    def update_performance_profile(self, profile: PerformanceProfile):
        self.performance_profile = profile
        self.performance = PerformanceSettings.auto_configure(profile)
        logger.info(f"Updated performance profile to: {profile}")

    def to_dict(self) -> dict:
        allowed = {f.name for f in fields(self) if f.init}
        result = {}
        for k, v in asdict(self).items():
            if k in allowed:
                if k == "export_targets":
                    result[k] = [asdict(t) if hasattr(t, "__dataclass_fields__") else t for t in v]
                elif k == "performance" and hasattr(v, "__dataclass_fields__"):
                    result[k] = asdict(v)
                elif isinstance(v, Path):
                    result[k] = str(v)
                else:
                    result[k] = v
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        allowed = {f.name for f in fields(cls) if f.init}
        filtered = {}
        for k, v in d.items():
            if k in allowed:
                if k == "export_targets":
                    filtered[k] = [ExportTarget(**t) if not isinstance(t, ExportTarget) else t for t in v]
                elif k == "performance" and isinstance(v, dict):
                    filtered[k] = PerformanceSettings(**v)
                elif k in ("export_path", "cache_file") and not isinstance(v, Path):
                    filtered[k] = Path(v)
                else:
                    filtered[k] = v
        return cls(**filtered)

    @classmethod
    def from_env(cls, env_path: Union[str, Path] = ".env") -> "Config":
        if Path(env_path).exists():
            load_dotenv(dotenv_path=env_path)
        try:
            proxy_port_str = os.getenv("PROXY_PORT")
            proxy_port = int(proxy_port_str) if proxy_port_str and proxy_port_str.isdigit() else None
            performance_profile = os.getenv("PERFORMANCE_PROFILE", "balanced")
            if performance_profile not in ["conservative", "balanced", "aggressive", "custom"]:
                logger.warning(f"Unknown performance profile '{performance_profile}', using 'balanced'")
                performance_profile = "balanced"

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
                "export_comments": _parse_bool(os.getenv("EXPORT_COMMENTS"), False),
                "log_level": os.getenv("LOG_LEVEL", "INFO"),
                "performance_profile": performance_profile,
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
                "enable_performance_monitoring": _parse_bool(os.getenv("ENABLE_PERFORMANCE_MONITORING"), True),
                "performance_log_interval": int(os.getenv("PERFORMANCE_LOG_INTERVAL", 60)),
                "max_error_rate": float(os.getenv("MAX_ERROR_RATE", 0.1)),
                "error_cooldown_time": int(os.getenv("ERROR_COOLDOWN_TIME", 300)),
                "max_file_size_mb": int(os.getenv("MAX_FILE_SIZE_MB", 2000)),
                "max_total_size_gb": int(os.getenv("MAX_TOTAL_SIZE_GB", 100)),
            }

            performance_settings = {
                "workers": int(os.getenv("WORKERS", 8)),
                "ffmpeg_workers": int(os.getenv("FFMPEG_WORKERS", 4)),
                "batch_size": int(os.getenv("BATCH_SIZE")) if os.getenv("BATCH_SIZE") else None,
                "cache_save_interval": int(os.getenv("CACHE_SAVE_INTERVAL", 50)),
                "message_batch_size": int(os.getenv("MESSAGE_BATCH_SIZE", 100)),
                "throttle_threshold_kbps": int(os.getenv("THROTTLE_THRESHOLD_KBPS", 50)),
                "throttle_pause_s": int(os.getenv("THROTTLE_PAUSE_S", 30)),
            }
            config_dict["performance"] = PerformanceSettings(**performance_settings)

            export_targets = []
            targets_str = os.getenv("EXPORT_TARGETS", "")
            if targets_str:
                for target_id in [t.strip() for t in targets_str.split(',') if t.strip()]:
                    export_targets.append(ExportTarget(id=target_id))
            config_dict["export_targets"] = export_targets
            return cls(**config_dict)
        except (ValueError, TypeError) as e:
            raise ConfigError(f"Invalid configuration value: {e}") from e

def _parse_bool(value: Optional[Union[str, bool]], default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'y', 'on')