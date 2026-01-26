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

# Type hints for performance profiles
PerformanceProfile = Literal["conservative", "balanced", "aggressive", "custom"]
HardwareAcceleration = Literal["none", "vaapi", "nvenc", "qsv", "auto"]
ProxyType = Literal["socks4", "socks5", "http"]

# Minimum system requirements
MIN_MEMORY_GB = 2
MIN_FREE_DISK_GB = 1
RECOMMENDED_MEMORY_GB = 8

# ⏱️ Timeouts for async operations
ITER_MESSAGES_TIMEOUT = 300  # 5 minutes for fetching messages
EXPORT_OPERATION_TIMEOUT = (
    7200  # 2 hours for exporting one entity (significantly increased)
)
QUEUE_OPERATION_TIMEOUT = 30  # 30 seconds for getting task from queue
HEALTH_CHECK_TIMEOUT = 10  # 10 seconds for health check
MEDIA_DOWNLOAD_TIMEOUT = 3600  # 1 hour for downloading media


@dataclass(slots=True)
class ExportTarget:
    """
    Представляет цель экспорта (канал, чат или пользователь) с улучшенной типизацией.
    """

    id: Union[str, int]
    name: str = ""
    type: str = "unknown"
    message_id: Optional[int] = None
    start_message_id: Optional[int] = (
        0  # Start export from this message ID (0 = from beginning)
    )

    # New fields for optimization
    estimated_messages: Optional[int] = None  # Estimated number of messages
    last_updated: Optional[float] = None  # Timestamp of last update
    priority: int = 1  # Processing priority (1-10)

    # Fields for working with forum topics
    is_forum: bool = False  # Whether the chat is a forum with topics
    topic_id: Optional[int] = None  # ID of specific topic (if exporting a topic)
    export_all_topics: bool = True  # Export all topics or only specified
    topic_filter: Optional[List[int]] = None  # List of topic IDs to export (if not all)
    export_path: Optional[Path] = None  # Add the missing export_path

    def __post_init__(self):
        """
        Initialize ExportTarget with improved type detection.
        """
        self.id = str(self.id).strip()
        if self.type == "single_post":
            return

        # First check topic links (priority)
        if "/c/" in self.id and "/" in self.id.split("/c/")[-1]:
            # Topic link: https://t.me/c/chat_id/topic_id or /c/chat_id/topic_id
            self.type = "forum_topic"
            try:
                parts = self.id.split("/c/")[-1].split("/")
                if len(parts) >= 2:
                    chat_id, topic_id = parts[0], parts[1]
                    self.id = f"-100{chat_id}"  # Convert to full chat_id
                    self.topic_id = int(topic_id)
                    self.is_forum = True
                    self.export_all_topics = False
            except (ValueError, IndexError):
                logger.warning(f"Could not parse forum topic URL: {self.id}")
            return

        # If type is already correctly set, don't overwrite it
        if self.type in [
            "forum_topic",
            "forum_chat",
            "forum",
            "channel",
            "chat",
            "user",
        ]:
            return

        # Improved entity type detection
        if self.id.startswith("@"):
            self.type = "channel"
        elif "t.me/" in self.id:
            # Regular t.me links (not topics)
            self.type = "channel"
        elif self.id.startswith("-100"):
            self.type = "channel"
        elif self.id.startswith("-") and self.id[1:].isdigit():
            self.type = "chat"
        elif self.id.isdigit():
            self.type = "user"
        else:
            logger.warning(f"Could not determine type for entity ID: {self.id}")


@dataclass(slots=True)
class PerformanceSettings:
    """
    Performance settings with automatic optimization.
    """

    # Core parallelism settings
    workers: int = 8  # General worker count (for backward compatibility)
    
    # 🚀 Media Download Workers (NEW - auto-tuned by disk type)
    media_download_workers: int = 0  # 0 = auto-detect based on disk type (SSD vs HDD)

    # 🧵 Unified Thread Pool settings (TIER B - B-1)
    max_threads: int = 0  # 0 = auto-detect (CPU cores * 1.5)
    thread_pool_metrics_enabled: bool = True  # Enable thread pool metrics collection

    # 🚀 Parallel Media Processing settings (TIER B - B-3)
    parallel_media_processing: bool = True  # Enable parallel media processing
    max_parallel_media: int = (
        0  # Max concurrent media operations (0 = auto: CPU cores / 2)
    )
    parallel_media_memory_limit_mb: int = 2048  # Memory limit for parallel processing

    # 🔐 Hash-Based Media Deduplication settings (TIER B - B-6)
    hash_based_deduplication: bool = (
        True  # Enable hash-based deduplication (content-based)
    )
    hash_cache_max_size: int = 10000  # Maximum hash cache entries (LRU eviction)
    hash_api_timeout: float = 5.0  # Timeout for GetFileHashes API call (seconds)

    # 🗃️ InputPeer Cache settings (TIER C - C-3)
    input_peer_cache_size: int = 1000  # Maximum cached InputPeer entries (LRU eviction)
    input_peer_cache_ttl: float = 3600.0  # Time-to-live for cache entries in seconds

    # 🚀 Zero-Copy Media Transfer settings (TIER B - B-2)
    zero_copy_enabled: bool = True  # Enable zero-copy file transfer (os.sendfile)
    zero_copy_min_size_mb: int = (
        10  # Minimum file size (MB) for zero-copy (smaller → aiofiles)
    )
    zero_copy_verify_copy: bool = True  # Verify file size after copy
    zero_copy_chunk_size_mb: int = 64  # Chunk size (MB) for aiofiles fallback mode

    # Batch processing settings
    message_batch_size: int = 100
    media_batch_size: int = 5
    cache_batch_size: int = 50
    cache_save_interval: int = 2000

    # Forum parallel processing settings
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
    
    def __post_init__(self):
        """Auto-tune performance settings based on system resources."""
        # Auto-detect media download workers if not set
        if self.media_download_workers == 0:
            self.media_download_workers = self._detect_optimal_media_workers()
            logger.info(f"🚀 Auto-tuned media_download_workers: {self.media_download_workers}")
    
    def _detect_optimal_media_workers(self) -> int:
        """
        Detect optimal number of media download workers based on disk type.
        
        Strategy:
        - SSD: 8 workers (can handle parallel I/O efficiently)
        - HDD: 3 workers (sequential I/O preferred to avoid head seeks)
        - Unknown/Error: 6 workers (conservative middle ground)
        
        Returns:
            Optimal worker count for media downloads
        """
        try:
            disk_type = self._detect_disk_type()
            
            if disk_type == "ssd":
                return 8  # SSDs handle parallel I/O well
            elif disk_type == "hdd":
                return 3  # HDDs suffer from head seeks, keep sequential
            else:
                return 6  # Unknown, use middle ground
        except Exception as e:
            logger.warning(f"Failed to detect disk type: {e}, using default 6 workers")
            return 6
    
    def _detect_disk_type(self) -> str:
        """
        Detect if primary disk is SSD or HDD.
        
        Method (Linux): Check /sys/block/*/queue/rotational
        - 0 = SSD (non-rotational)
        - 1 = HDD (rotational)
        
        Returns:
            "ssd", "hdd", or "unknown"
        """
        try:
            # Linux detection
            if os.path.exists("/sys/block"):
                # Find primary disk (sda, nvme0n1, etc.)
                import glob
                rotational_files = glob.glob("/sys/block/*/queue/rotational")
                
                if rotational_files:
                    # Check first available disk
                    with open(rotational_files[0], "r") as f:
                        value = f.read().strip()
                        if value == "0":
                            logger.debug(f"🔍 Detected SSD (rotational={value})")
                            return "ssd"
                        else:
                            logger.debug(f"🔍 Detected HDD (rotational={value})")
                            return "hdd"
            
            # Windows detection (via psutil)
            try:
                import psutil
                # On Windows, we can't easily detect disk type
                # Assume SSD if system has > 8GB RAM (heuristic)
                mem_gb = psutil.virtual_memory().total / (1024 ** 3)
                if mem_gb > 8:
                    logger.debug("🔍 Assuming SSD (modern system with >8GB RAM)")
                    return "ssd"
                else:
                    logger.debug("🔍 Assuming HDD (older system)")
                    return "hdd"
            except:
                pass
            
            logger.debug("🔍 Could not detect disk type")
            return "unknown"
        
        except Exception as e:
            logger.debug(f"🔍 Disk detection error: {e}")
            return "unknown"
    persistent_max_failures: int = (
        30  # Максимум неудач подряд перед отказом (увеличено)
    )
    persistent_chunk_timeout: int = (
        1200  # Базовый таймаут для частей (20 минут, увеличено)
    )

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

    # Download settings
    part_size_kb: int = 512  # 0 = auto-tuning based on file size
    download_retries: int = 5

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
                media_download_workers=3,  # Conservative for HDD
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
                persistent_max_failures=25,  # Увеличено для DC migration
                persistent_chunk_timeout=1500,  # 25 минут для медленных соединений
                # Параллельные загрузки отключены для надежности
                enable_parallel_download=False,
                max_parallel_connections=4,
                max_concurrent_downloads=1,
            )

        elif profile == "balanced":
            return cls(
                workers=min(8, cpu_count),
                media_download_workers=0,  # Auto-detect
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
                persistent_max_failures=30,  # Увеличено
                persistent_chunk_timeout=1200,  # 20 минут
                # Параллельные загрузки отключены для надежности
                enable_parallel_download=False,
                max_parallel_connections=8,
                max_concurrent_downloads=2,
            )

        elif profile == "aggressive":
            return cls(
                workers=min(16, cpu_count * 2),
                media_download_workers=12,  # Aggressive for SSD
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
                persistent_max_failures=35,  # Увеличено
                persistent_chunk_timeout=1200,  # 20 минут
                enable_parallel_download=True,
                max_parallel_connections=12,
                max_concurrent_downloads=3,
            )

        else:  # custom - возвращаем defaults
            return cls()


@dataclass(slots=True)
class TranscriptionConfig:
    """
    Configuration for audio transcription system.

    Version: 5.2.0 - Added cache_dir configuration
    """

    # Basic settings
    enabled: bool = True
    language: Optional[str] = "ru"  # Default language for transcription
    device: str = "auto"  # 'auto', 'cuda', 'cpu', 'cuda:0'

    # Whisper settings
    compute_type: str = "auto"  # 'auto', 'int8', 'float16', 'float32'
    batch_size: int = 8  # Batch size for batched inference
    duration_threshold: int = 60  # Seconds threshold for batched mode
    use_batched: bool = True  # Enable batched inference

    # Caching
    cache_enabled: bool = True  # Enable result caching
    cache_dir: Optional[str] = (
        None  # Cache directory (default: {export_path}/.cache/transcriptions)
    )

    # Parallelism settings (v5.1.0)
    max_concurrent: int = 2  # Max parallel transcriptions (0 = auto based on device)
    sorting: str = "size_asc"  # 'none', 'size_asc', 'size_desc' (LPT scheduling)


@dataclass(slots=True)
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
    # Media download settings - granular control
    download_photos: bool = True  # Download photos and images
    download_videos: bool = True  # Download videos
    download_audio: bool = True  # Download audio files
    download_other: bool = True  # Download stickers, documents, and other media
    # Backward compatibility - deprecated, will be removed in future version
    media_download: bool = True

    # Extension filtering (tdl-style)
    include_extensions: List[str] = field(default_factory=list)
    exclude_extensions: List[str] = field(default_factory=list)

    export_comments: bool = False  # Export comments for posts
    export_reactions: bool = False  # Export message reactions

    # Takeout settings
    use_takeout: bool = False  # Use Telegram Takeout for export
    takeout_fallback_delay: float = 1.0  # Delay in seconds if Takeout fails/disabled

    # Async pipeline configuration (fetch -> process -> write)
    # Feature flag to enable async pipeline - ON by default after TIER A completion
    async_pipeline_enabled: bool = True

    # Worker sizing for the pipeline stages (0 = auto in some cases)
    async_pipeline_fetch_workers: int = 1
    async_pipeline_process_workers: int = (
        0  # 0 = auto (derived from performance settings)
    )
    async_pipeline_write_workers: int = 1

    # Bounded queue sizes for pipeline stages (sensible defaults)
    async_pipeline_fetch_queue_size: int = 64
    async_pipeline_process_queue_size: int = 256

    # DC-aware routing settings (P1)
    dc_aware_routing_enabled: bool = (
        True  # Enable DC-aware worker routing (ON by default)
    )
    dc_routing_strategy: str = "smart"  # 'smart' | 'sticky' | 'round_robin'
    dc_prewarm_enabled: bool = True  # Pre-warm workers to entity DC before heavy fetch
    dc_prewarm_timeout: int = 5  # Timeout (seconds) for pre-warm RPCs

    # Session garbage collection (TIER A - Task 6)
    session_gc_enabled: bool = True  # Enable automatic session cleanup on startup
    session_gc_max_age_days: int = 30  # Remove sessions older than N days
    session_gc_keep_last_n: int = 3  # Always keep N most recent sessions

    # BloomFilter optimization (TIER B - B-4)
    bloom_filter_size_multiplier: float = (
        1.1  # Multiplier for expected message count (10% buffer)
    )
    bloom_filter_min_size: int = (
        10_000  # Minimum size (prevents over-allocation for small chats, ~120KB)
    )
    bloom_filter_max_size: int = (
        10_000_000  # Maximum size (prevents excessive memory, ~12MB)
    )
    bloom_filter_only_for_resume: bool = (
        True  # 🚀 OPTIMIZATION: Only use BloomFilter for resume scenarios (default: True)
        # When True: new exports use lightweight empty set (near-zero overhead)
        # When False: always use BloomFilter (original B-4 behavior, ~7ms per batch)
    )

    # TTY-Aware Modes (TIER B - B-5)
    tty_mode: str = "auto"  # TTY detection mode: 'auto' | 'force-tty' | 'force-non-tty'

    @property
    def any_media_download_enabled(self) -> bool:
        """Check if any type of media download is enabled."""
        return (
            self.download_photos
            or self.download_videos
            or self.download_audio
            or self.download_other
        )

    # Media processing settings
    process_video: bool = False  # По умолчанию выключено (как в MediaProcessor)
    process_audio: bool = True  # По умолчанию включено
    process_images: bool = True  # По умолчанию включено
    deferred_processing: bool = (
        True  # 🚀 NEW: Process media AFTER export to save CPU/Network bandwidth
    )

    # 🚀 Async media download - downloads happen in background, don't block message export
    async_media_download: bool = True  # Enable background download queue
    async_download_workers: int = (
        0  # 0 = auto (derived from performance.download_workers)
    )

    # Audio transcription settings (v3.0.0)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    transcription_timeout: float = 1800.0  # 30 minutes for individual transcription

    # Forum export settings
    export_pinned_topics_first: bool = (
        True  # Экспортировать закрепленные топики первыми
    )
    topic_message_limit: Optional[int] = (
        None  # Лимит сообщений на топик (None = без лимита)
    )
    create_topic_summaries: bool = True  # Создавать summary файлы для топиков
    forum_structure_mode: str = "by_topic"  # "by_topic" или "flat"

    # Lazy loading settings
    enable_lazy_loading: bool = False  # Enable lazy loading for media and content
    lazy_media_metadata_dir: str = "lazy_metadata"  # Directory for lazy media metadata
    lazy_topic_pagination: bool = False  # Enable topic pagination for forums
    lazy_topic_page_size: int = 50  # Number of topics per page when paginated
    lazy_message_pagination: bool = False  # Enable message pagination for large chats
    lazy_message_page_size: int = 1000  # Number of messages per page when paginated
    lazy_preview_mode: bool = False  # Enable preview mode with limited messages
    lazy_preview_limit: int = 100  # Number of messages to load in preview mode

    performance_profile: PerformanceProfile = "balanced"
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)

    # Параметры шардирования (параллельная загрузка сообщений)
    enable_shard_fetch: bool = False  # TEMPORARY DISABLED: Serialization issues with Message objects
    shard_count: int = 8  # Количество воркеров для шардирования
    shard_chunk_size: int = 1000  # Размер батча (лимит Takeout API)
    shard_chunks_multiplier: int = (
        4  # NEW: Множитель чанков (4x workers = 32 chunks для 8 workers)
    )
    shard_compression_enabled: bool = True  # Enable zlib compression for shards
    shard_compression_level: int = 1  # Compression level (1-9, 1=fastest)
    shard_lightweight_schema_enabled: bool = (
        False  # Enable lightweight schema for shards (only minimal message data)
    )

    # Adaptive chunking for slow regions (DC/API bottleneck mitigation)
    slow_chunk_threshold: float = (
        10.0  # Seconds - chunks slower than this trigger adaptive splitting
    )
    slow_chunk_max_retries: int = 2  # Maximum recursive split attempts for slow chunks
    slow_chunk_split_factor: int = (
        4  # How many pieces to split slow chunks into (default: 4)
    )

    # Hot Zones & Density-Based Adaptive Chunking
    enable_hot_zones: bool = (
        True  # Enable pre-defined hot zone detection and pre-splitting
    )
    enable_density_estimation: bool = True  # Enable density estimation via sampling
    hot_zones_db_path: Optional[str] = (
        None  # Custom path for slow-ranges database (default: .monitoring/)
    )

    # Prefetch optimization (overlap network fetch with processing)
    enable_prefetch_batches: bool = True  # Enable batch prefetching (default: ON)
    prefetch_queue_size: int = 2  # Max batches to prefetch (2 = double-buffering)
    prefetch_batch_size: int = 100  # Messages per batch for prefetch

    # Density thresholds for adaptive chunking (messages per 1000 IDs)
    density_very_high_threshold: float = 150.0  # >150 msgs/1K IDs = very high density
    density_high_threshold: float = 100.0  # >100 msgs/1K IDs = high density
    density_medium_threshold: float = 50.0  # >50 msgs/1K IDs = medium density

    # Chunk sizes for different density levels (in message IDs)
    chunk_size_very_high_density: int = 5_000  # Very high density → small chunks
    chunk_size_high_density: int = 10_000  # High density → medium-small chunks
    chunk_size_medium_density: int = 15_000  # Medium density → medium chunks
    chunk_size_low_density: int = 50_000  # Low density → large chunks (default)

    # Density estimation sampling parameters
    density_sample_points: int = 3  # Number of sample points across ID range
    density_sample_range: int = 1_000  # IDs to sample around each point

    image_quality: int = 85
    video_crf: int = 28
    video_preset: str = "fast"
    hw_acceleration: HardwareAcceleration = "vaapi"
    use_h265: bool = False
    compress_video: bool = True  # Сжатие видео включено по умолчанию с VA-API
    vaapi_device: str = "/dev/dri/renderD128"  # Устройство VA-API
    vaapi_quality: int = 25  # Качество для VA-API (18-28 для h264, 25-35 для hevc)

    # TIER C-1: VA-API Auto-Detection
    force_cpu_transcode: bool = False  # Override auto-detection, force CPU encoding
    vaapi_device_path: str = "/dev/dri/renderD128"  # Path to VA-API device

    cache_file: Path = field(default=DEFAULT_CACHE_PATH)
    cache_manager: Any = None

    log_level: str = "INFO"

    dialog_fetch_limit: int = 20
    batch_fetch_size: int = 100

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

        # 🚀 Override performance settings from env if present
        env_part_size = os.getenv("PART_SIZE_KB")
        if env_part_size is not None:
            self.performance.part_size_kb = int(env_part_size)

        env_retries = os.getenv("DOWNLOAD_RETRIES")
        if env_retries is not None:
            self.performance.download_retries = int(env_retries)

        # 🗃️ InputPeer Cache settings override (TIER C - C-3)
        env_cache_size = os.getenv("INPUT_PEER_CACHE_SIZE")
        if env_cache_size is not None:
            self.performance.input_peer_cache_size = int(env_cache_size)

        env_cache_ttl = os.getenv("INPUT_PEER_CACHE_TTL")
        if env_cache_ttl is not None:
            self.performance.input_peer_cache_ttl = float(env_cache_ttl)

        # 🚀 Auto-configure async_download_workers from performance settings
        if self.async_download_workers == 0:
            # Derive from performance.media_download_workers (typically 1/3 to avoid overwhelming Telegram)
            self.async_download_workers = max(3, self.performance.media_download_workers // 3)

        # Если путь к кэшу не абсолютный — делаем его относительным к export_path
        if not Path(self.cache_file).is_absolute():
            self.cache_file = Path(self.export_path) / Path(self.cache_file).name

        # Если путь к кэшу транскрипций не задан — делаем относительным к export_path
        if self.transcription.cache_dir is None:
            self.transcription.cache_dir = str(
                Path(self.export_path) / ".cache" / "transcriptions"
            )
        elif not Path(self.transcription.cache_dir).is_absolute():
            self.transcription.cache_dir = str(
                Path(self.export_path) / self.transcription.cache_dir
            )

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
            f"Workers: {self.performance.workers}, Media download workers: {self.performance.media_download_workers}"
        )
        logger.info(f"Async download workers: {self.async_download_workers}")
        logger.info(f"Memory limit: {self.performance.memory_limit_mb}MB")
        logger.info(f"Export path: {self.export_path}")
        logger.info(f"Cache file: {self.cache_file}")
        logger.info(f"Transcription cache: {self.transcription.cache_dir}")
        logger.info(f"Takeout enabled: {self.use_takeout}")
        logger.info(f"Async media download: {self.async_media_download}")
        logger.info(
            f"🚀 Sharding: enabled={self.enable_shard_fetch}, workers={self.shard_count}, chunk_size={self.shard_chunk_size}"
        )
        logger.info(
            f"⚡ Prefetch: enabled={self.enable_prefetch_batches}, "
            f"queue_size={self.prefetch_queue_size}, batch_size={self.prefetch_batch_size}"
        )
        if self.enable_hot_zones:
            logger.info(
                f"🔥 Hot Zones: enabled, density estimation={self.enable_density_estimation}, "
                f"thresholds=[{self.density_medium_threshold}, {self.density_high_threshold}, {self.density_very_high_threshold}]"
            )
            logger.info(
                f"   Chunk sizes: low={self.chunk_size_low_density}, med={self.chunk_size_medium_density}, "
                f"high={self.chunk_size_high_density}, very_high={self.chunk_size_very_high_density}"
            )

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
        if profile not in ["conservative", "balanced", "aggressive", "custom"]:
            logger.warning(f"Unknown performance profile '{profile}', using 'balanced'")
            profile = "balanced"

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
                "export_path": os.getenv("EXPORT_PATH") or str(DEFAULT_EXPORT_PATH),
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
                "download_photos": _parse_bool(os.getenv("DOWNLOAD_PHOTOS"), True),
                "download_videos": _parse_bool(os.getenv("DOWNLOAD_VIDEOS"), True),
                "download_audio": _parse_bool(os.getenv("DOWNLOAD_AUDIO"), True),
                "download_other": _parse_bool(os.getenv("DOWNLOAD_OTHER"), True),
                "media_download": _parse_bool(
                    os.getenv("MEDIA_DOWNLOAD"), True
                ),  # Backward compatibility
                "export_comments": _parse_bool(os.getenv("EXPORT_COMMENTS"), False),
                "export_reactions": _parse_bool(os.getenv("EXPORT_REACTIONS"), False),
                "use_takeout": _parse_bool(os.getenv("USE_TAKEOUT"), False),
                "takeout_fallback_delay": float(
                    os.getenv("TAKEOUT_FALLBACK_DELAY", "1.0")
                ),
                # Async media download
                "async_media_download": _parse_bool(
                    os.getenv("ASYNC_MEDIA_DOWNLOAD"), True
                ),
                "async_download_workers": int(
                    os.getenv("ASYNC_DOWNLOAD_WORKERS", "0")
                ),  # 0 = auto
                # Async pipeline configuration (fetch -> process -> write)
                "async_pipeline_enabled": _parse_bool(
                    os.getenv("ASYNC_PIPELINE_ENABLED"), False
                ),
                "async_pipeline_fetch_workers": int(
                    os.getenv("ASYNC_PIPELINE_FETCH_WORKERS", "1")
                ),
                "async_pipeline_process_workers": int(
                    os.getenv("ASYNC_PIPELINE_PROCESS_WORKERS", "0")
                ),  # 0 = auto
                "async_pipeline_write_workers": int(
                    os.getenv("ASYNC_PIPELINE_WRITE_WORKERS", "1")
                ),
                "async_pipeline_fetch_queue_size": int(
                    os.getenv("ASYNC_PIPELINE_FETCH_QUEUE_SIZE", "64")
                ),
                "async_pipeline_process_queue_size": int(
                    os.getenv("ASYNC_PIPELINE_PROCESS_QUEUE_SIZE", "256")
                ),
                # BloomFilter optimization (TIER B - B-4)
                "bloom_filter_size_multiplier": float(
                    os.getenv("BLOOM_FILTER_SIZE_MULTIPLIER", "1.1")
                ),
                "bloom_filter_min_size": int(
                    os.getenv("BLOOM_FILTER_MIN_SIZE", "10000")
                ),
                "bloom_filter_max_size": int(
                    os.getenv("BLOOM_FILTER_MAX_SIZE", "10000000")
                ),
                # TTY-Aware Modes (TIER B - B-5)
                "tty_mode": os.getenv("TTY_MODE", "auto"),
                # DC-aware routing (P1)
                "dc_aware_routing_enabled": _parse_bool(
                    os.getenv("DC_AWARE_ROUTING_ENABLED"), False
                ),
                "dc_routing_strategy": os.getenv("DC_ROUTING_STRATEGY", "smart"),
                "dc_prewarm_enabled": _parse_bool(
                    os.getenv("DC_PREWARM_ENABLED"), True
                ),
                "dc_prewarm_timeout": int(os.getenv("DC_PREWARM_TIMEOUT", "5")),
                "log_level": os.getenv("LOG_LEVEL", "INFO"),
                # Производительность
                "performance_profile": performance_profile,
                # Шардирование (параллельная загрузка сообщений, только Этап 1)
                "enable_shard_fetch": _parse_bool(
                    os.getenv("ENABLE_SHARD_FETCH"), False
                ),
                "shard_count": int(os.getenv("SHARD_COUNT", "8")),
                "shard_chunk_size": int(os.getenv("SHARD_CHUNK_SIZE", "1000")),
                "shard_chunks_multiplier": int(
                    os.getenv("SHARD_CHUNKS_MULTIPLIER", "4")
                ),
                "shard_compression_enabled": _parse_bool(
                    os.getenv("SHARD_COMPRESSION_ENABLED"), True
                ),
                "shard_compression_level": int(
                    os.getenv("SHARD_COMPRESSION_LEVEL", "1")
                ),
                "shard_lightweight_schema_enabled": _parse_bool(
                    os.getenv("SHARD_LIGHTWEIGHT_SCHEMA_ENABLED"), False
                ),
                # Adaptive chunking parameters
                "slow_chunk_threshold": float(
                    os.getenv("SLOW_CHUNK_THRESHOLD", "10.0")
                ),
                "slow_chunk_max_retries": int(os.getenv("SLOW_CHUNK_MAX_RETRIES", "2")),
                "slow_chunk_split_factor": int(
                    os.getenv("SLOW_CHUNK_SPLIT_FACTOR", "4")
                ),
                # Hot Zones & Density-Based Adaptive Chunking
                "enable_hot_zones": _parse_bool(os.getenv("ENABLE_HOT_ZONES"), True),
                "enable_density_estimation": _parse_bool(
                    os.getenv("ENABLE_DENSITY_ESTIMATION"), True
                ),
                "hot_zones_db_path": os.getenv("HOT_ZONES_DB_PATH"),
                "density_very_high_threshold": float(
                    os.getenv("DENSITY_VERY_HIGH_THRESHOLD", "150.0")
                ),
                "density_high_threshold": float(
                    os.getenv("DENSITY_HIGH_THRESHOLD", "100.0")
                ),
                "density_medium_threshold": float(
                    os.getenv("DENSITY_MEDIUM_THRESHOLD", "50.0")
                ),
                "chunk_size_very_high_density": int(
                    os.getenv("CHUNK_SIZE_VERY_HIGH_DENSITY", "5000")
                ),
                "chunk_size_high_density": int(
                    os.getenv("CHUNK_SIZE_HIGH_DENSITY", "10000")
                ),
                "chunk_size_medium_density": int(
                    os.getenv("CHUNK_SIZE_MEDIUM_DENSITY", "15000")
                ),
                # Prefetch optimization
                "enable_prefetch_batches": _parse_bool(
                    os.getenv("ENABLE_PREFETCH_BATCHES"), True
                ),
                "prefetch_queue_size": int(os.getenv("PREFETCH_QUEUE_SIZE", "2")),
                "prefetch_batch_size": int(os.getenv("PREFETCH_BATCH_SIZE", "100")),
                "chunk_size_low_density": int(
                    os.getenv("CHUNK_SIZE_LOW_DENSITY", "50000")
                ),
                "density_sample_points": int(os.getenv("DENSITY_SAMPLE_POINTS", "3")),
                "density_sample_range": int(os.getenv("DENSITY_SAMPLE_RANGE", "1000")),
                # Медиа
                "image_quality": int(os.getenv("IMAGE_QUALITY", 85)),
                "video_crf": int(os.getenv("VIDEO_CRF", 28)),
                "video_preset": os.getenv("VIDEO_PRESET", "fast"),
                "hw_acceleration": os.getenv("HW_ACCELERATION", "vaapi"),
                "use_h265": _parse_bool(os.getenv("USE_H265"), False),
                # TIER C-1: VA-API Auto-Detection
                "force_cpu_transcode": _parse_bool(
                    os.getenv("FORCE_CPU_TRANSCODE"), False
                ),
                "vaapi_device_path": os.getenv(
                    "VAAPI_DEVICE_PATH", "/dev/dri/renderD128"
                ),
                # Транскрипция (v5.1.0)
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
                    cache_dir=os.getenv(
                        "TRANSCRIPTION_CACHE_DIR"
                    ),  # None = auto ({export_path}/.cache/transcriptions)
                    max_concurrent=int(os.getenv("TRANSCRIPTION_MAX_CONCURRENT", "2")),
                    sorting=os.getenv("TRANSCRIPTION_SORTING", "size_asc"),
                ),
                "transcription_timeout": float(
                    os.getenv("TRANSCRIPTION_TIMEOUT", "1800.0")
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
    
    DEPRECATED: Use PerformanceSettings.auto_configure() instead.
    Kept for backward compatibility.

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
        "media_download_workers": max(4, int(base_workers * 1.5)),
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
