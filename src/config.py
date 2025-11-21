import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import psutil
from dotenv import load_dotenv

from src.exceptions import ConfigError
from src.utils import logger, sanitize_filename

DEFAULT_CACHE_PATH = Path("./huy.json")
DEFAULT_EXPORT_PATH = Path("./huys")

# Типизация для профилей производительности
PerformanceProfile = Literal["conservative", "balanced", "aggressive", "custom"]
HardwareAcceleration = Literal["none", "vaapi", "nvenc", "qsv", "auto"]
ProxyType = Literal["socks4", "socks5", "http"]

# Минимальные системные требования
MIN_MEMORY_GB = 2
MIN_FREE_DISK_GB = 1
RECOMMENDED_MEMORY_GB = 8

# ⏱️ Таймауты для асинхронных операций (Phase 2 Task 2.2)
ITER_MESSAGES_TIMEOUT = 300  # 5 минут для fetching сообщений
EXPORT_OPERATION_TIMEOUT = 600  # 10 минут для экспорта одной сущности
QUEUE_OPERATION_TIMEOUT = 30  # 30 секунд для получения задачи из очереди
HEALTH_CHECK_TIMEOUT = 10  # 10 секунд для health check
MEDIA_DOWNLOAD_TIMEOUT = 3600  # 1 час для скачивания медиа


@dataclass
class ExportTarget:
    """
    Представляет цель экспорта (канал, чат или пользователь) с улучшенной типизацией.
    """

    id: Union[str, int]
    name: str = ""
    type: str = "unknown"
    message_id: Optional[int] = None

    # Новые поля для оптимизации
    estimated_messages: Optional[int] = None  # Примерное количество сообщений
    last_updated: Optional[float] = None  # Timestamp последнего обновления
    priority: int = 1  # Приоритет обработки (1-10)

    # Поля для работы с топиками в чатах
    is_forum: bool = False  # Является ли чат форумом с топиками
    topic_id: Optional[int] = None  # ID конкретного топика (если экспортируем топик)
    export_all_topics: bool = True  # Экспортировать все топики или только указанный
    topic_filter: Optional[List[int]] = (
        None  # Список ID топиков для экспорта (если не все)
    )
    export_path: Optional[Path] = None  # Add the missing export_path

    def __post_init__(self):
        """
        Инициализация ExportTarget с улучшенным определением типа.
        """
        self.id = str(self.id).strip()
        if self.type == "single_post":
            return

        # Сначала проверяем ссылки на топики (приоритет)
        if "/c/" in self.id and "/" in self.id.split("/c/")[-1]:
            # Ссылка на топик: https://t.me/c/chat_id/topic_id или /c/chat_id/topic_id
            self.type = "forum_topic"
            try:
                parts = self.id.split("/c/")[-1].split("/")
                if len(parts) >= 2:
                    chat_id, topic_id = parts[0], parts[1]
                    self.id = f"-100{chat_id}"  # Преобразуем в полный chat_id
                    self.topic_id = int(topic_id)
                    self.is_forum = True
                    self.export_all_topics = False
            except (ValueError, IndexError):
                logger.warning(f"Could not parse forum topic URL: {self.id}")
            return

        # Если тип уже правильно установлен, не переписываем его
        if self.type in ["forum_topic", "forum_chat", "channel", "chat", "user"]:
            return

        # Улучшенное определение типа сущности
        if self.id.startswith("@"):
            self.type = "channel"
        elif "t.me/" in self.id:
            # Обычные t.me ссылки (не топики)
            self.type = "channel"
        elif self.id.startswith("-100"):
            self.type = "channel"
        elif self.id.startswith("-") and self.id[1:].isdigit():
            self.type = "chat"
        elif self.id.isdigit():
            self.type = "user"
        else:
            logger.warning(f"Could not determine type for entity ID: {self.id}")


@dataclass
class PerformanceSettings:
    """
    Настройки производительности с автоматической оптимизацией.
    """

    # Основные настройки параллелизма
    workers: int = 8
    download_workers: int = 12
    io_workers: int = 16
    ffmpeg_workers: int = 4

    # Настройки батчевой обработки
    message_batch_size: int = 100
    media_batch_size: int = 5
    cache_batch_size: int = 50
    cache_save_interval: int = 100

    # Настройки параллельной обработки форумов
    forum_parallel_enabled: bool = True
    forum_max_workers: int = 8
    forum_batch_size: int = 20
    forum_media_parallel: bool = True

    # Настройки памяти и кэширования
    memory_limit_mb: int = 1024
    cache_size_limit_mb: int = 256
    telegram_cache_ttl: int = 300  # TTL для кэша Telegram API в секундах

    # Настройки сети
    connection_pool_size: int = 100
    connection_pool_per_host: int = 20
    request_timeout: float = 1800.0  # 30 минут для больших файлов
    max_retries: int = 5
    retry_delay: float = 2.0

    # Адаптивные таймауты для скачивания файлов
    base_download_timeout: float = 300.0  # Базовый таймаут 5 минут
    large_file_timeout: float = 3600.0  # 1 час для файлов > 500MB
    huge_file_timeout: float = 7200.0  # 2 часа для файлов > 1GB
    large_file_threshold_mb: int = 500  # Порог для больших файлов
    huge_file_threshold_mb: int = 1000  # Порог для огромных файлов

    # Таймауты для низкоскоростных соединений
    slow_connection_multiplier: float = (
        3.0  # Множитель таймаута для медленных соединений
    )
    slow_speed_threshold_kbps: float = 100.0  # Порог медленного соединения (KB/s)

    # Настройки повторных попыток для больших файлов
    large_file_max_retries: int = (
        10  # Увеличенное количество попыток для больших файлов
    )
    large_file_retry_delay: float = 10.0  # Увеличенная задержка между попытками

    # Упорные загрузки (гарантированное скачивание)
    enable_persistent_download: bool = True  # Упорный режим - никогда не сдаваться
    persistent_download_min_size_mb: int = (
        1  # Минимальный размер для упорного режима (почти все файлы)
    )
    persistent_max_failures: int = 20  # Максимум неудач подряд перед отказом
    persistent_chunk_timeout: int = 600  # Базовый таймаут для частей (10 минут)

    # Параллельные загрузки
    enable_parallel_download: bool = False  # Отключено по умолчанию для надежности
    parallel_download_min_size_mb: int = (
        5  # Минимальный размер файла для параллельной загрузки
    )
    max_parallel_connections: int = 8  # Максимальное количество параллельных соединений
    max_concurrent_downloads: int = (
        3  # Максимальное количество одновременных параллельных загрузок
    )

    # Настройки throttling
    throttle_threshold_kbps: int = 50
    throttle_pause_s: int = 30
    rate_limit_calls_per_second: float = 10.0

    @classmethod
    def auto_configure(
        cls, profile: PerformanceProfile = "balanced"
    ) -> "PerformanceSettings":
        """
        Автоматическая настройка производительности на основе системных ресурсов.
        """
        # Получаем информацию о системе
        cpu_count = os.cpu_count() or 4
        memory_gb = psutil.virtual_memory().total / (1024**3)
        available_memory_gb = psutil.virtual_memory().available / (1024**3)

        # Проверяем минимальные требования
        if memory_gb < MIN_MEMORY_GB:
            logger.warning(
                f"System has only {memory_gb:.1f}GB RAM, minimum {MIN_MEMORY_GB}GB recommended"
            )

        if available_memory_gb < MIN_FREE_DISK_GB:
            logger.warning(f"Low available memory: {available_memory_gb:.1f}GB")

        # Настройки в зависимости от профиля
        if profile == "conservative":
            return cls(
                workers=min(4, cpu_count),
                download_workers=min(6, cpu_count),
                io_workers=min(8, cpu_count * 2),
                ffmpeg_workers=min(2, cpu_count // 2),
                message_batch_size=50,
                media_batch_size=3,
                memory_limit_mb=int(available_memory_gb * 200),  # 20% доступной памяти
                cache_size_limit_mb=128,
                connection_pool_size=50,
                cache_save_interval=100,
                forum_parallel_enabled=True,
                forum_max_workers=4,
                forum_batch_size=10,
                forum_media_parallel=True,
                request_timeout=1200.0,
                large_file_timeout=2400.0,
                huge_file_timeout=4800.0,
                large_file_max_retries=8,
                large_file_retry_delay=15.0,
                # Упорные загрузки для консервативного профиля
                enable_persistent_download=True,
                persistent_download_min_size_mb=1,
                persistent_max_failures=15,
                persistent_chunk_timeout=900,  # 15 минут для медленных соединений
                # Параллельные загрузки отключены для надежности
                enable_parallel_download=False,
                max_parallel_connections=4,
                max_concurrent_downloads=1,
            )

        elif profile == "balanced":
            return cls(
                workers=min(8, cpu_count),
                download_workers=min(12, int(cpu_count * 1.5)),
                io_workers=min(16, cpu_count * 2),
                ffmpeg_workers=min(4, cpu_count // 2),
                message_batch_size=100,
                media_batch_size=5,
                memory_limit_mb=int(available_memory_gb * 400),  # 40% доступной памяти
                cache_size_limit_mb=256,
                connection_pool_size=100,
                cache_save_interval=100,
                forum_parallel_enabled=True,
                forum_max_workers=8,
                forum_batch_size=20,
                forum_media_parallel=True,
                request_timeout=1800.0,
                large_file_timeout=3600.0,
                huge_file_timeout=7200.0,
                large_file_max_retries=10,
                large_file_retry_delay=10.0,
                # Упорные загрузки для сбалансированного профиля
                enable_persistent_download=True,
                persistent_download_min_size_mb=1,
                persistent_max_failures=20,
                persistent_chunk_timeout=600,  # 10 минут
                # Параллельные загрузки отключены для надежности
                enable_parallel_download=False,
                max_parallel_connections=8,
                max_concurrent_downloads=2,
            )

        elif profile == "aggressive":
            return cls(
                workers=min(16, cpu_count * 2),
                download_workers=min(24, cpu_count * 3),
                io_workers=min(32, cpu_count * 4),
                ffmpeg_workers=min(8, cpu_count),
                message_batch_size=200,
                media_batch_size=10,
                memory_limit_mb=int(available_memory_gb * 600),  # 60% доступной памяти
                cache_size_limit_mb=512,
                connection_pool_size=200,
                cache_save_interval=200,
                forum_parallel_enabled=True,
                forum_max_workers=16,
                forum_batch_size=30,
                forum_media_parallel=True,
                request_timeout=2400.0,
                large_file_timeout=4800.0,
                huge_file_timeout=9600.0,
                large_file_max_retries=15,
                large_file_retry_delay=5.0,
                # Упорные загрузки для агрессивного профиля
                enable_persistent_download=True,
                persistent_download_min_size_mb=1,
                persistent_max_failures=25,
                persistent_chunk_timeout=600,  # 10 минут
                enable_parallel_download=True,
                max_parallel_connections=12,
                max_concurrent_downloads=3,
            )

        else:  # custom - возвращаем defaults
            return cls()


@dataclass
class TranscriptionConfig:
    """
    Configuration for audio transcription system.

    Version: 5.0.0 - Simplified standalone implementation (Whisper Large V3 only)
    """

    # Basic settings
    enabled: bool = True
    language: str = "ru"  # Default language for transcription
    device: str = "auto"  # 'auto', 'cuda', 'cpu', 'cuda:0'

    # Whisper settings
    compute_type: str = "auto"  # 'auto', 'int8', 'float16', 'float32'
    batch_size: int = 8  # Batch size for batched inference
    duration_threshold: int = 60  # Seconds threshold for batched mode
    use_batched: bool = True  # Enable batched inference

    # Caching
    cache_enabled: bool = True  # Enable result caching


@dataclass
class Config:
    """
    Основная конфигурация экспортера.
    """

    api_id: int
    api_hash: str

    phone_number: Optional[str] = None
    session_name: str = "tobs_session"
    request_delay: float = 0.5

    # Core system settings
    enable_core_systems: bool = True
    cache_max_size_mb: int = 1024
    adaptation_strategy: str = (
        "balanced"  # conservative, balanced, aggressive, disabled
    )
    monitoring_interval: float = 30.0
    dashboard_retention_hours: int = 24

    export_targets: List[ExportTarget] = field(default_factory=list)
    export_path: Path = field(default=DEFAULT_EXPORT_PATH)
    media_subdir: str = "media"
    cache_subdir: str = "cache"
    monitoring_subdir: str = "monitoring"
    use_entity_folders: bool = True
    use_structured_export: bool = True  # Новая структурированная организация
    only_new: bool = True
    media_download: bool = True
    export_comments: bool = False

    # Media processing settings
    process_video: bool = False  # По умолчанию выключено (как в MediaProcessor)
    process_audio: bool = True  # По умолчанию включено
    process_images: bool = True  # По умолчанию включено

    # Audio transcription settings (v3.0.0)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)

    export_closed_topics: bool = False  # Экспортировать закрытые топики
    export_pinned_topics_first: bool = (
        True  # Экспортировать закрепленные топики первыми
    )
    topic_message_limit: Optional[int] = (
        None  # Лимит сообщений на топик (None = без лимита)
    )
    create_topic_summaries: bool = True  # Создавать summary файлы для топиков
    forum_structure_mode: str = "by_topic"  # "by_topic" или "flat"

    performance_profile: PerformanceProfile = "balanced"
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)

    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "fast"
    hw_acceleration: HardwareAcceleration = "vaapi"
    use_h265: bool = False
    compress_video: bool = True  # Сжатие видео включено по умолчанию с VA-API
    vaapi_device: str = "/dev/dri/renderD128"  # Устройство VA-API
    vaapi_quality: int = 25  # Качество для VA-API (18-28 для h264, 25-35 для hevc)

    cache_file: Path = field(default=DEFAULT_CACHE_PATH)
    cache_manager: Any = None

    log_level: str = "INFO"

    dialog_fetch_limit: int = 20

    proxy_type: Optional[ProxyType] = None
    proxy_addr: Optional[str] = None
    proxy_port: Optional[int] = None

    export_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    media_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    cache_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    monitoring_paths: Dict[str, Path] = field(default_factory=dict, init=False)
    cache: Dict[str, Any] = field(default_factory=dict, init=False)

    # Мониторинг производительности
    enable_performance_monitoring: bool = True
    performance_log_interval: int = 60  # секунд

    # Обработка ошибок
    max_error_rate: float = 0.1  # 10% максимум ошибок
    error_cooldown_time: int = 300  # 5 минут

    # Ресурсные лимиты
    max_file_size_mb: int = 2000  # 2GB лимит на файл
    max_total_size_gb: int = 100  # 100GB лимит на весь экспорт

    def __post_init__(self):
        """
        Инициализация конфигурации с валидацией и автоматической настройкой.
        """
        # Базовая валидация
        self._validate_required_fields()

        # Автоматическая настройка производительности
        if hasattr(self, "performance_profile"):
            self.performance = PerformanceSettings.auto_configure(
                self.performance_profile
            )

        # Если путь к кэшу не абсолютный — делаем его относительным к export_path
        if not Path(self.cache_file).is_absolute():
            self.cache_file = Path(self.export_path) / Path(self.cache_file).name

        # Валидация и нормализация путей
        self._setup_paths()

        # Валидация системных ресурсов
        self._validate_system_requirements()

        # Обновление путей для целей экспорта
        self._update_target_paths()

        # Логирование конфигурации
        self._log_configuration()

    def _validate_required_fields(self):
        """Валидация обязательных полей."""
        if not self.api_id or not self.api_hash:
            raise ConfigError(
                "API_ID and API_HASH must be set in .env file",
                field_name="api_credentials",
            )

        if self.api_id <= 0:
            raise ConfigError(
                f"Invalid API_ID: {self.api_id}. Must be positive integer",
                field_name="api_id",
                field_value=self.api_id,
            )

        if len(self.api_hash) < 32:
            raise ConfigError(
                f"Invalid API_HASH length: {len(self.api_hash)}. Must be at least 32 characters",
                field_name="api_hash",
            )

    def _setup_paths(self):
        """Настройка и валидация путей."""
        self.export_path = Path(self.export_path).absolute()
        self.cache_file = Path(self.cache_file).absolute()

        # Создание директорий
        for path in [self.export_path, self.cache_file.parent]:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigError(f"Failed to create path {path}: {e}")

    def _validate_system_requirements(self):
        """Валидация системных требований."""
        try:
            # Проверка памяти
            memory_gb = psutil.virtual_memory().total / (1024**3)
            available_memory_gb = psutil.virtual_memory().available / (1024**3)

            if memory_gb < MIN_MEMORY_GB:
                logger.warning(
                    f"System has only {memory_gb:.1f}GB RAM, minimum {MIN_MEMORY_GB}GB recommended"
                )

            # Проверка дискового пространства
            disk_usage = psutil.disk_usage(str(self.export_path))
            free_space_gb = disk_usage.free / (1024**3)

            if free_space_gb < MIN_FREE_DISK_GB:
                raise ConfigError(
                    f"Insufficient disk space: {free_space_gb:.1f}GB free, minimum {MIN_FREE_DISK_GB}GB required",
                    context={
                        "path": str(self.export_path),
                        "free_space_gb": free_space_gb,
                    },
                )

            # Проверка настроек производительности
            if self.performance.memory_limit_mb > available_memory_gb * 1024 * 0.8:
                logger.warning(
                    f"Memory limit {self.performance.memory_limit_mb}MB is high for available memory {available_memory_gb:.1f}GB"
                )

        except Exception as e:
            logger.warning(f"Could not validate system requirements: {e}")

    def _update_target_paths(self):
        """Обновление путей экспорта для каждой цели с поддержкой структурированной организации."""
        self.export_paths = {}
        self.media_paths = {}
        self.cache_paths = {}
        self.monitoring_paths = {}

        for target in self.export_targets:
            target_id = str(target.id)
            target_name = self._get_entity_folder_name(target)

            if self.use_entity_folders:
                if self.use_structured_export:
                    # Структурированная организация: entity_name/ с подпапками
                    entity_base = self.export_path / target_name
                    export_path = entity_base  # Основной файл в корне сущности
                    media_path = entity_base / self.media_subdir
                    cache_path = entity_base / self.cache_subdir
                    monitoring_path = entity_base / self.monitoring_subdir
                else:
                    # Старая организация: entity_name/ с _media подпапкой
                    base_path = self.export_path / target_name
                    export_path = base_path
                    media_path = base_path / f"_{self.media_subdir}"
                    cache_path = base_path
                    monitoring_path = base_path
            else:
                # Плоская структура в корне export_path
                export_path = self.export_path
                media_path = self.export_path / self.media_subdir
                cache_path = self.export_path
                monitoring_path = self.export_path

            self.export_paths[target_id] = export_path.resolve()
            self.media_paths[target_id] = media_path.resolve()
            self.cache_paths[target_id] = cache_path.resolve()
            self.monitoring_paths[target_id] = monitoring_path.resolve()

    def _get_entity_folder_name(self, target: ExportTarget) -> str:
        """Генерация безопасного имени папки для цели экспорта."""
        name = target.name or f"id_{target.id}"
        clean_name = sanitize_filename(name, max_length=100)
        return clean_name

    def _log_configuration(self):
        """Логирование конфигурации для отладки."""

        logger.info(
            f"Configuration loaded with performance profile: {self.performance_profile}"
        )
        logger.info(
            f"Workers: {self.performance.workers}, Download workers: {self.performance.download_workers}"
        )
        logger.info(f"Memory limit: {self.performance.memory_limit_mb}MB")
        logger.info(f"Export path: {self.export_path}")
        logger.info(f"Cache file: {self.cache_file}")

    def add_export_target(self, target: ExportTarget):
        """Добавить новую цель экспорта если её ещё нет."""
        if str(target.id) not in [str(t.id) for t in self.export_targets]:
            self.export_targets.append(target)
            self._update_target_paths()

    def get_export_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Получить путь экспорта для сущности."""
        return self.export_paths.get(str(entity_id), self.export_path)

    def get_media_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Получить путь медиа для сущности."""
        return self.media_paths.get(
            str(entity_id), self.export_path / self.media_subdir
        )

    def get_cache_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Получить путь кэша для сущности."""
        return self.cache_paths.get(
            str(entity_id), self.export_path / self.cache_subdir
        )

    def get_monitoring_path_for_entity(self, entity_id: Union[str, int]) -> Path:
        """Получить путь мониторинга для сущности."""
        return self.monitoring_paths.get(
            str(entity_id), self.export_path / self.monitoring_subdir
        )

    def update_performance_profile(self, profile: PerformanceProfile):
        """Обновить профиль производительности."""
        self.performance_profile = profile
        self.performance = PerformanceSettings.auto_configure(profile)
        logger.info(f"Updated performance profile to: {profile}")

    def validate_target_access(self, target: ExportTarget) -> bool:
        """Валидация доступа к цели экспорта."""
        try:
            # Проверяем существование и доступность экспортного пути
            if not target.export_path:
                logger.error(f"Export path not set for target {target.name}")
                return False
            export_path = Path(target.export_path)
            if not export_path.exists():
                export_path.mkdir(parents=True, exist_ok=True)

            # Проверяем права на запись
            test_file = export_path / ".access_test"
            test_file.touch()
            test_file.unlink()

            return True
        except (PermissionError, OSError) as e:
            logger.error(f"Access validation failed for {target.export_path}: {e}")
            return False

    def estimate_export_size(self) -> Dict[str, Any]:
        """Оценка размера экспорта на основе конфигурации."""
        total_messages = sum(t.estimated_messages or 100 for t in self.export_targets)

        # Базовые оценки (средние значения)
        avg_message_size_kb = 2  # Средний размер текстового сообщения
        avg_media_size_mb = 5  # Средний размер медиа файла
        media_ratio = 0.3  # Примерно 30% сообщений содержат медиа

        # Расчеты
        text_size_mb = (total_messages * avg_message_size_kb) / 1024
        media_count = int(total_messages * media_ratio)
        media_size_mb = media_count * avg_media_size_mb

        # Учитываем настройки производительности
        concurrent_factor = min(self.performance.max_concurrent_downloads / 10, 1.0)
        estimated_duration = (total_messages / (50 * concurrent_factor)) / 60  # минуты

        return {
            "estimated_messages": total_messages,
            "estimated_size_mb": round(text_size_mb + media_size_mb, 1),
            "estimated_duration_minutes": round(max(estimated_duration, 1), 1),
            "estimated_media_files": media_count,
        }

    def to_dict(self) -> dict:
        """Возвращает словарь только с сериализуемыми полями."""
        allowed = {f.name for f in fields(self) if f.init}
        result: Dict[str, Any] = {}
        for k, v in asdict(self).items():
            if k in allowed:
                if k == "export_targets":
                    result[k] = [
                        asdict(t) if hasattr(t, "__dataclass_fields__") else t
                        for t in v
                    ]
                elif k == "performance" and hasattr(v, "__dataclass_fields__"):
                    result[k] = asdict(v)
                elif isinstance(v, Path):
                    result[k] = str(v)
                else:
                    result[k] = v
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        """Создаёт объект Config из словаря."""
        allowed = {f.name for f in fields(cls) if f.init}
        filtered: Dict[str, Any] = {}
        for k, v in d.items():
            if k in allowed:
                if k == "export_targets":
                    filtered[k] = [
                        ExportTarget(**t) if not isinstance(t, ExportTarget) else t
                        for t in v
                    ]
                elif k == "performance" and isinstance(v, dict):
                    filtered[k] = PerformanceSettings(**v)
                elif k == "transcription" and isinstance(v, dict):
                    filtered[k] = TranscriptionConfig(**v)
                elif k in ("export_path", "cache_file") and not isinstance(v, Path):
                    filtered[k] = Path(v)
                else:
                    filtered[k] = v
        return cls(**filtered)

    # Backward compatibility properties for legacy transcription config
    @property
    def enable_transcription(self) -> bool:
        """Backward compatibility: maps to transcription.enabled"""
        return self.transcription.enabled

    @enable_transcription.setter
    def enable_transcription(self, value: bool):
        """Backward compatibility: maps to transcription.enabled"""
        self.transcription.enabled = value

    @property
    def transcription_model(self) -> str:
        """Backward compatibility: always returns 'large-v3' (Whisper Large V3)"""
        return "large-v3"

    @transcription_model.setter
    def transcription_model(self, value: str):
        """Backward compatibility: ignored (only Whisper Large V3 supported)"""
        pass

    @property
    def transcription_language(self) -> Optional[str]:
        """Backward compatibility: maps to transcription.language"""
        return self.transcription.language

    @transcription_language.setter
    def transcription_language(self, value: Optional[str]):
        """Backward compatibility: maps to transcription.language"""
        self.transcription.language = value

    @property
    def transcription_device(self) -> str:
        """Backward compatibility: maps to transcription.device"""
        return self.transcription.device

    @transcription_device.setter
    def transcription_device(self, value: str):
        """Backward compatibility: maps to transcription.device"""
        self.transcription.device = value

    @property
    def transcription_compute_type(self) -> str:
        """Backward compatibility: maps to transcription.compute_type"""
        return self.transcription.compute_type

    @transcription_compute_type.setter
    def transcription_compute_type(self, value: str):
        """Backward compatibility: maps to transcription.compute_type"""
        self.transcription.compute_type = value

    @property
    def transcription_cache_enabled(self) -> bool:
        """Backward compatibility: maps to transcription.cache_enabled"""
        return self.transcription.cache_enabled

    @transcription_cache_enabled.setter
    def transcription_cache_enabled(self, value: bool):
        """Backward compatibility: maps to transcription.cache_enabled"""
        self.transcription.cache_enabled = value

    @classmethod
    def from_env(cls, env_path: Union[str, Path] = ".env") -> "Config":
        """Загружает конфиг из .env и переменных окружения."""
        if Path(env_path).exists():
            load_dotenv(dotenv_path=env_path)

        try:
            # Парсинг основных параметров
            proxy_port_str = os.getenv("PROXY_PORT")
            proxy_port = (
                int(proxy_port_str)
                if proxy_port_str and proxy_port_str.isdigit()
                else None
            )

            # Определение профиля производительности
            performance_profile = os.getenv("PERFORMANCE_PROFILE", "balanced")
            if performance_profile not in [
                "conservative",
                "balanced",
                "aggressive",
                "custom",
            ]:
                logger.warning(
                    f"Unknown performance profile '{performance_profile}', using 'balanced'"
                )
                performance_profile = "balanced"

            config_dict: Dict[str, Any] = {
                "api_id": int(os.getenv("API_ID", 0)),
                "api_hash": os.getenv("API_HASH", ""),
                "phone_number": os.getenv("PHONE_NUMBER"),
                "session_name": os.getenv("SESSION_NAME", "tobs_session"),
                "export_path": os.getenv("EXPORT_PATH"),
                "media_subdir": os.getenv("MEDIA_SUBDIR", "media"),
                "cache_subdir": os.getenv("CACHE_SUBDIR", "cache"),
                "monitoring_subdir": os.getenv("MONITORING_SUBDIR", "monitoring"),
                "use_entity_folders": _parse_bool(
                    os.getenv("USE_ENTITY_FOLDERS"), True
                ),
                "use_structured_export": _parse_bool(
                    os.getenv("USE_STRUCTURED_EXPORT"), True
                ),
                "only_new": _parse_bool(os.getenv("ONLY_NEW"), False),
                "media_download": _parse_bool(os.getenv("MEDIA_DOWNLOAD"), True),
                "export_comments": _parse_bool(os.getenv("EXPORT_COMMENTS"), False),
                "log_level": os.getenv("LOG_LEVEL", "INFO"),
                # Производительность
                "performance_profile": performance_profile,
                # Медиа
                "image_quality": int(os.getenv("IMAGE_QUALITY", 85)),
                "video_crf": int(os.getenv("VIDEO_CRF", 28)),
                "video_preset": os.getenv("VIDEO_PRESET", "fast"),
                "hw_acceleration": os.getenv("HW_ACCELERATION", "vaapi"),
                "use_h265": _parse_bool(os.getenv("USE_H265"), False),
                # Транскрипция (v3.0.0)
                "transcription": TranscriptionConfig(
                    enabled=_parse_bool(os.getenv("TRANSCRIPTION_ENABLED"), True),
                    language=os.getenv("TRANSCRIPTION_LANGUAGE", "ru"),
                    device=os.getenv("TRANSCRIPTION_DEVICE", "auto"),
                    compute_type=os.getenv("TRANSCRIPTION_COMPUTE_TYPE", "auto"),
                    batch_size=int(os.getenv("TRANSCRIPTION_BATCH_SIZE", "8")),
                    duration_threshold=int(
                        os.getenv("TRANSCRIPTION_DURATION_THRESHOLD", "60")
                    ),
                    use_batched=_parse_bool(
                        os.getenv("TRANSCRIPTION_USE_BATCHED"), True
                    ),
                    cache_enabled=_parse_bool(
                        os.getenv("TRANSCRIPTION_CACHE_ENABLED"), True
                    ),
                ),
                # Кэширование
                "cache_file": os.getenv("CACHE_FILE", str(DEFAULT_CACHE_PATH)),
                # Интерактивный режим
                "dialog_fetch_limit": int(os.getenv("DIALOG_FETCH_LIMIT", 20)),
                # Прокси
                "proxy_type": os.getenv("PROXY_TYPE"),
                "proxy_addr": os.getenv("PROXY_ADDR"),
                "proxy_port": proxy_port,
                # Расширенные настройки
                "enable_performance_monitoring": _parse_bool(
                    os.getenv("ENABLE_PERFORMANCE_MONITORING"), True
                ),
                "performance_log_interval": int(
                    os.getenv("PERFORMANCE_LOG_INTERVAL", 60)
                ),
                "max_error_rate": float(os.getenv("MAX_ERROR_RATE", 0.1)),
                "error_cooldown_time": int(os.getenv("ERROR_COOLDOWN_TIME", 300)),
                "max_file_size_mb": int(os.getenv("MAX_FILE_SIZE_MB", 2000)),
                "max_total_size_gb": int(os.getenv("MAX_TOTAL_SIZE_GB", 100)),
            }

            # Парсинг целей экспорта
            export_targets = []
            targets_str = os.getenv("EXPORT_TARGETS", "")
            if targets_str:
                for target_id in [
                    t.strip() for t in targets_str.split(",") if t.strip()
                ]:
                    export_targets.append(ExportTarget(id=target_id))

            config_dict["export_targets"] = export_targets
            return cls(**config_dict)

        except (ValueError, TypeError) as e:
            raise ConfigError(f"Invalid configuration value: {e}") from e


def _parse_bool(value: Optional[Union[str, bool]], default: bool = False) -> bool:
    """Парсинг булевого значения из строки или bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "y", "on")


def get_optimal_workers(
    memory_gb: float, cpu_count: int, profile: PerformanceProfile = "balanced"
) -> Dict[str, int]:
    """
    Вычисляет оптимальное количество workers на основе системных ресурсов.

    Returns:
        Словарь с рекомендуемыми значениями workers
    """
    if profile == "conservative":
        multiplier = 0.5
    elif profile == "aggressive":
        multiplier = 2.0
    else:  # balanced
        multiplier = 1.0

    base_workers = min(int(cpu_count * multiplier), int(memory_gb * 2))

    return {
        "workers": max(2, base_workers),
        "download_workers": max(4, int(base_workers * 1.5)),
        "io_workers": max(4, int(base_workers * 2)),
        "ffmpeg_workers": max(1, base_workers // 2),
    }


def validate_proxy_config(
    proxy_type: Optional[str], proxy_addr: Optional[str], proxy_port: Optional[int]
) -> bool:
    """Валидация конфигурации прокси."""
    if not proxy_type:
        return True  # Прокси не используется

    if proxy_type not in ["socks4", "socks5", "http"]:
        raise ConfigError(f"Unsupported proxy type: {proxy_type}")

    if not proxy_addr:
        raise ConfigError("Proxy address is required when proxy type is specified")

    if not proxy_port or not (1 <= proxy_port <= 65535):
        raise ConfigError(f"Invalid proxy port: {proxy_port}")

    return True


# Экспорт дополнительных утилит для удобства
__all__ = [
    "Config",
    "ExportTarget",
    "PerformanceSettings",
    "PerformanceProfile",
    "HardwareAcceleration",
    "ProxyType",
    "get_optimal_workers",
    "validate_proxy_config",
]
