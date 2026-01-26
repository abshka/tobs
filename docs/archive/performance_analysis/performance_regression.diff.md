diff --git i/src/config.py w/src/config.py
index 8f6204e..45bf0c5 100644
--- i/src/config.py
+++ w/src/config.py
@@ -32,7 +32,7 @@ HEALTH_CHECK_TIMEOUT = 10  # 10 seconds for health check
 MEDIA_DOWNLOAD_TIMEOUT = 3600  # 1 hour for downloading media
 
 
-@dataclass
+@dataclass(slots=True)
 class ExportTarget:
     """
     Представляет цель экспорта (канал, чат или пользователь) с улучшенной типизацией.
@@ -55,9 +55,7 @@ class ExportTarget:
     is_forum: bool = False  # Whether the chat is a forum with topics
     topic_id: Optional[int] = None  # ID of specific topic (if exporting a topic)
     export_all_topics: bool = True  # Export all topics or only specified
-    topic_filter: Optional[List[int]] = (
-        None  # List of topic IDs to export (if not all)
-    )
+    topic_filter: Optional[List[int]] = None  # List of topic IDs to export (if not all)
     export_path: Optional[Path] = None  # Add the missing export_path
 
     def __post_init__(self):
@@ -111,7 +109,7 @@ class ExportTarget:
             logger.warning(f"Could not determine type for entity ID: {self.id}")
 
 
-@dataclass
+@dataclass(slots=True)
 class PerformanceSettings:
     """
     Performance settings with automatic optimization.
@@ -123,11 +121,41 @@ class PerformanceSettings:
     io_workers: int = 16
     ffmpeg_workers: int = 4
 
+    # 🧵 Unified Thread Pool settings (TIER B - B-1)
+    max_threads: int = 0  # 0 = auto-detect (CPU cores * 1.5)
+    thread_pool_metrics_enabled: bool = True  # Enable thread pool metrics collection
+
+    # 🚀 Parallel Media Processing settings (TIER B - B-3)
+    parallel_media_processing: bool = True  # Enable parallel media processing
+    max_parallel_media: int = (
+        0  # Max concurrent media operations (0 = auto: CPU cores / 2)
+    )
+    parallel_media_memory_limit_mb: int = 2048  # Memory limit for parallel processing
+
+    # 🔐 Hash-Based Media Deduplication settings (TIER B - B-6)
+    hash_based_deduplication: bool = (
+        True  # Enable hash-based deduplication (content-based)
+    )
+    hash_cache_max_size: int = 10000  # Maximum hash cache entries (LRU eviction)
+    hash_api_timeout: float = 5.0  # Timeout for GetFileHashes API call (seconds)
+
+    # 🗃️ InputPeer Cache settings (TIER C - C-3)
+    input_peer_cache_size: int = 1000  # Maximum cached InputPeer entries (LRU eviction)
+    input_peer_cache_ttl: float = 3600.0  # Time-to-live for cache entries in seconds
+
+    # 🚀 Zero-Copy Media Transfer settings (TIER B - B-2)
+    zero_copy_enabled: bool = True  # Enable zero-copy file transfer (os.sendfile)
+    zero_copy_min_size_mb: int = (
+        10  # Minimum file size (MB) for zero-copy (smaller → aiofiles)
+    )
+    zero_copy_verify_copy: bool = True  # Verify file size after copy
+    zero_copy_chunk_size_mb: int = 64  # Chunk size (MB) for aiofiles fallback mode
+
     # Batch processing settings
     message_batch_size: int = 100
     media_batch_size: int = 5
     cache_batch_size: int = 50
-    cache_save_interval: int = 100
+    cache_save_interval: int = 2000
 
     # Forum parallel processing settings
     forum_parallel_enabled: bool = True
@@ -193,6 +221,10 @@ class PerformanceSettings:
     throttle_pause_s: int = 30
     rate_limit_calls_per_second: float = 10.0
 
+    # Download settings
+    part_size_kb: int = 512  # 0 = auto-tuning based on file size
+    download_retries: int = 5
+
     @classmethod
     def auto_configure(
         cls, profile: PerformanceProfile = "balanced"
@@ -314,7 +346,7 @@ class PerformanceSettings:
             return cls()
 
 
-@dataclass
+@dataclass(slots=True)
 class TranscriptionConfig:
     """
     Configuration for audio transcription system.
@@ -335,14 +367,16 @@ class TranscriptionConfig:
 
     # Caching
     cache_enabled: bool = True  # Enable result caching
-    cache_dir: Optional[str] = None  # Cache directory (default: {export_path}/.cache/transcriptions)
+    cache_dir: Optional[str] = (
+        None  # Cache directory (default: {export_path}/.cache/transcriptions)
+    )
 
     # Parallelism settings (v5.1.0)
     max_concurrent: int = 2  # Max parallel transcriptions (0 = auto based on device)
     sorting: str = "size_asc"  # 'none', 'size_asc', 'size_desc' (LPT scheduling)
 
 
-@dataclass
+@dataclass(slots=True)
 class Config:
     """
     Основная конфигурация экспортера.
@@ -385,11 +419,54 @@ class Config:
     exclude_extensions: List[str] = field(default_factory=list)
 
     export_comments: bool = False  # Export comments for posts
+    export_reactions: bool = False  # Export message reactions
 
     # Takeout settings
     use_takeout: bool = False  # Use Telegram Takeout for export
     takeout_fallback_delay: float = 1.0  # Delay in seconds if Takeout fails/disabled
 
+    # Async pipeline configuration (fetch -> process -> write)
+    # Feature flag to enable async pipeline - ON by default after TIER A completion
+    async_pipeline_enabled: bool = True
+
+    # Worker sizing for the pipeline stages (0 = auto in some cases)
+    async_pipeline_fetch_workers: int = 1
+    async_pipeline_process_workers: int = (
+        0  # 0 = auto (derived from performance settings)
+    )
+    async_pipeline_write_workers: int = 1
+
+    # Bounded queue sizes for pipeline stages (sensible defaults)
+    async_pipeline_fetch_queue_size: int = 64
+    async_pipeline_process_queue_size: int = 256
+
+    # DC-aware routing settings (P1)
+    dc_aware_routing_enabled: bool = (
+        True  # Enable DC-aware worker routing (ON by default)
+    )
+    dc_routing_strategy: str = "smart"  # 'smart' | 'sticky' | 'round_robin'
+    dc_prewarm_enabled: bool = True  # Pre-warm workers to entity DC before heavy fetch
+    dc_prewarm_timeout: int = 5  # Timeout (seconds) for pre-warm RPCs
+
+    # Session garbage collection (TIER A - Task 6)
+    session_gc_enabled: bool = True  # Enable automatic session cleanup on startup
+    session_gc_max_age_days: int = 30  # Remove sessions older than N days
+    session_gc_keep_last_n: int = 3  # Always keep N most recent sessions
+
+    # BloomFilter optimization (TIER B - B-4)
+    bloom_filter_size_multiplier: float = (
+        1.1  # Multiplier for expected message count (10% buffer)
+    )
+    bloom_filter_min_size: int = (
+        10_000  # Minimum size (prevents over-allocation for small chats, ~120KB)
+    )
+    bloom_filter_max_size: int = (
+        10_000_000  # Maximum size (prevents excessive memory, ~12MB)
+    )
+
+    # TTY-Aware Modes (TIER B - B-5)
+    tty_mode: str = "auto"  # TTY detection mode: 'auto' | 'force-tty' | 'force-non-tty'
+
     @property
     def any_media_download_enabled(self) -> bool:
         """Check if any type of media download is enabled."""
@@ -445,29 +522,44 @@ class Config:
     enable_shard_fetch: bool = False  # Использовать шардирование для загрузки сообщений
     shard_count: int = 8  # Количество воркеров для шардирования
     shard_chunk_size: int = 1000  # Размер батча (лимит Takeout API)
-    shard_chunks_multiplier: int = 4  # NEW: Множитель чанков (4x workers = 32 chunks для 8 workers)
-    
+    shard_chunks_multiplier: int = (
+        4  # NEW: Множитель чанков (4x workers = 32 chunks для 8 workers)
+    )
+    shard_compression_enabled: bool = True  # Enable zlib compression for shards
+    shard_compression_level: int = 1  # Compression level (1-9, 1=fastest)
+    shard_lightweight_schema_enabled: bool = (
+        False  # Enable lightweight schema for shards (only minimal message data)
+    )
+
     # Adaptive chunking for slow regions (DC/API bottleneck mitigation)
-    slow_chunk_threshold: float = 10.0  # Seconds - chunks slower than this trigger adaptive splitting
+    slow_chunk_threshold: float = (
+        10.0  # Seconds - chunks slower than this trigger adaptive splitting
+    )
     slow_chunk_max_retries: int = 2  # Maximum recursive split attempts for slow chunks
-    slow_chunk_split_factor: int = 4  # How many pieces to split slow chunks into (default: 4)
-    
+    slow_chunk_split_factor: int = (
+        4  # How many pieces to split slow chunks into (default: 4)
+    )
+
     # Hot Zones & Density-Based Adaptive Chunking
-    enable_hot_zones: bool = True  # Enable pre-defined hot zone detection and pre-splitting
+    enable_hot_zones: bool = (
+        True  # Enable pre-defined hot zone detection and pre-splitting
+    )
     enable_density_estimation: bool = True  # Enable density estimation via sampling
-    hot_zones_db_path: Optional[str] = None  # Custom path for slow-ranges database (default: .monitoring/)
-    
+    hot_zones_db_path: Optional[str] = (
+        None  # Custom path for slow-ranges database (default: .monitoring/)
+    )
+
     # Density thresholds for adaptive chunking (messages per 1000 IDs)
     density_very_high_threshold: float = 150.0  # >150 msgs/1K IDs = very high density
     density_high_threshold: float = 100.0  # >100 msgs/1K IDs = high density
     density_medium_threshold: float = 50.0  # >50 msgs/1K IDs = medium density
-    
+
     # Chunk sizes for different density levels (in message IDs)
     chunk_size_very_high_density: int = 5_000  # Very high density → small chunks
     chunk_size_high_density: int = 10_000  # High density → medium-small chunks
     chunk_size_medium_density: int = 15_000  # Medium density → medium chunks
     chunk_size_low_density: int = 50_000  # Low density → large chunks (default)
-    
+
     # Density estimation sampling parameters
     density_sample_points: int = 3  # Number of sample points across ID range
     density_sample_range: int = 1_000  # IDs to sample around each point
@@ -481,12 +573,17 @@ class Config:
     vaapi_device: str = "/dev/dri/renderD128"  # Устройство VA-API
     vaapi_quality: int = 25  # Качество для VA-API (18-28 для h264, 25-35 для hevc)
 
+    # TIER C-1: VA-API Auto-Detection
+    force_cpu_transcode: bool = False  # Override auto-detection, force CPU encoding
+    vaapi_device_path: str = "/dev/dri/renderD128"  # Path to VA-API device
+
     cache_file: Path = field(default=DEFAULT_CACHE_PATH)
     cache_manager: Any = None
 
     log_level: str = "INFO"
 
     dialog_fetch_limit: int = 20
+    batch_fetch_size: int = 100
 
     proxy_type: Optional[ProxyType] = None
     proxy_addr: Optional[str] = None
@@ -523,6 +620,24 @@ class Config:
                 self.performance_profile
             )
 
+        # 🚀 Override performance settings from env if present
+        env_part_size = os.getenv("PART_SIZE_KB")
+        if env_part_size is not None:
+            self.performance.part_size_kb = int(env_part_size)
+
+        env_retries = os.getenv("DOWNLOAD_RETRIES")
+        if env_retries is not None:
+            self.performance.download_retries = int(env_retries)
+
+        # 🗃️ InputPeer Cache settings override (TIER C - C-3)
+        env_cache_size = os.getenv("INPUT_PEER_CACHE_SIZE")
+        if env_cache_size is not None:
+            self.performance.input_peer_cache_size = int(env_cache_size)
+
+        env_cache_ttl = os.getenv("INPUT_PEER_CACHE_TTL")
+        if env_cache_ttl is not None:
+            self.performance.input_peer_cache_ttl = float(env_cache_ttl)
+
         # 🚀 Auto-configure async_download_workers from performance settings
         if self.async_download_workers == 0:
             # Derive from performance.download_workers (typically 1/3 to avoid overwhelming Telegram)
@@ -681,7 +796,9 @@ class Config:
         logger.info(f"Transcription cache: {self.transcription.cache_dir}")
         logger.info(f"Takeout enabled: {self.use_takeout}")
         logger.info(f"Async media download: {self.async_media_download}")
-        logger.info(f"🚀 Sharding: enabled={self.enable_shard_fetch}, workers={self.shard_count}, chunk_size={self.shard_chunk_size}")
+        logger.info(
+            f"🚀 Sharding: enabled={self.enable_shard_fetch}, workers={self.shard_count}, chunk_size={self.shard_chunk_size}"
+        )
         if self.enable_hot_zones:
             logger.info(
                 f"🔥 Hot Zones: enabled, density estimation={self.enable_density_estimation}, "
@@ -909,7 +1026,7 @@ class Config:
                 "api_hash": os.getenv("API_HASH", ""),
                 "phone_number": os.getenv("PHONE_NUMBER"),
                 "session_name": os.getenv("SESSION_NAME", "tobs_session"),
-                "export_path": os.getenv("EXPORT_PATH"),
+                "export_path": os.getenv("EXPORT_PATH") or str(DEFAULT_EXPORT_PATH),
                 "media_subdir": os.getenv("MEDIA_SUBDIR", "media"),
                 "cache_subdir": os.getenv("CACHE_SUBDIR", "cache"),
                 "monitoring_subdir": os.getenv("MONITORING_SUBDIR", "monitoring"),
@@ -928,6 +1045,7 @@ class Config:
                     os.getenv("MEDIA_DOWNLOAD"), True
                 ),  # Backward compatibility
                 "export_comments": _parse_bool(os.getenv("EXPORT_COMMENTS"), False),
+                "export_reactions": _parse_bool(os.getenv("EXPORT_REACTIONS"), False),
                 "use_takeout": _parse_bool(os.getenv("USE_TAKEOUT"), False),
                 "takeout_fallback_delay": float(
                     os.getenv("TAKEOUT_FALLBACK_DELAY", "1.0")
@@ -939,6 +1057,46 @@ class Config:
                 "async_download_workers": int(
                     os.getenv("ASYNC_DOWNLOAD_WORKERS", "0")
                 ),  # 0 = auto
+                # Async pipeline configuration (fetch -> process -> write)
+                "async_pipeline_enabled": _parse_bool(
+                    os.getenv("ASYNC_PIPELINE_ENABLED"), False
+                ),
+                "async_pipeline_fetch_workers": int(
+                    os.getenv("ASYNC_PIPELINE_FETCH_WORKERS", "1")
+                ),
+                "async_pipeline_process_workers": int(
+                    os.getenv("ASYNC_PIPELINE_PROCESS_WORKERS", "0")
+                ),  # 0 = auto
+                "async_pipeline_write_workers": int(
+                    os.getenv("ASYNC_PIPELINE_WRITE_WORKERS", "1")
+                ),
+                "async_pipeline_fetch_queue_size": int(
+                    os.getenv("ASYNC_PIPELINE_FETCH_QUEUE_SIZE", "64")
+                ),
+                "async_pipeline_process_queue_size": int(
+                    os.getenv("ASYNC_PIPELINE_PROCESS_QUEUE_SIZE", "256")
+                ),
+                # BloomFilter optimization (TIER B - B-4)
+                "bloom_filter_size_multiplier": float(
+                    os.getenv("BLOOM_FILTER_SIZE_MULTIPLIER", "1.1")
+                ),
+                "bloom_filter_min_size": int(
+                    os.getenv("BLOOM_FILTER_MIN_SIZE", "10000")
+                ),
+                "bloom_filter_max_size": int(
+                    os.getenv("BLOOM_FILTER_MAX_SIZE", "10000000")
+                ),
+                # TTY-Aware Modes (TIER B - B-5)
+                "tty_mode": os.getenv("TTY_MODE", "auto"),
+                # DC-aware routing (P1)
+                "dc_aware_routing_enabled": _parse_bool(
+                    os.getenv("DC_AWARE_ROUTING_ENABLED"), False
+                ),
+                "dc_routing_strategy": os.getenv("DC_ROUTING_STRATEGY", "smart"),
+                "dc_prewarm_enabled": _parse_bool(
+                    os.getenv("DC_PREWARM_ENABLED"), True
+                ),
+                "dc_prewarm_timeout": int(os.getenv("DC_PREWARM_TIMEOUT", "5")),
                 "log_level": os.getenv("LOG_LEVEL", "INFO"),
                 # Производительность
                 "performance_profile": performance_profile,
@@ -948,22 +1106,53 @@ class Config:
                 ),
                 "shard_count": int(os.getenv("SHARD_COUNT", "8")),
                 "shard_chunk_size": int(os.getenv("SHARD_CHUNK_SIZE", "1000")),
-                "shard_chunks_multiplier": int(os.getenv("SHARD_CHUNKS_MULTIPLIER", "4")),
+                "shard_chunks_multiplier": int(
+                    os.getenv("SHARD_CHUNKS_MULTIPLIER", "4")
+                ),
+                "shard_compression_enabled": _parse_bool(
+                    os.getenv("SHARD_COMPRESSION_ENABLED"), True
+                ),
+                "shard_compression_level": int(
+                    os.getenv("SHARD_COMPRESSION_LEVEL", "1")
+                ),
+                "shard_lightweight_schema_enabled": _parse_bool(
+                    os.getenv("SHARD_LIGHTWEIGHT_SCHEMA_ENABLED"), False
+                ),
                 # Adaptive chunking parameters
-                "slow_chunk_threshold": float(os.getenv("SLOW_CHUNK_THRESHOLD", "10.0")),
+                "slow_chunk_threshold": float(
+                    os.getenv("SLOW_CHUNK_THRESHOLD", "10.0")
+                ),
                 "slow_chunk_max_retries": int(os.getenv("SLOW_CHUNK_MAX_RETRIES", "2")),
-                "slow_chunk_split_factor": int(os.getenv("SLOW_CHUNK_SPLIT_FACTOR", "4")),
+                "slow_chunk_split_factor": int(
+                    os.getenv("SLOW_CHUNK_SPLIT_FACTOR", "4")
+                ),
                 # Hot Zones & Density-Based Adaptive Chunking
                 "enable_hot_zones": _parse_bool(os.getenv("ENABLE_HOT_ZONES"), True),
-                "enable_density_estimation": _parse_bool(os.getenv("ENABLE_DENSITY_ESTIMATION"), True),
+                "enable_density_estimation": _parse_bool(
+                    os.getenv("ENABLE_DENSITY_ESTIMATION"), True
+                ),
                 "hot_zones_db_path": os.getenv("HOT_ZONES_DB_PATH"),
-                "density_very_high_threshold": float(os.getenv("DENSITY_VERY_HIGH_THRESHOLD", "150.0")),
-                "density_high_threshold": float(os.getenv("DENSITY_HIGH_THRESHOLD", "100.0")),
-                "density_medium_threshold": float(os.getenv("DENSITY_MEDIUM_THRESHOLD", "50.0")),
-                "chunk_size_very_high_density": int(os.getenv("CHUNK_SIZE_VERY_HIGH_DENSITY", "5000")),
-                "chunk_size_high_density": int(os.getenv("CHUNK_SIZE_HIGH_DENSITY", "10000")),
-                "chunk_size_medium_density": int(os.getenv("CHUNK_SIZE_MEDIUM_DENSITY", "15000")),
-                "chunk_size_low_density": int(os.getenv("CHUNK_SIZE_LOW_DENSITY", "50000")),
+                "density_very_high_threshold": float(
+                    os.getenv("DENSITY_VERY_HIGH_THRESHOLD", "150.0")
+                ),
+                "density_high_threshold": float(
+                    os.getenv("DENSITY_HIGH_THRESHOLD", "100.0")
+                ),
+                "density_medium_threshold": float(
+                    os.getenv("DENSITY_MEDIUM_THRESHOLD", "50.0")
+                ),
+                "chunk_size_very_high_density": int(
+                    os.getenv("CHUNK_SIZE_VERY_HIGH_DENSITY", "5000")
+                ),
+                "chunk_size_high_density": int(
+                    os.getenv("CHUNK_SIZE_HIGH_DENSITY", "10000")
+                ),
+                "chunk_size_medium_density": int(
+                    os.getenv("CHUNK_SIZE_MEDIUM_DENSITY", "15000")
+                ),
+                "chunk_size_low_density": int(
+                    os.getenv("CHUNK_SIZE_LOW_DENSITY", "50000")
+                ),
                 "density_sample_points": int(os.getenv("DENSITY_SAMPLE_POINTS", "3")),
                 "density_sample_range": int(os.getenv("DENSITY_SAMPLE_RANGE", "1000")),
                 # Медиа
@@ -972,6 +1161,13 @@ class Config:
                 "video_preset": os.getenv("VIDEO_PRESET", "fast"),
                 "hw_acceleration": os.getenv("HW_ACCELERATION", "vaapi"),
                 "use_h265": _parse_bool(os.getenv("USE_H265"), False),
+                # TIER C-1: VA-API Auto-Detection
+                "force_cpu_transcode": _parse_bool(
+                    os.getenv("FORCE_CPU_TRANSCODE"), False
+                ),
+                "vaapi_device_path": os.getenv(
+                    "VAAPI_DEVICE_PATH", "/dev/dri/renderD128"
+                ),
                 # Транскрипция (v5.1.0)
                 "transcription": TranscriptionConfig(
                     enabled=_parse_bool(os.getenv("TRANSCRIPTION_ENABLED"), True),
@@ -988,10 +1184,10 @@ class Config:
                     cache_enabled=_parse_bool(
                         os.getenv("TRANSCRIPTION_CACHE_ENABLED"), True
                     ),
-                    cache_dir=os.getenv("TRANSCRIPTION_CACHE_DIR"),  # None = auto ({export_path}/.cache/transcriptions)
-                    max_concurrent=int(
-                        os.getenv("TRANSCRIPTION_MAX_CONCURRENT", "2")
-                    ),
+                    cache_dir=os.getenv(
+                        "TRANSCRIPTION_CACHE_DIR"
+                    ),  # None = auto ({export_path}/.cache/transcriptions)
+                    max_concurrent=int(os.getenv("TRANSCRIPTION_MAX_CONCURRENT", "2")),
                     sorting=os.getenv("TRANSCRIPTION_SORTING", "size_asc"),
                 ),
                 "transcription_timeout": float(
diff --git i/src/core/cache.py w/src/core/cache.py
index 4afb5c6..81cf873 100644
--- i/src/core/cache.py
+++ w/src/core/cache.py
@@ -5,7 +5,7 @@ Unified cache manager combining simple and advanced caching.
 import asyncio
 import base64
 import logging
-import pickle
+import msgpack  # S-3: Security fix - replaced pickle with msgpack
 import time
 import zlib
 from collections import OrderedDict
@@ -33,7 +33,7 @@ class CompressionType(Enum):
 
     NONE = "none"
     GZIP = "gzip"
-    PICKLE = "pickle"
+    MSGPACK = "msgpack"  # S-3: Renamed from PICKLE to MSGPACK
 
 
 @dataclass
@@ -80,6 +80,8 @@ class CacheStats:
 
 
 def _json_default(obj):
+    if hasattr(obj, "to_dict"):
+        return obj.to_dict()
     if isinstance(obj, set):
         return list(obj)
     raise TypeError
@@ -206,29 +208,28 @@ class CacheManager:
             elif isinstance(data, bytes):
                 raw_data = data
             else:
-                # For extraction of raw_data we choose pickled bytes if PICKLE compression is configured,
-                # otherwise we prefer JSON (orjson) which helps decide compression threshold.
-                if self.compression == CompressionType.PICKLE:
-                    raw_data = pickle.dumps(data)
+                # S-3: Use msgpack instead of pickle for object serialization
+                if self.compression == CompressionType.MSGPACK:
+                    raw_data = msgpack.packb(data, use_bin_type=True)
                 else:
                     # Для сложных объектов пытаемся сериализовать в JSON с помощью orjson
                     try:
                         raw_data = orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS)
                     except TypeError as e:
-                        # Not JSON serializable, fallback to pickle for GZIP if configured
+                        # Not JSON serializable, fallback to msgpack for GZIP if configured
                         logger.debug(
-                            f"Data not JSON serializable: {e}. Falling back to pickle compression."
+                            f"Data not JSON serializable: {e}. Falling back to msgpack compression."
                         )
                         if self.compression == CompressionType.GZIP:
-                            pickled_raw = pickle.dumps(data)
-                            # Track that we fell back from JSON to pickle serialization
+                            msgpack_raw = msgpack.packb(data, use_bin_type=True)
+                            # Track that we fell back from JSON to msgpack serialization
                             self._stats.compression_fallbacks += 1
-                            if len(pickled_raw) < self.compression_threshold:
+                            if len(msgpack_raw) < self.compression_threshold:
                                 return data, False, "none"
-                            compressed = zlib.compress(pickled_raw)
-                            if len(compressed) < len(pickled_raw) * 0.9:
+                            compressed = zlib.compress(msgpack_raw)
+                            if len(compressed) < len(msgpack_raw) * 0.9:
                                 self._stats.compression_saves += 1
-                                return compressed, True, "pickle"
+                                return compressed, True, "msgpack"
                         return data, False, "none"
 
             if len(raw_data) < self.compression_threshold:
@@ -239,12 +240,12 @@ class CacheManager:
                 if len(compressed) < len(raw_data) * 0.9:  # Сжатие эффективно
                     self._stats.compression_saves += 1
                     return compressed, True, "gzip"
-            elif self.compression == CompressionType.PICKLE:
-                pickled_raw = pickle.dumps(data)
-                compressed = zlib.compress(pickled_raw)
-                # For PICKLE compression we always store pickled bytes to preserve types
+            elif self.compression == CompressionType.MSGPACK:
+                msgpack_raw = msgpack.packb(data, use_bin_type=True)
+                compressed = zlib.compress(msgpack_raw)
+                # For MSGPACK compression we always store msgpack bytes to preserve types
                 self._stats.compression_saves += 1
-                return compressed, True, "pickle"
+                return compressed, True, "msgpack"
 
         except Exception as e:
             logger.warning(f"Compression failed: {e}")
@@ -268,7 +269,14 @@ class CacheManager:
                 except orjson.JSONDecodeError:
                     # Если не JSON, возвращаем как строку
                     return decompressed_bytes.decode("utf-8")
+            elif compression_type == "msgpack":
+                # S-3: Security fix - use msgpack instead of pickle
+                return msgpack.unpackb(zlib.decompress(data), raw=False)
             elif compression_type == "pickle":
+                # S-3: Backward compatibility - support old pickle data
+                # TODO: Remove after migration period
+                logger.warning("Found legacy pickle data, consider re-caching with msgpack")
+                import pickle
                 return pickle.loads(zlib.decompress(data))
         except Exception as e:
             logger.error(f"Decompression failed: {e}")
@@ -680,6 +688,15 @@ class CacheManager:
         """Принудительное сохранение."""
         await self._save_cache()
 
+    async def get_file_path(self, file_key: str) -> Optional[str]:
+        """Get stored file path for media deduplication."""
+        return await self.get(f"media_file_{file_key}")
+
+    async def store_file_path(self, file_key: str, path: str):
+        """Store file path for media deduplication."""
+        await self.set(f"media_file_{file_key}", path)
+
+
 
 # Глобальный экземпляр кэш-менеджера
 _cache_manager: Optional[CacheManager] = None
diff --git i/src/core/performance.py w/src/core/performance.py
index 2590a02..8c3f246 100644
--- i/src/core/performance.py
+++ w/src/core/performance.py
@@ -45,7 +45,7 @@ class AdaptationStrategy(Enum):
     AGGRESSIVE = "aggressive"
 
 
-@dataclass
+@dataclass(slots=True)
 class SystemMetrics:
     """Системные метрики."""
 
@@ -63,7 +63,7 @@ class SystemMetrics:
     open_files: int
 
 
-@dataclass
+@dataclass(slots=True)
 class PerformanceAlert:
     """Алерт производительности."""
 
@@ -78,7 +78,7 @@ class PerformanceAlert:
     resolved_at: Optional[float] = None
 
 
-@dataclass
+@dataclass(slots=True)
 class PerformanceProfile:
     """Профиль производительности."""
 
@@ -94,7 +94,7 @@ class PerformanceProfile:
     cpu_limit_percent: Optional[float] = None
 
 
-@dataclass
+@dataclass(slots=True)
 class ComponentStats:
     """Статистика компонента."""
 
diff --git i/src/export/exporter.py w/src/export/exporter.py
index 986383b..df61f7e 100644
--- i/src/export/exporter.py
+++ w/src/export/exporter.py
@@ -43,16 +43,16 @@ from .pipeline import AsyncPipeline
 # ============================================================================
 
 # Prefetch pipeline settings
-PREFETCH_BATCH_SIZE = int(os.getenv("PREFETCH_BATCH_SIZE", "100"))  # Telegram API max
+PREFETCH_BATCH_SIZE = int(os.getenv("PREFETCH_BATCH_SIZE", "200"))  # Increased from 100 for better throughput
 PREFETCH_LOOKAHEAD = int(os.getenv("PREFETCH_LOOKAHEAD", "2"))  # Double-buffering
 
 # LRU cache for sender names
 SENDER_CACHE_MAX_SIZE = int(os.getenv("SENDER_CACHE_SIZE", "10000"))
 
-# AsyncBufferedSaver buffer size (512KB default for better SSD performance)
-# For NVMe SSD: can increase to 1MB (1048576) for even better throughput
+# AsyncBufferedSaver buffer size (1MB default for NVMe SSD performance)
+# For NVMe SSD: 1MB (1048576) provides excellent throughput
 # For HDD: 256KB (262144) may be more efficient
-EXPORT_BUFFER_SIZE = int(os.getenv("EXPORT_BUFFER_SIZE", "524288"))  # 512KB
+EXPORT_BUFFER_SIZE = int(os.getenv("EXPORT_BUFFER_SIZE", "1048576"))  # 1MB (increased from 512KB)
 
 # Media file copy chunk size (8MB for large media files)
 # Larger chunks = fewer syscalls but more memory per operation
@@ -282,6 +282,9 @@ class AsyncBufferedSaver:
 
     Buffer size is configurable via EXPORT_BUFFER_SIZE env var (default: 512KB).
     Larger buffers reduce syscalls but use more memory.
+    
+    Security S-4: Implements atomic writes using tmp + rename pattern to prevent data corruption
+    on crash/interruption. Writes to .tmp file first, then atomically renames to final file.
     """
 
     def __init__(
@@ -294,15 +297,40 @@ class AsyncBufferedSaver:
         self._buffer = []
         self._current_size = 0
         self._file = None
+        # S-4: Atomic write support - write to .tmp first
+        self._tmp_path = f"{path}.tmp"
+        self._finalized = False
 
     async def __aenter__(self):
-        self._file = await aiofiles.open(self.path, self.mode, encoding=self.encoding)
+        # S-4: Write to temporary file first for atomicity
+        self._file = await aiofiles.open(self._tmp_path, self.mode, encoding=self.encoding)
         return self
 
     async def __aexit__(self, exc_type, exc_val, exc_tb):
         await self.flush()
         if self._file:
             await self._file.close()
+        
+        # S-4: Atomic rename on success, cleanup on failure
+        if exc_type is None:
+            # Success: atomically rename tmp -> final
+            try:
+                await aiofiles.os.rename(self._tmp_path, self.path)
+                self._finalized = True
+            except Exception as e:
+                logger.error(f"Failed to finalize file {self.path}: {e}")
+                # Cleanup tmp file on rename failure
+                try:
+                    await aiofiles.os.remove(self._tmp_path)
+                except Exception:
+                    pass
+                raise
+        else:
+            # Failure: cleanup tmp file
+            try:
+                await aiofiles.os.remove(self._tmp_path)
+            except Exception:
+                pass  # Ignore cleanup errors
 
     async def write(self, data: str):
         self._buffer.append(data)
@@ -376,8 +404,18 @@ class ExportStatistics:
         self.messages_export_duration: float = 0.0
         self.media_download_duration: float = 0.0
         self.transcription_duration: float = 0.0
+        
+        # Performance profiling (detailed breakdown)
+        self.time_api_requests: float = 0.0  # Time waiting for Telegram API
+        self.time_processing: float = 0.0    # Time processing messages (formatting, etc)
+        self.time_file_io: float = 0.0       # Time writing to disk
+        self.api_request_count: int = 0      # Number of API requests made
+        
         # Pipeline-level statistics (e.g., processed_count, errors, duration, queue maxes)
         self.pipeline_stats: Dict[str, Any] = {}
+        
+        # 🚀 Parallel Media Processing metrics (TIER B - B-3)
+        self.parallel_media_metrics: Optional[Dict[str, Any]] = None
 
         # Start times (for tracking)
         self._messages_start: Optional[float] = None
@@ -453,6 +491,43 @@ class ExportStatistics:
             "total": total,
         }
 
+    def copy(self) -> "ExportStatistics":
+        """Create an independent copy of this statistics object."""
+        new_stats = ExportStatistics()
+        
+        # Copy all attributes
+        new_stats.start_time = self.start_time
+        new_stats.end_time = self.end_time
+        new_stats.messages_processed = self.messages_processed
+        new_stats.media_downloaded = self.media_downloaded
+        new_stats.notes_created = self.notes_created
+        new_stats.errors_encountered = self.errors_encountered
+        new_stats.cache_hits = self.cache_hits
+        new_stats.cache_misses = self.cache_misses
+        new_stats.avg_cpu_percent = self.avg_cpu_percent
+        new_stats.peak_memory_mb = self.peak_memory_mb
+        
+        # Copy operation durations
+        new_stats.messages_export_duration = self.messages_export_duration
+        new_stats.media_download_duration = self.media_download_duration
+        new_stats.transcription_duration = self.transcription_duration
+        
+        # Copy performance profiling fields (TIER A)
+        new_stats.time_api_requests = self.time_api_requests
+        new_stats.time_processing = self.time_processing
+        new_stats.time_file_io = self.time_file_io
+        new_stats.api_request_count = self.api_request_count
+        
+        # Deep copy pipeline stats
+        new_stats.pipeline_stats = self.pipeline_stats.copy() if self.pipeline_stats else {}
+        
+        # Copy start times (but these should typically be None at copy time)
+        new_stats._messages_start = self._messages_start
+        new_stats._media_start = self._media_start
+        new_stats._transcription_start = self._transcription_start
+        
+        return new_stats
+
 
 class Exporter:
     """
@@ -504,6 +579,12 @@ class Exporter:
         # Time-based progress updates
         self._last_progress_update = 0.0
         self._progress_update_interval = 0.5  # Update progress every 0.5 seconds
+        
+        # 🚀 Parallel Media Processor (TIER B - B-3)
+        # Initialize ParallelMediaProcessor for concurrent media operations
+        from src.media.parallel_processor import create_parallel_processor_from_config
+        self._parallel_media_processor = create_parallel_processor_from_config(config)
+        logger.info(f"✅ ParallelMediaProcessor initialized: {self._parallel_media_processor._config}")
 
         # Lazy logging to reduce overhead (prefer native LogBatcher when available)
         self._log_batch_interval = float(
@@ -701,6 +782,48 @@ class Exporter:
             reverse=False,  # Changed to False for chronological order
         )
 
+    async def _calculate_bloom_filter_size(self, entity) -> int:
+        """
+        Calculate optimal BloomFilter size based on entity message count (TIER B-4).
+        
+        Args:
+            entity: Telegram entity to analyze
+            
+        Returns:
+            Expected items for BloomFilter (with buffer for new messages)
+        """
+        try:
+            # Get total message count from entity
+            total_messages = await self.telegram_manager.get_message_count(entity)
+            
+            if total_messages == 0:
+                logger.warning("Entity has 0 messages, using minimum BloomFilter size")
+                return self.config.bloom_filter_min_size
+            
+            # Add buffer for new messages during export (default 10%)
+            multiplier = self.config.bloom_filter_size_multiplier
+            expected = int(total_messages * multiplier)
+            
+            # Clamp to configured range
+            # Min: prevents over-allocation for small chats (default 10k = ~120KB)
+            # Max: prevents excessive memory for mega-chats (default 10M = ~12MB)
+            clamped = max(
+                self.config.bloom_filter_min_size,
+                min(expected, self.config.bloom_filter_max_size)
+            )
+            
+            logger.info(
+                f"📊 BloomFilter sizing: {total_messages:,} messages "
+                f"× {multiplier:.1f} = {expected:,} expected → {clamped:,} (final)"
+            )
+            
+            return clamped
+            
+        except Exception as e:
+            logger.warning(f"Failed to calculate BloomFilter size: {e}")
+            # Fallback to current default (1M = ~1.2MB)
+            return 1_000_000
+
     async def _process_message_parallel(
         self, message, target, media_dir, output_dir, entity_reporter
     ):
@@ -870,10 +993,14 @@ class Exporter:
                     entity_data = None
 
             if not isinstance(entity_data, EntityCacheData):
+                # 🔄 TIER B-4: Calculate optimal BloomFilter size dynamically
+                bf_size = await self._calculate_bloom_filter_size(entity)
+                
                 entity_data = EntityCacheData(
                     entity_id=str(target.id),
                     entity_name=entity_name,
                     entity_type="regular",
+                    processed_message_ids=BloomFilter(expected_items=bf_size),
                 )
 
             # Create output directory structure FIRST
@@ -911,6 +1038,13 @@ class Exporter:
             logger.info(f"📁 Monitoring directory created: {monitoring_dir}")
             logger.info(f"📊 Monitoring file: monitoring_{target.id}.json")
             logger.info(f"💾 Cache key: {cache_key}")
+            
+            # Register progress save hook for graceful shutdown (TIER A - Task 3)
+            from src.shutdown_manager import shutdown_manager
+            self._current_reporter = entity_reporter  # Store for shutdown hook
+            shutdown_manager.register_async_cleanup_hook(
+                lambda: self._save_progress_on_shutdown(entity_data, cache_key)
+            )
 
             # Create single chat file
             safe_name = (
@@ -933,6 +1067,11 @@ class Exporter:
             if self.config.media_download:
                 await asyncio.to_thread(media_dir.mkdir, exist_ok=True)
 
+            # Initialize OutputManager for TTY-aware progress reporting (TIER B - B-5)
+            from src.ui.output_manager import get_output_manager
+            output_mgr = get_output_manager()
+            output_mgr.start_export(entity_name, total_messages=None)
+
             # Use Rich progress bar for better UX (streaming mode - no percentage)
             with Progress(
                 SpinnerColumn(),
@@ -998,6 +1137,11 @@ class Exporter:
                         )
 
                         async def process_fn(message):
+                            # 🔄 TIER B-4: Early filter for already-processed messages
+                            # This handles edge cases like message ID gaps (deleted messages)
+                            if message.id in entity_data.processed_message_ids:
+                                return None  # Skip this message
+                            
                             # Filter empty messages early (same semantics as the old loop)
                             if not (
                                 getattr(message, "text", None)
@@ -1078,12 +1222,20 @@ class Exporter:
                                     )
 
                         # Execute the pipeline (it will fetch/process/write)
+                        # 🔄 TIER B-4: Resume from last processed message
+                        resume_from_id = entity_data.last_message_id or 0
+                        if resume_from_id > 0:
+                            logger.info(f"📍 [Pipeline] Resume point: message ID {resume_from_id}")
+                        else:
+                            logger.info("📍 [Pipeline] Starting from beginning")
+                        
                         pipeline_stats = await pipeline.run(
                             entity=entity,
                             telegram_manager=self.telegram_manager,
                             process_fn=process_fn,
                             writer_fn=writer_fn,
                             limit=None,
+                            min_id=resume_from_id,  # TIER B-4: Skip already processed messages
                         )
 
                         logger.info(f"Async pipeline finished: {pipeline_stats}")
@@ -1113,10 +1265,34 @@ class Exporter:
 
                         fetched_count = 0  # Count of messages fetched from generator (for debugging)
 
+                        # 🔄 TIER B-4: Resume from last processed message (if any)
+                        resume_from_id = entity_data.last_message_id or 0
+                        if resume_from_id > 0:
+                            logger.info(f"📍 Resume point: message ID {resume_from_id} (skipping already processed)")
+                        else:
+                            logger.info("📍 Starting from beginning (no previous progress)")
+
                         async for message in self.telegram_manager.fetch_messages(
                             entity,
                             limit=None,  # Export all messages
+                            min_id=resume_from_id,  # Skip already processed messages
                         ):
+                            # Check for graceful shutdown request (TIER A - Task 3)
+                            from src.shutdown_manager import shutdown_manager
+                            if shutdown_manager.shutdown_requested:
+                                logger.info("🛑 Graceful shutdown requested, stopping message fetch")
+                                break
+                            
+                            # 🔄 TIER B-4: Early skip check for already-processed messages
+                            # This handles edge cases like message ID gaps (deleted messages)
+                            # where min_id alone might not be sufficient
+                            if message.id in entity_data.processed_message_ids:
+                                logger.debug(f"⏭️ Skipping message {message.id} (already in BloomFilter)")
+                                continue
+                            
+                            # Track API time (time spent waiting for messages from fetch_messages iterator)
+                            api_start = time.time()
+                            
                             fetched_count += 1
 
                             batch.append(message)
@@ -1125,21 +1301,46 @@ class Exporter:
                             if len(batch) < batch_size:
                                 continue
 
+                            # Track API time for this batch
+                            api_time = time.time() - api_start
+                            self.statistics.time_api_requests += api_time
+                            self.statistics.api_request_count += 1
+
                             # --- BATCH PROCESSING ---
-                            # Process batch in parallel (MW3)
-                            tasks = [
-                                self._process_message_parallel(
+                            # Track processing time
+                            process_start = time.time()
+                            
+                            # 🚀 Process batch with ParallelMediaProcessor (TIER B - B-3)
+                            # This allows concurrent media downloads/processing with semaphore control
+                            async def process_fn(msg):
+                                """Wrapper for _process_message_parallel"""
+                                return await self._process_message_parallel(
                                     msg, target, media_dir, output_dir, entity_reporter
                                 )
-                                for msg in batch
-                                if (msg.text or msg.media)  # Filter empty
-                            ]
-
-                            if tasks:
-                                results = await asyncio.gather(*tasks)
+                            
+                            # Filter empty messages
+                            messages_to_process = [msg for msg in batch if (msg.text or msg.media)]
+                            
+                            if messages_to_process:
+                                # Use parallel processor for concurrent media handling
+                                results = await self._parallel_media_processor.process_batch(
+                                    messages_to_process, process_fn
+                                )
+                                
+                                process_time = time.time() - process_start
+                                self.statistics.time_processing += process_time
+                                
+                                # Track file I/O time
+                                io_start = time.time()
 
                                 # Write results sequentially
-                                for content, msg_id, has_media, media_cnt in results:
+                                for result in results:
+                                    # Handle exceptions from gather
+                                    if isinstance(result, Exception):
+                                        logger.warning(f"Failed to process message: {result}")
+                                        continue
+                                    
+                                    content, msg_id, has_media, media_cnt = result
                                     if not content:
                                         continue  # Skip failed
 
@@ -1157,6 +1358,15 @@ class Exporter:
                                     entity_reporter.record_message_processed(
                                         msg_id, has_media=has_media
                                     )
+                                
+                                io_time = time.time() - io_start
+                                self.statistics.time_file_io += io_time
+
+                                # Check for shutdown after processing batch
+                                if shutdown_manager.shutdown_requested:
+                                    logger.info("🛑 Shutdown requested after batch processing")
+                                    await f.flush()  # Flush buffer before stopping
+                                    break
 
                                 # Periodic save
                                 if processed_count % 100 == 0:
@@ -1198,6 +1408,7 @@ class Exporter:
                                     current_time - self._last_progress_update
                                     >= self._progress_update_interval
                                 ):
+                                    # Update Rich progress bar
                                     progress.update(
                                         task_id_progress,
                                         messages=processed_count,
@@ -1205,6 +1416,16 @@ class Exporter:
                                     )
                                     self._last_progress_update = current_time
 
+                                    # Also send update to OutputManager (TIER B - B-5)
+                                    from src.ui.output_manager import ProgressUpdate
+                                    output_mgr.show_progress(ProgressUpdate(
+                                        entity_name=entity_name,
+                                        messages_processed=processed_count,
+                                        total_messages=None,
+                                        stage="processing",
+                                        percentage=None
+                                    ))
+
                                     # Also update progress queue if provided
                                     if progress_queue:
                                         await progress_queue.put(
@@ -1221,18 +1442,36 @@ class Exporter:
 
                         # Process remaining messages in batch
                         if batch:
-                            tasks = [
-                                self._process_message_parallel(
+                            # Track processing time for final batch
+                            process_start = time.time()
+                            
+                            # 🚀 Use parallel processor for final batch (TIER B - B-3)
+                            async def process_fn(msg):
+                                """Wrapper for _process_message_parallel"""
+                                return await self._process_message_parallel(
                                     msg, target, media_dir, output_dir, entity_reporter
                                 )
-                                for msg in batch
-                                if (msg.text or msg.media)
-                            ]
+                            
+                            messages_to_process = [msg for msg in batch if (msg.text or msg.media)]
+                            
+                            if messages_to_process:
+                                results = await self._parallel_media_processor.process_batch(
+                                    messages_to_process, process_fn
+                                )
+                                
+                                process_time = time.time() - process_start
+                                self.statistics.time_processing += process_time
+                                
+                                # Track file I/O time for final batch
+                                io_start = time.time()
 
-                            if tasks:
-                                results = await asyncio.gather(*tasks)
-
-                                for content, msg_id, has_media, media_cnt in results:
+                                for result in results:
+                                    # Handle exceptions from gather
+                                    if isinstance(result, Exception):
+                                        logger.warning(f"Failed to process message: {result}")
+                                        continue
+                                    
+                                    content, msg_id, has_media, media_cnt = result
                                     if not content:
                                         continue
 
@@ -1248,6 +1487,9 @@ class Exporter:
                                     entity_reporter.record_message_processed(
                                         msg_id, has_media=has_media
                                     )
+                                
+                                io_time = time.time() - io_start
+                                self.statistics.time_file_io += io_time
 
                                 # Final update
                                 progress.update(
@@ -1296,6 +1538,24 @@ class Exporter:
                         logger.info(
                             f"📊 Collected stats from {len(worker_stats)} workers"
                         )
+                
+                # 🚀 Collect parallel media processing metrics (TIER B - B-3)
+                parallel_metrics = self._parallel_media_processor.get_metrics()
+                if parallel_metrics and parallel_metrics.total_media_processed > 0:
+                    metrics_dict = {
+                        "total_media_processed": parallel_metrics.total_media_processed,
+                        "concurrent_peak": parallel_metrics.concurrent_peak,
+                        "avg_concurrency": round(parallel_metrics.avg_concurrency, 2),
+                        "memory_throttles": parallel_metrics.memory_throttles,
+                    }
+                    self.statistics.parallel_media_metrics = metrics_dict
+                    entity_reporter.metrics.parallel_media_metrics = metrics_dict
+                    logger.info(
+                        f"🚀 Parallel media stats: {parallel_metrics.total_media_processed} media, "
+                        f"peak concurrency: {parallel_metrics.concurrent_peak}, "
+                        f"avg: {parallel_metrics.avg_concurrency:.2f}, "
+                        f"throttles: {parallel_metrics.memory_throttles}"
+                    )
 
                 entity_reporter.finish_export()
                 entity_reporter.save_report()
@@ -1315,6 +1575,10 @@ class Exporter:
                     f"  📈 Monitoring saved to: {monitoring_dir}/monitoring_{target.id}.json"
                 )
                 logger.info(f"  💾 Cache key: {cache_key}")
+                
+                # Notify OutputManager of successful completion (TIER B - B-5)
+                output_mgr.finish_export(entity_name, success=True)
+                
             except Exception as save_error:
                 logger.warning(
                     f"Failed to save cache/monitoring for {entity_name}: {save_error}"
@@ -1324,6 +1588,12 @@ class Exporter:
             logger.error(f"Export failed for {target.name}: {e}")
             self.statistics.errors_encountered += 1
 
+            # Notify OutputManager of failure (TIER B - B-5)
+            try:
+                output_mgr.finish_export(entity_name, success=False)
+            except:
+                pass  # OutputManager may not be initialized
+
             # Try to save cache/monitoring even on failure
             try:
                 await self.cache_manager.set(cache_key, entity_data)
@@ -1354,57 +1624,119 @@ class Exporter:
 
             raise
 
-        return self.statistics
+        return self.statistics.copy()
 
     async def _get_sender_name(self, message) -> str:
-        """Get formatted sender name for message with caching and string interning."""
+        """Get formatted sender name for message with caching and string interning.
+
+        If the message's sender object is missing or doesn't contain a human-readable
+        name, attempt to resolve the sender via `self.telegram_manager.resolve_entity`
+        (cached) and extract the name from the resolved entity.
+        """
         try:
             sender_id = message.sender_id
             if not sender_id:
                 return self._intern_string("Unknown User")
 
-            # Check cache first
+            # Fast path: check cache first
             if sender_id in self._sender_name_cache:
                 return self._sender_name_cache[sender_id]
 
-            if message.sender:
-                name = "Unknown User"
-                if hasattr(message.sender, "first_name"):
-                    # User
+            def _format_entity(entity) -> Optional[str]:
+                if entity is None:
+                    return None
+                # User-like objects
+                if hasattr(entity, "first_name"):
                     name_parts = []
-                    if message.sender.first_name:
-                        name_parts.append(message.sender.first_name)
-                    if getattr(message.sender, "last_name", None):
-                        name_parts.append(message.sender.last_name)
-                    name = " ".join(name_parts) if name_parts else f"User {sender_id}"
-                elif hasattr(message.sender, "title"):
-                    # Channel/Group
-                    name = str(message.sender.title)
-                else:
-                    name = f"User {sender_id}"
+                    if getattr(entity, "first_name", None):
+                        name_parts.append(entity.first_name)
+                    if getattr(entity, "last_name", None):
+                        name_parts.append(entity.last_name)
+                    if name_parts:
+                        return " ".join(name_parts)
+                    if getattr(entity, "username", None):
+                        return f"@{entity.username}"
+                    return None
+                # Channel / Group
+                if hasattr(entity, "title"):
+                    return str(entity.title)
+                # Fall back to username if present
+                if getattr(entity, "username", None):
+                    return f"@{entity.username}"
+                return None
 
-                # Intern and cache the result
+            # Prefer using the message.sender object if available
+            if getattr(message, "sender", None):
+                name = _format_entity(message.sender) or f"User {sender_id}"
                 interned_name = self._intern_string(name)
                 self._sender_name_cache[sender_id] = interned_name
                 return interned_name
-            else:
-                return self._intern_string(f"User {sender_id}")
+
+            # If sender object is missing, try to resolve the entity by id
+            resolved = None
+            try:
+                resolved = await self.telegram_manager.resolve_entity(sender_id)
+            except Exception:
+                # If resolving fails (network / API error), fall through to fallback
+                resolved = None
+
+            name = _format_entity(resolved)
+            if name:
+                interned_name = self._intern_string(name)
+                self._sender_name_cache[sender_id] = interned_name
+                return interned_name
+
+            # Final fallback
+            return self._intern_string(f"User {sender_id}")
         except Exception:
             return self._intern_string("Unknown User")
 
     def _format_timestamp(self, dt) -> str:
-        """Format datetime in Telegram export format (optimized with interning)."""
-        # f-string is faster than strftime
-        timestamp_str = (
-            f"{dt.day:02d}.{dt.month:02d}.{dt.year} {dt.hour:02d}:{dt.minute:02d}"
-        )
+        """Format datetime in Telegram export format (optimized with interning).
+
+        Convert message timestamps to UTC+3 before rendering to keep exported
+        notes consistently in the desired timezone.
+        """
+        import datetime as _dt
+
+        try:
+            if dt is None:
+                return self._intern_string("Unknown Date")
+
+            # If naive, assume UTC (best-effort) then convert to UTC+3
+            if getattr(dt, "tzinfo", None) is None:
+                dt = dt.replace(tzinfo=_dt.timezone.utc)
+
+            tz = _dt.timezone(_dt.timedelta(hours=3))
+            dt_local = dt.astimezone(tz)
+
+            timestamp_str = (
+                f"{dt_local.day:02d}.{dt_local.month:02d}.{dt_local.year} "
+                f"{dt_local.hour:02d}:{dt_local.minute:02d}"
+            )
+        except Exception:
+            # Fallback to safe formatting if anything goes wrong
+            try:
+                timestamp_str = (
+                    f"{getattr(dt, 'day', 0):02d}."
+                    f"{getattr(dt, 'month', 0):02d}."
+                    f"{getattr(dt, 'year', 0)} "
+                    f"{getattr(dt, 'hour', 0):02d}:{getattr(dt, 'minute', 0):02d}"
+                )
+            except Exception:
+                timestamp_str = "00.00.0000 00:00"
+
         return self._intern_string(timestamp_str)
 
     def _get_current_datetime(self) -> str:
-        """Get current datetime formatted."""
-        import datetime
+        """Get current datetime formatted in UTC+3."""
+        import datetime as _dt
 
-        return datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
+        tz = _dt.timezone(_dt.timedelta(hours=3))
+        # Use UTC then convert to UTC+3 to avoid depending on system local tz
+        now_utc = _dt.datetime.now(_dt.timezone.utc)
+        now_local = now_utc.astimezone(tz)
+        return now_local.strftime("%d.%m.%Y %H:%M")
 
     def _get_media_type_name(self, media) -> str:
         """Get human-readable media type name."""
@@ -1424,7 +1756,11 @@ class Exporter:
         return type_mapping.get(media_type, "Media")
 
     async def _update_message_count(self, file_path, count):
-        """Update the total message count in the exported file."""
+        """
+        Update the total message count in the exported file.
+        
+        Security S-4: Uses atomic write (tmp + rename) to prevent corruption.
+        """
         try:
             # Read the file asynchronously
             async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
@@ -1435,9 +1771,13 @@ class Exporter:
                 "Total Messages: Processing...", f"Total Messages: {count}"
             )
 
-            # Write back asynchronously
-            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
+            # S-4: Atomic write - write to .tmp then rename
+            tmp_path = f"{file_path}.tmp"
+            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                 await f.write(content)
+            
+            # Atomic rename
+            await aiofiles.os.rename(tmp_path, file_path)
         except Exception as e:
             logger.warning(f"Failed to update message count: {e}")
 
@@ -1447,7 +1787,7 @@ class Exporter:
     # --- Forum Export Methods ---
 
     async def _export_forum(
-        self, target: ExportTarget, progress_queue, task_id
+        self, target: ExportTarget, progress_queue, task_id, progress_obj=None, overall_task_id=None
     ) -> ExportStatistics:
         logger.info(f"Starting forum export: {target.name}")
         entity = await self.telegram_manager.resolve_entity(target.id)
@@ -1484,38 +1824,190 @@ class Exporter:
         )
 
         if not topics_to_export:
-            return self.statistics
+            return self.statistics.copy()
 
-        # Export each topic
-        for i, topic in enumerate(topics_to_export):
-            topic_title = topic.title or f"Topic {topic.topic_id}"
-            logger.info(
-                f"Exporting topic {i + 1}/{len(topics_to_export)}: {topic_title}"
-            )
+        # Use provided progress object or create a new one
+        use_existing_progress = progress_obj is not None
+        
+        async def _do_export(progress, main_task_id=None):
+            """Helper function to perform export with given progress context."""
+            # Dictionary to store task IDs for each topic
+            topic_tasks = {}
+            
+            # Pre-fetch message counts for all topics and create progress tasks
+            logger.info("📊 Fetching message counts for all topics...")
+            
+            # Calculate total message count across all topics
+            total_forum_messages = 0
+            for i, topic in enumerate(topics_to_export):
+                topic_title = topic.title or f"Topic {topic.topic_id}"
+                
+                # Get message count for this topic
+                try:
+                    total_messages = topic.message_count or 0
+                    total_forum_messages += total_messages
+                    
+                    logger.debug(
+                        f"  Topic {i+1}/{len(topics_to_export)}: {topic_title} - {total_messages} messages"
+                    )
+                except Exception as e:
+                    logger.warning(f"  Could not get count for topic {topic_title}: {e}")
+            
+            # Update the main task if provided, otherwise create overall bar
+            if main_task_id is not None:
+                # Update existing task with total
+                progress.update(
+                    main_task_id,
+                    total=total_forum_messages if total_forum_messages > 0 else None,
+                    current=0,
+                    total_field=total_forum_messages,
+                )
+                forum_overall_task_id = main_task_id
+            else:
+                # Create our own overall forum progress bar
+                forum_overall_task_id = progress.add_task(
+                    f"[bold green]📊 Overall: {entity_name}",
+                    total=total_forum_messages if total_forum_messages > 0 else None,
+                    current=0,
+                    total_field=total_forum_messages,
+                )
+            
+            # Track overall progress
+            overall_processed = 0
+            
+            # Now create individual topic progress bars
+            for i, topic in enumerate(topics_to_export):
+                topic_title = topic.title or f"Topic {topic.topic_id}"
+                
+                try:
+                    total_messages = topic.message_count or 0
+                    
+                    # Create a progress task for this topic
+                    task_id = progress.add_task(
+                        f"  [cyan]{topic_title[:40]}...",
+                        total=total_messages if total_messages > 0 else None,
+                        current=0,
+                        total_field=total_messages,
+                    )
+                    topic_tasks[topic.topic_id] = task_id
+                    
+                except Exception as e:
+                    logger.warning(f"  Could not get count for topic {topic_title}: {e}")
+                    # Create task with unknown total
+                    task_id = progress.add_task(
+                        f"  [cyan]{topic_title[:40]}...",
+                        total=None,
+                        current=0,
+                        total_field=0,
+                    )
+                    topic_tasks[topic.topic_id] = task_id
 
-            safe_title = sanitize_filename(topic_title) or f"topic_{topic.topic_id}"
-            topic_file = topics_dir / f"{safe_title}.md"
+            # Export each topic with progress tracking
+            for i, topic in enumerate(topics_to_export):
+                topic_title = topic.title or f"Topic {topic.topic_id}"
+                logger.info(
+                    f"Exporting topic {i + 1}/{len(topics_to_export)}: {topic_title}"
+                )
 
-            topic_processed_count = 0
+                safe_title = sanitize_filename(topic_title) or f"topic_{topic.topic_id}"
+                topic_file = topics_dir / f"{safe_title}.md"
 
-            try:
-                async with AsyncBufferedSaver(topic_file, "w", encoding="utf-8") as f:
-                    await f.write(f"# Topic: {topic_title}\n")
-                    await f.write(f"ID: {topic.topic_id}\n\n")
+                topic_processed_count = 0
+                topic_task_id = topic_tasks.get(topic.topic_id)
 
-                    # Batch processing for this topic
-                    batch = []
-                    batch_size = 50
+                try:
+                    async with AsyncBufferedSaver(topic_file, "w", encoding="utf-8") as f:
+                        await f.write(f"# Topic: {topic_title}\n")
+                        await f.write(f"ID: {topic.topic_id}\n\n")
 
-                    async for (
-                        message
-                    ) in self.telegram_manager.get_topic_messages_stream(
-                        entity, topic.topic_id
-                    ):
-                        batch.append(message)
+                        # Batch processing for this topic
+                        batch = []
+                        batch_size = 75  # Increased from 50 for better throughput
+                        
+                        # Performance tracking
+                        stream_start = time.time()
+                        messages_fetched = 0
 
-                        if len(batch) >= batch_size:
-                            # Process batch
+                        async for (
+                            message
+                        ) in self.telegram_manager.get_topic_messages_stream(
+                            entity, topic.topic_id
+                        ):
+                            messages_fetched += 1
+                            batch.append(message)
+
+                            if len(batch) >= batch_size:
+                                # Track API time (streaming)
+                                api_time = time.time() - stream_start
+                                self.statistics.time_api_requests += api_time
+                                self.statistics.api_request_count += 1
+                                
+                                # Track processing time
+                                process_start = time.time()
+                                
+                                # Process batch
+                                tasks = [
+                                    self._process_message_parallel(
+                                        msg, target, media_dir, topics_dir, entity_reporter
+                                    )
+                                    for msg in batch
+                                    if (msg.text or msg.media)
+                                ]
+
+                                if tasks:
+                                    results = await asyncio.gather(*tasks)
+                                    
+                                    process_time = time.time() - process_start
+                                    self.statistics.time_processing += process_time
+                                    
+                                    # Track file I/O time
+                                    io_start = time.time()
+                                    
+                                    batch_processed = 0
+                                    for content, msg_id, has_media, media_cnt in results:
+                                        if content:
+                                            await f.write(content)
+                                            topic_processed_count += 1
+                                            batch_processed += 1
+                                            self.statistics.messages_processed += 1
+                                            self.statistics.media_downloaded += media_cnt
+                                            entity_reporter.record_message_processed(
+                                                msg_id, has_media=has_media
+                                            )
+                                    
+                                    io_time = time.time() - io_start
+                                    self.statistics.time_file_io += io_time
+                                    
+                                    # Update overall progress
+                                    overall_processed += batch_processed
+                                    progress.update(
+                                        forum_overall_task_id,
+                                        completed=overall_processed,
+                                        current=overall_processed,
+                                    )
+                                    
+                                    # Update progress for this topic
+                                    if topic_task_id is not None:
+                                        progress.update(
+                                            topic_task_id,
+                                            completed=topic_processed_count,
+                                            current=topic_processed_count,
+                                        )
+
+                                batch.clear()
+                                stream_start = time.time()  # Reset for next batch
+
+                        # Process remaining
+                        if batch:
+                            # Track final API time
+                            if messages_fetched > 0:
+                                api_time = time.time() - stream_start
+                                self.statistics.time_api_requests += api_time
+                                if batch:  # Only count if there were messages
+                                    self.statistics.api_request_count += 1
+                            
+                            process_start = time.time()
+                            
                             tasks = [
                                 self._process_message_parallel(
                                     msg, target, media_dir, topics_dir, entity_reporter
@@ -1523,56 +2015,83 @@ class Exporter:
                                 for msg in batch
                                 if (msg.text or msg.media)
                             ]
-
                             if tasks:
                                 results = await asyncio.gather(*tasks)
+                                
+                                process_time = time.time() - process_start
+                                self.statistics.time_processing += process_time
+                                
+                                io_start = time.time()
+                                
+                                remaining_processed = 0
                                 for content, msg_id, has_media, media_cnt in results:
                                     if content:
                                         await f.write(content)
                                         topic_processed_count += 1
+                                        remaining_processed += 1
                                         self.statistics.messages_processed += 1
                                         self.statistics.media_downloaded += media_cnt
                                         entity_reporter.record_message_processed(
                                             msg_id, has_media=has_media
                                         )
-
-                            batch.clear()
-
-                    # Process remaining
-                    if batch:
-                        tasks = [
-                            self._process_message_parallel(
-                                msg, target, media_dir, topics_dir, entity_reporter
+                                
+                                io_time = time.time() - io_start
+                                self.statistics.time_file_io += io_time
+                                
+                                # Update overall progress
+                                overall_processed += remaining_processed
+                                progress.update(
+                                    forum_overall_task_id,
+                                    completed=overall_processed,
+                                    current=overall_processed,
+                                )
+                        
+                        # Final progress update for this topic
+                        if topic_task_id is not None:
+                            progress.update(
+                                topic_task_id,
+                                completed=topic_processed_count,
+                                current=topic_processed_count,
                             )
-                            for msg in batch
-                            if (msg.text or msg.media)
-                        ]
-                        if tasks:
-                            results = await asyncio.gather(*tasks)
-                            for content, msg_id, has_media, media_cnt in results:
-                                if content:
-                                    await f.write(content)
-                                    topic_processed_count += 1
-                                    self.statistics.messages_processed += 1
-                                    self.statistics.media_downloaded += media_cnt
-                                    entity_reporter.record_message_processed(
-                                        msg_id, has_media=has_media
-                                    )
 
-                logger.info(
-                    f"  ✅ Finished topic {topic_title}: {topic_processed_count} messages"
-                )
+                    logger.info(
+                        f"  ✅ Finished topic {topic_title}: {topic_processed_count} messages"
+                    )
 
-            except Exception as e:
-                logger.error(f"  ❌ Failed to export topic {topic_title}: {e}")
-                self.statistics.errors_encountered += 1
+                except Exception as e:
+                    logger.error(f"  ❌ Failed to export topic {topic_title}: {e}")
+                    self.statistics.errors_encountered += 1
+                    # Mark task as failed
+                    if topic_task_id is not None:
+                        progress.update(
+                            topic_task_id,
+                            description=f"[red]❌ {topic_title[:40]}...",
+                        )
+        
+        # Call the export function with the appropriate progress context
+        if use_existing_progress:
+            # Use the provided progress object
+            await _do_export(progress_obj, overall_task_id)
+        else:
+            # Create our own progress context
+            with Progress(
+                SpinnerColumn(),
+                TextColumn("[progress.description]{task.description}"),
+                BarColumn(),
+                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
+                TextColumn("•"),
+                TextColumn("[cyan]{task.fields[current]}/{task.fields[total_field]} msgs"),
+                TimeRemainingColumn(),
+                transient=False,
+            ) as progress:
+                await _do_export(progress, None)
 
         # Finish reporting
         entity_reporter.metrics.total_messages = self.statistics.messages_processed
         entity_reporter.finish_export()
         entity_reporter.save_report()
 
-        return self.statistics
+        return self.statistics.copy()
 
     async def export_all(
         self, targets: List[ExportTarget], progress_queue=None
@@ -1594,11 +2113,16 @@ class Exporter:
             TextColumn("[progress.description]{task.description}"),
             BarColumn(),
             TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
+            TextColumn("•"),
+            TextColumn("[cyan]{task.fields[current]}/{task.fields[total_field]} msgs"),
             TimeRemainingColumn(),
-            transient=True,
+            transient=False,
         ) as progress:
             task_id_progress = progress.add_task(
-                "[cyan]Exporting targets...", total=len(targets)
+                "[cyan]Exporting targets...", 
+                total=len(targets),
+                current=0,
+                total_field=0,
             )
 
             for i, target in enumerate(targets):
@@ -1613,9 +2137,29 @@ class Exporter:
                 logger.info(f"Exporting target {i + 1}/{len(targets)}: {target.name}")
 
                 try:
-                    stats = await self.export_target(
-                        target, progress_queue, f"target_{i}"
-                    )
+                    # Pass progress context to forum exports
+                    if target.type in ["forum", "forum_chat", "forum_topic"]:
+                        # Reset statistics
+                        self.statistics = ExportStatistics()
+                        
+                        # Clear caches
+                        self._sender_name_cache.clear()
+                        self._prefetch_stats = {"hits": 0, "misses": 0}
+                        if self._prefetch_task and not self._prefetch_task.done():
+                            self._prefetch_task.cancel()
+                        self._prefetch_task = None
+                        self._prefetch_result = None
+                        
+                        # Call _export_forum directly with progress context
+                        stats = await asyncio.wait_for(
+                            self._export_forum(target, progress_queue, f"target_{i}", progress, task_id_progress),
+                            timeout=EXPORT_OPERATION_TIMEOUT,
+                        )
+                    else:
+                        stats = await self.export_target(
+                            target, progress_queue, f"target_{i}"
+                        )
+                    
                     results.append(stats)
 
                     logger.info(f"✅ Target {target.name} exported successfully")
@@ -1662,6 +2206,14 @@ async def run_export(
     High-level export orchestration function.
     Replaces the main export logic from main.py run_export function.
     """
+    # 🚀 TIER C-4: Initialize metrics collection and resource monitoring
+    from ..monitoring import get_metrics_collector, ResourceMonitor
+    from ..monitoring.metrics_formatter import log_metrics_summary
+    import json
+    
+    metrics = get_metrics_collector()
+    resource_monitor = ResourceMonitor(interval_seconds=5.0)
+    
     exporter = Exporter(
         config=config,
         telegram_manager=telegram_manager,
@@ -1673,6 +2225,9 @@ async def run_export(
     )
 
     try:
+        # 📊 Start resource monitoring (TIER C-4)
+        await resource_monitor.start()
+        logger.info("✅ TIER C-4: Resource monitoring started")
         # Initialize all components
         await exporter.initialize()
 
@@ -1730,6 +2285,12 @@ async def run_export(
 
                     # ⚡ HACK: Temporarily swap the client in the manager
                     original_client = telegram_manager.client
+                    
+                    # IMPORTANT: Update _original_client to point to the real client
+                    # before we replace self.client with TakeoutSessionWrapper
+                    if not hasattr(telegram_manager, '_original_client') or telegram_manager._original_client is None:
+                        telegram_manager._original_client = original_client
+                    
                     telegram_manager.client = takeout_client
 
                     # Pass the ID to the manager so shards can reuse it
@@ -1759,6 +2320,7 @@ async def run_export(
                     finally:
                         # Restore original client and settings
                         telegram_manager.client = original_client
+                        telegram_manager._original_client = original_client  # Restore _original_client too
                         if hasattr(telegram_manager, "_external_takeout_id"):
                             telegram_manager._external_takeout_id = None
 
@@ -1796,6 +2358,31 @@ async def run_export(
         return results
 
     finally:
+        # 📊 TIER C-4: Stop resource monitoring and export metrics
+        try:
+            await resource_monitor.stop()
+            logger.info("✅ TIER C-4: Resource monitoring stopped")
+            
+            # Export metrics to JSON file
+            metrics_data = metrics.export_json()
+            metrics_path = os.path.join(config.export_path, "export_metrics.json")
+            
+            try:
+                with open(metrics_path, 'w', encoding='utf-8') as f:
+                    json.dump(metrics_data, f, indent=2, ensure_ascii=False)
+                logger.info(f"📊 Metrics exported to: {metrics_path}")
+            except Exception as e:
+                logger.warning(f"⚠️ Failed to export metrics JSON: {e}")
+            
+            # Log human-readable metrics summary
+            try:
+                log_metrics_summary(metrics_data)
+            except Exception as e:
+                logger.warning(f"⚠️ Failed to log metrics summary: {e}")
+                
+        except Exception as e:
+            logger.warning(f"⚠️ Error during metrics finalization: {e}")
+        
         # Ensure cleanup happens
         await exporter.shutdown()
 
@@ -1819,3 +2406,28 @@ def print_export_summary(results: List[ExportStatistics]):
     rprint(f"[cyan]Errors:[/cyan] {total_errors}")
     rprint(f"[cyan]Total Duration:[/cyan] {total_duration:.1f}s")
     rprint("[bold green]═══════════════════════════════════════════════[/bold green]\n")
+
+    async def _save_progress_on_shutdown(self, entity_data: EntityCacheData, cache_key: str) -> None:
+        """
+        Save current progress state on graceful shutdown (TIER A - Task 3).
+        
+        This ensures resume capability even if export is interrupted.
+        
+        Args:
+            entity_data: Current entity state with processed messages
+            cache_key: Cache key for storing state
+        """
+        try:
+            logger.info(f"💾 Saving progress state on shutdown: {entity_data.entity_name}")
+            
+            # Save to cache
+            await self.cache_manager.set(cache_key, entity_data)
+            
+            # Also save metrics/stats if reporter available
+            if hasattr(self, '_current_reporter') and self._current_reporter:
+                self._current_reporter.save_metrics()
+                
+            logger.info(f"✅ Progress saved: {entity_data.processed_message_ids.items_added} messages processed")
+            
+        except Exception as e:
+            logger.error(f"❌ Failed to save progress on shutdown: {e}", exc_info=True)
diff --git i/src/export_reporter.py w/src/export_reporter.py
index b27bb7d..58c6ba2 100644
--- i/src/export_reporter.py
+++ w/src/export_reporter.py
@@ -16,7 +16,7 @@ from src.core.performance import PerformanceMonitor
 from src.utils import logger
 
 
-@dataclass
+@dataclass(slots=True)
 class ExportMetrics:
     """Метрики экспорта."""
 
@@ -44,6 +44,8 @@ class ExportMetrics:
     bytes_per_second: float = 0.0
     peak_memory_mb: float = 0.0
     avg_cpu_percent: float = 0.0
+    # Pipeline statistics captured when using AsyncPipeline (e.g. processed_count, duration, queue maxes)
+    pipeline_stats: Optional[Dict[str, Any]] = field(default_factory=dict)
 
     # Ошибки
     errors: List[Dict[str, Any]] = field(default_factory=list)
@@ -56,9 +58,12 @@ class ExportMetrics:
     # Keys are stringified worker indices for JSON compatibility
     # Values include: messages, flood_waits, requests, total_latency_ms, io_time_ms
     worker_stats: Optional[Dict[str, Dict[str, int]]] = None
+    
+    # C-3: InputPeer cache metrics (TIER C optimization)
+    input_peer_cache_metrics: Optional[Dict[str, Any]] = None
 
 
-@dataclass
+@dataclass(slots=True)
 class SystemInfo:
     """Информация о системе."""
 
@@ -78,7 +83,7 @@ class SystemInfo:
                 self.disk_free_gb = 0.0
 
 
-@dataclass
+@dataclass(slots=True)
 class EntityReport:
     """Отчет по сущности."""
 
@@ -227,6 +232,20 @@ class ExportReporter:
     def set_total_messages(self, total: int):
         """Установить общее количество сообщений."""
         self.metrics.total_messages = total
+    
+    def record_input_peer_cache_metrics(self, cache_metrics: Dict[str, Any]):
+        """
+        Record InputPeer cache metrics (C-3 optimization).
+        
+        Args:
+            cache_metrics: Dictionary from InputPeerCache.get_metrics()
+        """
+        self.metrics.input_peer_cache_metrics = cache_metrics.copy()
+        logger.debug(
+            f"InputPeer cache metrics: "
+            f"hit_rate={cache_metrics.get('hit_rate', 0)}%, "
+            f"size={cache_metrics.get('size', 0)}/{cache_metrics.get('max_size', 0)}"
+        )
 
     def get_progress(self) -> Dict[str, Any]:
         """Получить текущий прогресс."""
diff --git i/src/hot_zones_manager.py w/src/hot_zones_manager.py
index a32b5be..f6bb756 100644
--- i/src/hot_zones_manager.py
+++ w/src/hot_zones_manager.py
@@ -31,7 +31,7 @@ from src.config import Config
 logger = logging.getLogger(__name__)
 
 
-@dataclass
+@dataclass(slots=True)
 class HotZone:
     """
     Represents a known problematic ID range in a specific datacenter.
@@ -67,7 +67,7 @@ class HotZone:
         return self.id_start <= message_id <= self.id_end
 
 
-@dataclass
+@dataclass(slots=True)
 class SlowChunkRecord:
     """
     Record of a slow chunk for persistent database.
diff --git i/src/media/cache.py w/src/media/cache.py
index b2dc841..59d9bc6 100644
--- i/src/media/cache.py
+++ w/src/media/cache.py
@@ -61,18 +61,29 @@ class MediaCache:
             logger.debug(f"Cache save failed: {e}")
 
     async def _copy_file_async(self, src_path: Path, dst_path: Path):
-        """Асинхронное копирование файла."""
-        import aiofiles
+        """
+        Асинхронное копирование файла с zero-copy оптимизацией.
+        
+        Uses os.sendfile() on supported platforms for kernel-level copying,
+        falls back to aiofiles on unsupported platforms or for small files.
+        """
+        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer
 
         try:
-            dst_path.parent.mkdir(parents=True, exist_ok=True)
-
-            # Простое потоковое копирование
-            async with aiofiles.open(src_path, "rb") as src:
-                async with aiofiles.open(dst_path, "wb") as dst:
-                    while chunk := await src.read(1024 * 1024):  # 1MB chunks
-                        await dst.write(chunk)
-
+            # Use zero-copy transfer with config from cache_manager if available
+            config = ZeroCopyConfig(
+                enabled=True,
+                min_size_mb=10,
+                verify_copy=True,
+                chunk_size_mb=64
+            )
+            
+            transfer = get_zero_copy_transfer(config)
+            success = await transfer.copy_file(src_path, dst_path, verify=True)
+            
+            if not success:
+                raise RuntimeError(f"Zero-copy transfer failed: {src_path} -> {dst_path}")
+            
             logger.debug(f"File copied from cache: {src_path} -> {dst_path}")
 
         except Exception as e:
diff --git i/src/media/download_queue.py w/src/media/download_queue.py
index 7f473e0..5087b56 100644
--- i/src/media/download_queue.py
+++ w/src/media/download_queue.py
@@ -43,7 +43,7 @@ class DownloadStatus(Enum):
     CANCELLED = "cancelled"
 
 
-@dataclass
+@dataclass(slots=True)
 class DownloadTask:
     """A single download task in the queue."""
 
@@ -77,7 +77,7 @@ class DownloadTask:
         return start - self.created_at
 
 
-@dataclass
+@dataclass(slots=True)
 class QueueStats:
     """Statistics for the download queue."""
 
diff --git i/src/media/downloader.py w/src/media/downloader.py
index 66c5903..4a310ad 100644
--- i/src/media/downloader.py
+++ w/src/media/downloader.py
@@ -10,13 +10,16 @@ import os
 import re
 import time
 from pathlib import Path
-from typing import Any, Optional
+from typing import Any, Dict, Optional
 
 from loguru import logger
 from telethon import utils
 from telethon.tl.functions import InvokeWithTakeoutRequest
 from telethon.tl.types import Message
 
+# B-6: Hash-based deduplication
+from src.media.hash_dedup import HashBasedDeduplicator
+
 
 class TelegramServerError(Exception):
     """Raised when Telegram servers are having issues (not client-side problem)."""
@@ -244,6 +247,8 @@ class MediaDownloader:
         temp_dir: Path,
         client: Any = None,
         worker_clients: Optional[list] = None,
+        cache_manager: Optional[Any] = None,
+        config: Optional[Any] = None,
     ):
         """
         Инициализация загрузчика медиа.
@@ -253,11 +258,18 @@ class MediaDownloader:
             temp_dir: Директория для временных файлов
             client: Основной клиент Telegram
             worker_clients: Список дополнительных клиентов (воркеров) для распределения нагрузки
+            cache_manager: Менеджер кэша для дедупликации загрузок
+            config: Конфигурация приложения
         """
         self.connection_manager = connection_manager
         self.temp_dir = temp_dir
         self.client = client
         self.worker_clients = worker_clients or []
+        self.cache_manager = cache_manager
+        self.config = config
+        
+        # In-memory cache for current session deduplication
+        self._downloaded_cache: Dict[str, Path] = {}
 
         # Статистика загрузок
         self._persistent_download_attempts = 0
@@ -268,6 +280,61 @@ class MediaDownloader:
         # Настройки из environment
         self._persistent_enabled = PERSISTENT_DOWNLOAD_MODE
         self._persistent_min_size_mb = PERSISTENT_MIN_SIZE_MB
+        
+        # B-6: Hash-based deduplication
+        if config and hasattr(config, 'performance') and config.performance.hash_based_deduplication:
+            # Determine cache directory
+            if hasattr(config, 'cache_path'):
+                cache_dir = Path(config.cache_path)
+            else:
+                cache_dir = temp_dir.parent / 'cache'
+            cache_dir.mkdir(parents=True, exist_ok=True)
+            
+            self._hash_dedup = HashBasedDeduplicator(
+                cache_path=cache_dir / "media_hash_cache.msgpack",
+                max_cache_size=config.performance.hash_cache_max_size,
+                enable_api_hashing=True
+            )
+            logger.info("🔐 Hash-based deduplication ENABLED")
+        else:
+            self._hash_dedup = None
+            logger.info("ID-based deduplication only (hash dedup disabled)")
+
+    def _get_file_key(self, message: Message) -> Optional[str]:
+        """
+        Generate a unique key for the media file to prevent duplicate downloads.
+        """
+        if not hasattr(message, "media") or not message.media:
+            return None
+            
+        media = message.media
+        try:
+            if hasattr(media, "document") and media.document:
+                # Document ID + Access Hash is unique
+                return f"doc_{media.document.id}_{media.document.access_hash}"
+            elif hasattr(media, "photo") and media.photo:
+                # Photo ID + Access Hash is unique
+                return f"photo_{media.photo.id}_{media.photo.access_hash}"
+        except Exception:
+            pass
+            
+        return None
+
+    def _get_part_size(self, file_size: int) -> int:
+        """Determine optimal part size for downloading."""
+        # Use configured value if set (and not 0/auto)
+        if self.config and hasattr(self.config, "performance"):
+            configured_kb = getattr(self.config.performance, "part_size_kb", 0)
+            if configured_kb > 0:
+                return configured_kb
+        
+        # Auto-tuning
+        if file_size < 10 * 1024 * 1024:  # < 10MB
+            return 128
+        elif file_size < 100 * 1024 * 1024:  # < 100MB
+            return 256
+        else:  # > 100MB
+            return 512
 
     async def download_media(
         self,
@@ -278,8 +345,10 @@ class MediaDownloader:
         """
         Главный метод загрузки медиафайла из сообщения.
 
-        Автоматически выбирает стратегию загрузки на основе размера файла
-        и настроек окружения.
+        Использует трёх-уровневую дедупликацию:
+        1. TIER 1: Hash-based (content matching) - highest precision
+        2. TIER 2: ID-based (existing) - fast fallback
+        3. TIER 3: Download - if both caches miss
 
         Args:
             message: Telegram сообщение с медиа
@@ -293,6 +362,64 @@ class MediaDownloader:
             logger.warning("Message has no file attribute or file is None")
             return None
 
+        # Store file_hash for later cache updates
+        file_hash: Optional[str] = None
+        
+        # === TIER 1: Hash-Based Deduplication (B-6) ===
+        if self._hash_dedup:
+            try:
+                # Get file hash from Telegram API
+                file_hash = await self._hash_dedup.get_file_hash(
+                    client=self.client,
+                    media=message.media,
+                    timeout=self.config.performance.hash_api_timeout
+                )
+                
+                if file_hash:
+                    # Check hash cache
+                    cached_path = await self._hash_dedup.check_cache(file_hash)
+                    if cached_path:
+                        # CACHE HIT: Reuse existing file
+                        logger.info(
+                            f"✅ Hash dedup HIT: msg {message.id} -> "
+                            f"{cached_path.name}"
+                        )
+                        return cached_path
+                    else:
+                        logger.debug(
+                            f"Hash dedup MISS: {file_hash[:16]}... (will check ID cache)"
+                        )
+            except Exception as e:
+                logger.warning(f"Hash dedup failed: {e}, falling back to ID-based")
+        
+        # === TIER 2: ID-Based Deduplication (Existing) ===
+        file_key = self._get_file_key(message)
+        if file_key:
+            # 1. Check in-memory cache
+            if file_key in self._downloaded_cache:
+                cached_path = self._downloaded_cache[file_key]
+                if cached_path.exists() and cached_path.stat().st_size > 0:
+                    logger.debug(f"♻️ ID dedup hit (memory): {file_key}")
+                    # Update hash cache if we have the hash
+                    if self._hash_dedup and file_hash:
+                        self._hash_dedup.add_to_cache(file_hash, cached_path)
+                    return cached_path
+            
+            # 2. Check persistent cache manager
+            if self.cache_manager and hasattr(self.cache_manager, "get_file_path"):
+                cached_path_str = await self.cache_manager.get_file_path(file_key)
+                if cached_path_str:
+                    cached_path = Path(cached_path_str)
+                    if cached_path.exists() and cached_path.stat().st_size > 0:
+                        logger.debug(f"♻️ ID dedup hit (persistent): {file_key}")
+                        # Update memory cache
+                        self._downloaded_cache[file_key] = cached_path
+                        # Update hash cache if we have the hash
+                        if self._hash_dedup and file_hash:
+                            self._hash_dedup.add_to_cache(file_hash, cached_path)
+                        return cached_path
+
+        # === TIER 3: Download ===
         expected_size = getattr(message.file, "size", 0)
         if expected_size == 0:
             logger.warning(f"Message {message.id} has zero file size")
@@ -300,18 +427,34 @@ class MediaDownloader:
 
         file_size_mb = expected_size / (1024 * 1024)
         logger.info(
-            f"Starting download for message {message.id}: {file_size_mb:.2f} MB"
+            f"Downloading message {message.id}: {file_size_mb:.2f} MB"
         )
 
+        result_path = None
         # Используем persistent download для всех файлов (guaranteed completion)
         if self._persistent_enabled:
-            return await self._persistent_download(
+            result_path = await self._persistent_download(
                 message, expected_size, progress_queue, task_id
             )
         else:
-            return await self._standard_download(
+            result_path = await self._standard_download(
                 message, expected_size, progress_queue, task_id
             )
+            
+        # === Update ALL Caches on Success ===
+        if result_path and result_path.exists():
+            # Update ID cache (existing)
+            if file_key:
+                self._downloaded_cache[file_key] = result_path
+                if self.cache_manager and hasattr(self.cache_manager, "store_file_path"):
+                    await self.cache_manager.store_file_path(file_key, str(result_path))
+            
+            # Update hash cache (B-6 new)
+            if self._hash_dedup and file_hash:
+                self._hash_dedup.add_to_cache(file_hash, result_path)
+                logger.debug(f"Updated hash cache: {file_hash[:16]}... -> {result_path.name}")
+                
+        return result_path
 
     async def _persistent_download(
         self,
@@ -441,7 +584,7 @@ class MediaDownloader:
                                 location,
                                 file=temp_path,
                                 progress_callback=progress_callback,
-                                part_size_kb=512,
+                                part_size_kb=self._get_part_size(expected_size),
                             ),
                             timeout=chunk_timeout,
                         )
@@ -707,15 +850,27 @@ class MediaDownloader:
 
                 # Загрузка с семафором
                 async with self.connection_manager.download_semaphore:
-                    # Use standard download_media for stability
-                    await asyncio.wait_for(
-                        download_client.download_media(
-                            message,
-                            file=temp_path,
-                            progress_callback=progress_callback,
-                        ),
-                        timeout=base_timeout,
-                    )
+                    try:
+                        location = utils.get_input_location(message.media)
+                        await asyncio.wait_for(
+                            download_client.download_file(
+                                location,
+                                file=temp_path,
+                                progress_callback=progress_callback,
+                                part_size_kb=self._get_part_size(expected_size),
+                            ),
+                            timeout=base_timeout,
+                        )
+                    except Exception:
+                        # Fallback to download_media if download_file fails (e.g. location extraction issue)
+                        await asyncio.wait_for(
+                            download_client.download_media(
+                                message,
+                                file=temp_path,
+                                progress_callback=progress_callback,
+                            ),
+                            timeout=base_timeout,
+                        )
 
                 # Проверяем успешность загрузки
                 if temp_path.exists():
@@ -803,6 +958,9 @@ class MediaDownloader:
             if self._standard_download_attempts > 0
             else 0
         )
+        
+        # B-6: Include hash dedup stats
+        hash_dedup_stats = self.get_hash_dedup_stats() if self._hash_dedup else {}
 
         return {
             "persistent_downloads": {
@@ -817,7 +975,19 @@ class MediaDownloader:
                 "successes": self._standard_download_successes,
                 "success_rate_percent": standard_success_rate,
             },
+            "hash_deduplication": hash_dedup_stats,  # B-6: Hash-based dedup stats
         }
+    
+    def get_hash_dedup_stats(self) -> Dict[str, int]:
+        """
+        Get hash-based deduplication statistics.
+        
+        Returns:
+            Dictionary with hash dedup stats or empty dict if disabled
+        """
+        if self._hash_dedup:
+            return self._hash_dedup.get_stats()
+        return {}
 
     def log_statistics(self) -> None:
         """Логировать статистику загрузок."""
diff --git i/src/media/hardware.py w/src/media/hardware.py
index 601220e..5cd49d8 100644
--- i/src/media/hardware.py
+++ w/src/media/hardware.py
@@ -11,6 +11,9 @@ from typing import Any, Dict, Optional
 
 from loguru import logger
 
+# TIER C-1: Import VA-API Auto-Detection
+from .vaapi_detector import VAAPIStatus, get_vaapi_capabilities
+
 
 class HardwareAccelerationDetector:
     """Детектор аппаратного ускорения."""
@@ -28,46 +31,56 @@ class HardwareAccelerationDetector:
         }
 
     async def detect_hardware_acceleration(self) -> Dict[str, bool]:
-        """Проверка доступности VA-API кодека в FFmpeg с реальным тестированием."""
+        """Проверка доступности VA-API кодека с auto-detection через vainfo."""
         if self._detection_complete:
             return self.available_encoders
 
         try:
-            # Проверяем доступность VA-API устройства
+            # TIER C-1: Auto-detect VA-API using vainfo command
             vaapi_device = (
-                getattr(self.config, "vaapi_device", "/dev/dri/renderD128")
+                getattr(self.config, "vaapi_device_path", "/dev/dri/renderD128")
                 if self.config
                 else "/dev/dri/renderD128"
             )
-            if not self._check_vaapi_device(vaapi_device):
-                logger.warning(f"VA-API device {vaapi_device} not accessible")
+            
+            # Check if force CPU transcode is enabled
+            force_cpu = (
+                getattr(self.config, "force_cpu_transcode", False)
+                if self.config
+                else False
+            )
+            
+            if force_cpu:
+                logger.info("🐢 Force CPU transcoding enabled (FORCE_CPU_TRANSCODE=true)")
                 self.available_encoders["vaapi"] = False
                 self._detection_complete = True
                 return self.available_encoders
-
-            # Получаем список доступных кодеров
-            proc = await asyncio.create_subprocess_exec(
-                "ffmpeg",
-                "-hide_banner",
-                "-encoders",
-                stdout=asyncio.subprocess.PIPE,
-                stderr=asyncio.subprocess.PIPE,
-            )
-            stdout, _ = await proc.communicate()
-            encoders_output = stdout.decode("utf-8", errors="ignore")
-
-            # Проверяем наличие VA-API кодека
-            if "h264_vaapi" in encoders_output:
-                # Тестируем VA-API кодер
-                if await self._test_hardware_encoder("h264_vaapi"):
-                    self.available_encoders["vaapi"] = True
-                    logger.info("VA-API hardware encoder h264_vaapi is working")
+            
+            # Run VA-API detection
+            vaapi_caps = get_vaapi_capabilities(device_path=vaapi_device)
+            
+            if vaapi_caps.status == VAAPIStatus.AVAILABLE:
+                # Verify h264_vaapi encoder is in the list
+                if "h264_vaapi" in vaapi_caps.encoders:
+                    # Test the encoder with FFmpeg
+                    if await self._test_hardware_encoder("h264_vaapi"):
+                        self.available_encoders["vaapi"] = True
+                        logger.info(
+                            f"✅ VA-API ready: {vaapi_caps.driver} "
+                            f"(encoders: {', '.join(vaapi_caps.encoders)})"
+                        )
+                    else:
+                        self.available_encoders["vaapi"] = False
+                        logger.warning("VA-API detected but h264_vaapi encoder failed test")
                 else:
                     self.available_encoders["vaapi"] = False
-                    logger.warning("VA-API hardware encoder h264_vaapi failed test")
+                    logger.warning("VA-API detected but h264_vaapi encoder not available")
             else:
                 self.available_encoders["vaapi"] = False
-                logger.warning("VA-API encoder not found in FFmpeg")
+                if vaapi_caps.status == VAAPIStatus.UNAVAILABLE:
+                    logger.info("VA-API unavailable - using CPU encoding")
+                else:
+                    logger.warning("VA-API detection error - falling back to CPU encoding")
 
         except Exception as e:
             logger.warning(f"VA-API detection failed: {e}")
@@ -75,11 +88,6 @@ class HardwareAccelerationDetector:
 
         self._detection_complete = True
 
-        if self.available_encoders["vaapi"]:
-            logger.info("VA-API hardware acceleration is available")
-        else:
-            logger.info("VA-API not available, using software encoding")
-
         return self.available_encoders
 
     def _get_encoder_name(self, encoder_type: str) -> str:
@@ -106,7 +114,7 @@ class HardwareAccelerationDetector:
             elif "vaapi" in encoder:
                 # VA-API требует специальную настройку
                 vaapi_device = (
-                    getattr(self.config, "vaapi_device", "/dev/dri/renderD128")
+                    getattr(self.config, "vaapi_device_path", "/dev/dri/renderD128")
                     if self.config
                     else "/dev/dri/renderD128"
                 )
diff --git i/src/media/lazy_loader.py w/src/media/lazy_loader.py
index 0f1c5e6..b3f8d74 100644
--- i/src/media/lazy_loader.py
+++ w/src/media/lazy_loader.py
@@ -16,7 +16,7 @@ from telethon.tl.types import Message
 from .manager import MediaProcessor
 
 
-@dataclass
+@dataclass(slots=True)
 class LazyMediaMetadata:
     """Metadata for lazy-loaded media."""
 
diff --git i/src/media/manager.py w/src/media/manager.py
index 21ed07f..cddc197 100644
--- i/src/media/manager.py
+++ w/src/media/manager.py
@@ -10,7 +10,6 @@ import os
 import shutil
 import tempfile
 import time
-from concurrent.futures import ThreadPoolExecutor
 from pathlib import Path
 from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union
 
@@ -19,6 +18,7 @@ import aiofiles.os
 from loguru import logger
 from telethon.tl.types import Message
 
+from src.core.thread_pool import get_thread_pool  # 🧵 TIER B - B-1: Unified thread pool
 from src.utils import sanitize_filename
 
 from .cache import MediaCache
@@ -80,24 +80,26 @@ class MediaProcessor:
         # Создание временной директории
         self.temp_dir.mkdir(exist_ok=True)
 
-        # Пулы потоков для разных типов операций
-        self.io_executor = ThreadPoolExecutor(
-            max_workers=max_workers, thread_name_prefix="media_io"
-        )
-        self.cpu_executor = ThreadPoolExecutor(
-            max_workers=max_workers // 2 or 1, thread_name_prefix="media_cpu"
-        )
-        self.ffmpeg_executor = ThreadPoolExecutor(
-            max_workers=max_workers // 2 or 1, thread_name_prefix="ffmpeg"
-        )
+        # 🧵 TIER B - B-1: Use unified thread pool instead of local executors
+        # This eliminates thread contention between download/processing/ffmpeg operations
+        self._thread_pool = get_thread_pool()
+        logger.info(f"📊 MediaProcessor using UnifiedThreadPool (global singleton)")
+
+        # Legacy compatibility: keep references as properties for now
+        # These will be removed in future versions once all processors are updated
+        self.io_executor = None  # Deprecated - use _thread_pool.submit() instead
+        self.cpu_executor = None  # Deprecated - use _thread_pool.submit() instead
+        self.ffmpeg_executor = None  # Deprecated - use _thread_pool.submit() instead
 
         # Настройки обработки по умолчанию
         self.default_processing = ProcessingSettings()
 
         # Инициализация модульных компонентов
         self._hw_detector = HardwareAccelerationDetector(config)
-        self._metadata_extractor = MetadataExtractor(self.io_executor)
-        self._validator = MediaValidator(self.io_executor)
+        self._metadata_extractor = MetadataExtractor(
+            self._thread_pool
+        )  # Pass thread pool
+        self._validator = MediaValidator(self._thread_pool)  # Pass thread pool
         self._cache = MediaCache(cache_manager)
 
         # Downloader будет инициализирован после start()
@@ -164,6 +166,8 @@ class MediaProcessor:
                 temp_dir=self.temp_dir,
                 client=self.client,
                 worker_clients=self.worker_clients,
+                cache_manager=self.cache_manager,
+                config=self.config,
             )
 
             # 🚀 Инициализация Background Download Queue (если включено)
@@ -179,10 +183,9 @@ class MediaProcessor:
                     f"🚀 Background download queue started with {workers} workers"
                 )
 
-            # Инициализация процессоров
+            # Инициализация процессоров (используют unified thread pool)
             self._video_processor = VideoProcessor(
-                io_executor=self.io_executor,
-                cpu_executor=self.cpu_executor,
+                thread_pool=self._thread_pool,  # 🧵 TIER B - B-1
                 hw_detector=self._hw_detector,
                 metadata_extractor=self._metadata_extractor,
                 config=self.config,
@@ -190,16 +193,14 @@ class MediaProcessor:
             )
 
             self._audio_processor = AudioProcessor(
-                io_executor=self.io_executor,
-                cpu_executor=self.cpu_executor,
+                thread_pool=self._thread_pool,  # 🧵 TIER B - B-1
                 validator=self._validator,
                 config=self.config,
                 settings=self.default_processing,
             )
 
             self._image_processor = ImageProcessor(
-                io_executor=self.io_executor,
-                cpu_executor=self.cpu_executor,
+                thread_pool=self._thread_pool,  # 🧵 TIER B - B-1
                 config=self.config,
                 settings=self.default_processing,
             )
@@ -220,6 +221,7 @@ class MediaProcessor:
                     cache_dir = None
                     if transcription_config.cache_dir:
                         from pathlib import Path
+
                         cache_dir = Path(transcription_config.cache_dir)
 
                     self._transcriber = WhisperTranscriber(
@@ -370,7 +372,9 @@ class MediaProcessor:
         Returns placeholder paths immediately - actual files will be downloaded
         by background workers.
         """
-        assert self._download_queue is not None, "Download queue must be initialized for async downloads"
+        assert self._download_queue is not None, (
+            "Download queue must be initialized for async downloads"
+        )
         result_paths = []
 
         for media_type, msg in media_items:
@@ -996,18 +1000,30 @@ class MediaProcessor:
                 return False
 
     async def _copy_file(self, task: ProcessingTask) -> bool:
-        """Простое копирование файла при невозможности обработки."""
-        try:
-            async with aiofiles.open(task.input_path, "rb") as src:
-                async with aiofiles.open(task.output_path, "wb") as dst:
-                    while chunk := await src.read(8192 * 1024):
-                        await dst.write(chunk)
+        """
+        Простое копирование файла с zero-copy оптимизацией при невозможности обработки.
 
-            if task.output_path.exists() and task.output_path.stat().st_size > 0:
+        Uses os.sendfile() on supported platforms for efficient kernel-level copying.
+        """
+        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer
+
+        try:
+            # Zero-copy transfer with config
+            config = ZeroCopyConfig(
+                enabled=True, min_size_mb=10, verify_copy=True, chunk_size_mb=64
+            )
+
+            transfer = get_zero_copy_transfer(config)
+            success = await transfer.copy_file(
+                task.input_path, task.output_path, verify=True
+            )
+
+            if success:
                 logger.info(f"File copied successfully: {task.output_path}")
                 return True
-
-            return False
+            else:
+                logger.error("Zero-copy transfer failed")
+                return False
 
         except Exception as e:
             logger.error(f"File copy failed: {e}")
@@ -1388,9 +1404,12 @@ class MediaProcessor:
         # Закрытие пулов потоков
         try:
             logger.debug("Shutting down thread executors...")
-            self.io_executor.shutdown(wait=True)
-            self.cpu_executor.shutdown(wait=True)
-            self.ffmpeg_executor.shutdown(wait=True)
+            if self.io_executor:
+                self.io_executor.shutdown(wait=True)
+            if self.cpu_executor:
+                self.cpu_executor.shutdown(wait=True)
+            if self.ffmpeg_executor:
+                self.ffmpeg_executor.shutdown(wait=True)
             logger.debug("Thread executors shut down successfully")
         except Exception as e:
             logger.error(f"Error during executor shutdown: {e}")
diff --git i/src/media/metadata.py w/src/media/metadata.py
index 77e9078..64c6066 100644
--- i/src/media/metadata.py
+++ w/src/media/metadata.py
@@ -8,6 +8,7 @@ Provides caching for improved performance.
 import asyncio
 import hashlib
 import mimetypes
+from fractions import Fraction
 from pathlib import Path
 from typing import Any, Dict
 
@@ -20,13 +21,41 @@ from PIL import Image
 from .models import MediaMetadata
 
 
+def _parse_frame_rate(rate_str: str) -> float:
+    """
+    Безопасный парсинг frame rate из ffprobe (например, '30/1' или '24000/1001').
+    
+    Args:
+        rate_str: Строка с frame rate в формате 'num/den' или просто число
+        
+    Returns:
+        float: FPS значение (fallback: 30.0 при ошибках)
+    """
+    try:
+        if '/' in rate_str:
+            return float(Fraction(rate_str))
+        return float(rate_str)
+    except (ValueError, ZeroDivisionError) as e:
+        logger.warning(f"Invalid frame rate '{rate_str}': {e}, defaulting to 30.0")
+        return 30.0
+
+
 class MetadataExtractor:
     """Извлекает метаданные из медиафайлов."""
 
-    def __init__(self, io_executor):
-        self.io_executor = io_executor
+    def __init__(self, thread_pool):
+        """
+        Initialize metadata extractor.
+        
+        Args:
+            thread_pool: Unified thread pool for CPU-bound operations
+        """
+        self.thread_pool = thread_pool  # 🧵 TIER B - B-1
         self._metadata_cache: Dict[str, MediaMetadata] = {}
         self._file_checksums: Dict[Path, str] = {}
+        
+        # Legacy compatibility
+        self.io_executor = None
 
     async def get_metadata(self, file_path: Path, media_type: str) -> MediaMetadata:
         """Получение метаданных медиа файла."""
@@ -98,7 +127,7 @@ class MetadataExtractor:
                                 "width": int(video_stream.get("width", 0)),
                                 "height": int(video_stream.get("height", 0)),
                                 "duration": float(video_stream.get("duration", 0)),
-                                "fps": eval(video_stream.get("r_frame_rate", "0/1")),
+                                "fps": _parse_frame_rate(video_stream.get("r_frame_rate", "0/1")),
                                 "codec": video_stream.get("codec_name"),
                                 "bitrate": int(video_stream.get("bit_rate", 0)),
                             }
diff --git i/src/media/models.py w/src/media/models.py
index 85ddcb9..0001782 100644
--- i/src/media/models.py
+++ w/src/media/models.py
@@ -10,7 +10,7 @@ from pathlib import Path
 from typing import Optional, Tuple
 
 
-@dataclass
+@dataclass(slots=True)
 class MediaMetadata:
     """Метаданные медиа файла."""
 
@@ -28,7 +28,7 @@ class MediaMetadata:
     checksum: Optional[str] = None
 
 
-@dataclass
+@dataclass(slots=True)
 class ProcessingSettings:
     """Настройки обработки медиа."""
 
@@ -44,7 +44,7 @@ class ProcessingSettings:
     aggressive_compression: bool = False
 
 
-@dataclass
+@dataclass(slots=True)
 class ProcessingTask:
     """Задача обработки медиа."""
 
diff --git i/src/media/processors/audio.py w/src/media/processors/audio.py
index 6b58a49..54ae5af 100644
--- i/src/media/processors/audio.py
+++ w/src/media/processors/audio.py
@@ -25,8 +25,7 @@ class AudioProcessor(BaseProcessor):
 
     def __init__(
         self,
-        io_executor: Any,
-        cpu_executor: Any,
+        thread_pool: Any,  # UnifiedThreadPool instance
         validator: Any,
         config: Optional[Any] = None,
         settings: Optional[ProcessingSettings] = None,
@@ -35,13 +34,12 @@ class AudioProcessor(BaseProcessor):
         Инициализация аудиопроцессора.
 
         Args:
-            io_executor: Executor для IO операций
-            cpu_executor: Executor для CPU-интенсивных операций (FFmpeg)
+            thread_pool: Unified thread pool для всех операций
             validator: MediaValidator для проверки целостности файлов
             config: Объект конфигурации
             settings: Настройки обработки по умолчанию
         """
-        super().__init__(io_executor, cpu_executor, settings)
+        super().__init__(thread_pool, settings)  # 🧵 TIER B - B-1
         self.validator = validator
         self.config = config
 
@@ -265,7 +263,7 @@ class AudioProcessor(BaseProcessor):
 
     async def _copy_file(self, task: ProcessingTask) -> bool:
         """
-        Копирование файла как fallback при ошибках обработки.
+        Копирование файла с zero-copy оптимизацией как fallback при ошибках обработки.
 
         Args:
             task: Задача обработки
@@ -273,6 +271,8 @@ class AudioProcessor(BaseProcessor):
         Returns:
             True если копирование успешно
         """
+        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer
+
         max_attempts = 3
 
         for attempt in range(max_attempts):
@@ -294,32 +294,30 @@ class AudioProcessor(BaseProcessor):
                 if task.output_path.exists():
                     task.output_path.unlink()
 
-                # Копирование файла
-                copied_bytes = 0
-                async with aiofiles.open(task.input_path, "rb") as src:
-                    async with aiofiles.open(task.output_path, "wb") as dst:
-                        while chunk := await src.read(64 * 1024):  # 64KB chunks
-                            await dst.write(chunk)
-                            copied_bytes += len(chunk)
-                        await dst.flush()
-
-                # Проверка результата
-                if not task.output_path.exists():
-                    logger.error(f"Output file was not created: {task.output_path}")
+                # Zero-copy transfer
+                config = ZeroCopyConfig(
+                    enabled=True,
+                    min_size_mb=10,
+                    verify_copy=True,
+                    chunk_size_mb=64
+                )
+                
+                transfer = get_zero_copy_transfer(config)
+                success = await transfer.copy_file(
+                    task.input_path,
+                    task.output_path,
+                    verify=True
+                )
+                
+                if success:
+                    self._audio_copied_count += 1
+                    return True
+                else:
+                    logger.error(f"Zero-copy failed on attempt {attempt + 1}")
+                    if attempt < max_attempts - 1:
+                        await asyncio.sleep(1)
                     continue
 
-                output_size = task.output_path.stat().st_size
-
-                if output_size != source_size:
-                    logger.error(
-                        f"File copy size mismatch! Source: {source_size} bytes, "
-                        f"Output: {output_size} bytes (attempt {attempt + 1})"
-                    )
-                    continue
-
-                self._audio_copied_count += 1
-                return True
-
             except Exception as e:
                 logger.error(f"File copy failed on attempt {attempt + 1}: {e}")
                 if attempt < max_attempts - 1:
diff --git i/src/media/processors/base.py w/src/media/processors/base.py
index ce55c97..a569b00 100644
--- i/src/media/processors/base.py
+++ w/src/media/processors/base.py
@@ -6,7 +6,7 @@ Defines the abstract interface that all media processors must implement.
 
 from abc import ABC, abstractmethod
 from pathlib import Path
-from typing import Optional
+from typing import Any, Optional
 
 from ..models import ProcessingSettings, ProcessingTask
 
@@ -15,11 +15,23 @@ class BaseProcessor(ABC):
     """Базовый класс для всех процессоров медиа."""
 
     def __init__(
-        self, io_executor, cpu_executor, settings: Optional[ProcessingSettings] = None
+        self, 
+        thread_pool: Any,  # UnifiedThreadPool instance
+        settings: Optional[ProcessingSettings] = None
     ):
-        self.io_executor = io_executor
-        self.cpu_executor = cpu_executor
+        """
+        Initialize base processor.
+        
+        Args:
+            thread_pool: Unified thread pool for CPU-bound operations
+            settings: Processing settings
+        """
+        self.thread_pool = thread_pool
         self.settings = settings or ProcessingSettings()
+        
+        # Legacy compatibility - deprecated, will be removed
+        self.io_executor = None
+        self.cpu_executor = None
 
     @abstractmethod
     async def process(self, task: ProcessingTask, worker_name: str) -> bool:
diff --git i/src/media/processors/image.py w/src/media/processors/image.py
index 72e9135..b7d3406 100644
--- i/src/media/processors/image.py
+++ w/src/media/processors/image.py
@@ -26,8 +26,7 @@ class ImageProcessor(BaseProcessor):
 
     def __init__(
         self,
-        io_executor: Any,
-        cpu_executor: Any,
+        thread_pool: Any,  # UnifiedThreadPool instance
         config: Optional[Any] = None,
         settings: Optional[ProcessingSettings] = None,
     ):
@@ -35,12 +34,11 @@ class ImageProcessor(BaseProcessor):
         Инициализация процессора изображений.
 
         Args:
-            io_executor: Executor для IO операций
-            cpu_executor: Executor для CPU-интенсивных операций (PIL)
+            thread_pool: Unified thread pool для всех операций
             config: Объект конфигурации
             settings: Настройки обработки по умолчанию
         """
-        super().__init__(io_executor, cpu_executor, settings)
+        super().__init__(thread_pool, settings)  # 🧵 TIER B - B-1
         self.config = config
 
         # Статистика
@@ -271,7 +269,7 @@ class ImageProcessor(BaseProcessor):
 
     async def _copy_file(self, task: ProcessingTask) -> bool:
         """
-        Копирование файла как fallback при ошибках обработки.
+        Копирование файла с zero-copy оптимизацией как fallback при ошибках обработки.
 
         Args:
             task: Задача обработки
@@ -279,6 +277,8 @@ class ImageProcessor(BaseProcessor):
         Returns:
             True если копирование успешно
         """
+        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer
+
         max_attempts = 3
 
         for attempt in range(max_attempts):
@@ -300,32 +300,30 @@ class ImageProcessor(BaseProcessor):
                 if task.output_path.exists():
                     task.output_path.unlink()
 
-                # Копирование файла
-                copied_bytes = 0
-                async with aiofiles.open(task.input_path, "rb") as src:
-                    async with aiofiles.open(task.output_path, "wb") as dst:
-                        while chunk := await src.read(64 * 1024):  # 64KB chunks
-                            await dst.write(chunk)
-                            copied_bytes += len(chunk)
-                        await dst.flush()
-
-                # Проверка результата
-                if not task.output_path.exists():
-                    logger.error(f"Output file was not created: {task.output_path}")
+                # Zero-copy transfer
+                config = ZeroCopyConfig(
+                    enabled=True,
+                    min_size_mb=10,
+                    verify_copy=True,
+                    chunk_size_mb=64
+                )
+                
+                transfer = get_zero_copy_transfer(config)
+                success = await transfer.copy_file(
+                    task.input_path,
+                    task.output_path,
+                    verify=True
+                )
+                
+                if success:
+                    self._image_copied_count += 1
+                    return True
+                else:
+                    logger.error(f"Zero-copy failed on attempt {attempt + 1}")
+                    if attempt < max_attempts - 1:
+                        await asyncio.sleep(1)
                     continue
 
-                output_size = task.output_path.stat().st_size
-
-                if output_size != source_size:
-                    logger.error(
-                        f"File copy size mismatch! Source: {source_size} bytes, "
-                        f"Output: {output_size} bytes (attempt {attempt + 1})"
-                    )
-                    continue
-
-                self._image_copied_count += 1
-                return True
-
             except Exception as e:
                 logger.error(f"File copy failed on attempt {attempt + 1}: {e}")
                 if attempt < max_attempts - 1:
diff --git i/src/media/processors/transcription.py w/src/media/processors/transcription.py
index a8aec3f..3e48cff 100644
--- i/src/media/processors/transcription.py
+++ w/src/media/processors/transcription.py
@@ -32,7 +32,7 @@ except ImportError:
 
 
 
-@dataclass
+@dataclass(slots=True)
 class TranscriptionResult:
     """
     Result of audio transcription.
diff --git i/src/media/processors/video.py w/src/media/processors/video.py
index ea1d457..b29ecb7 100644
--- i/src/media/processors/video.py
+++ w/src/media/processors/video.py
@@ -26,8 +26,7 @@ class VideoProcessor(BaseProcessor):
 
     def __init__(
         self,
-        io_executor: Any,
-        cpu_executor: Any,
+        thread_pool: Any,  # UnifiedThreadPool instance
         hw_detector: Any,
         metadata_extractor: Any,
         config: Optional[Any] = None,
@@ -37,14 +36,13 @@ class VideoProcessor(BaseProcessor):
         Инициализация видеопроцессора.
 
         Args:
-            io_executor: Executor для IO операций
-            cpu_executor: Executor для CPU-интенсивных операций (FFmpeg)
+            thread_pool: Unified thread pool для всех операций
             hw_detector: HardwareAccelerationDetector для проверки GPU
             metadata_extractor: MetadataExtractor для извлечения метаданных
             config: Объект конфигурации
             settings: Настройки обработки по умолчанию
         """
-        super().__init__(io_executor, cpu_executor, settings)
+        super().__init__(thread_pool, settings)  # 🧵 TIER B - B-1
         self.hw_detector = hw_detector
         self.metadata_extractor = metadata_extractor
         self.config = config
@@ -623,7 +621,7 @@ class VideoProcessor(BaseProcessor):
 
     async def _copy_file(self, task: ProcessingTask) -> bool:
         """
-        Копирование файла как fallback при ошибках обработки.
+        Копирование файла с zero-copy оптимизацией как fallback при ошибках обработки.
 
         Args:
             task: Задача обработки
@@ -631,6 +629,8 @@ class VideoProcessor(BaseProcessor):
         Returns:
             True если копирование успешно
         """
+        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer
+
         max_attempts = 3
 
         for attempt in range(max_attempts):
@@ -652,32 +652,28 @@ class VideoProcessor(BaseProcessor):
                 if task.output_path.exists():
                     task.output_path.unlink()
 
-                # Копирование файла
-                copied_bytes = 0
-                async with aiofiles.open(task.input_path, "rb") as src:
-                    async with aiofiles.open(task.output_path, "wb") as dst:
-                        while chunk := await src.read(64 * 1024):  # 64KB chunks
-                            await dst.write(chunk)
-                            copied_bytes += len(chunk)
-                        await dst.flush()
-
-                # Проверка результата
-                if not task.output_path.exists():
-                    logger.error(f"Output file was not created: {task.output_path}")
+                # Zero-copy transfer
+                config = ZeroCopyConfig(
+                    enabled=True,
+                    min_size_mb=10,
+                    verify_copy=True,
+                    chunk_size_mb=64
+                )
+                
+                transfer = get_zero_copy_transfer(config)
+                success = await transfer.copy_file(
+                    task.input_path,
+                    task.output_path,
+                    verify=True
+                )
+                
+                if success:
+                    self._video_copied_count += 1
+                    return True
+                else:
+                    logger.error(f"Zero-copy failed on attempt {attempt + 1}")
                     continue
 
-                output_size = task.output_path.stat().st_size
-
-                if output_size != source_size:
-                    logger.error(
-                        f"File copy size mismatch! Source: {source_size} bytes, "
-                        f"Output: {output_size} bytes (attempt {attempt + 1})"
-                    )
-                    continue
-
-                self._video_copied_count += 1
-                return True
-
             except Exception as e:
                 logger.error(f"File copy failed on attempt {attempt + 1}: {e}")
                 if attempt < max_attempts - 1:
diff --git i/src/media/validators.py w/src/media/validators.py
index ce5b2a1..20391f3 100644
--- i/src/media/validators.py
+++ w/src/media/validators.py
@@ -15,8 +15,17 @@ from PIL import Image
 class MediaValidator:
     """Проверяет целостность медиафайлов."""
 
-    def __init__(self, io_executor):
-        self.io_executor = io_executor
+    def __init__(self, thread_pool):
+        """
+        Initialize media validator.
+        
+        Args:
+            thread_pool: Unified thread pool for CPU-bound operations
+        """
+        self.thread_pool = thread_pool  # 🧵 TIER B - B-1
+        
+        # Legacy compatibility
+        self.io_executor = None
 
     async def validate_file_integrity(self, file_path: Path) -> bool:
         """Проверка целостности скачанного файла."""
diff --git i/src/telegram_client.py w/src/telegram_client.py
index a4f1bd6..9e16b83 100644
--- i/src/telegram_client.py
+++ w/src/telegram_client.py
@@ -16,12 +16,13 @@ try:
     from telethon.errors.rpcerrorlist import PhoneCodeInvalidError as PhoneCodeInvalidErrorRPC
 except Exception:
     PhoneCodeInvalidErrorRPC = PhoneCodeInvalidError
-from telethon.tl.functions.channels import GetFullChannelRequest
-from telethon.tl.types import Channel, Message, User
+from telethon.tl.functions.channels import GetFullChannelRequest, GetForumTopicsRequest
+from telethon.tl.types import Channel, ForumTopic, Message, User
 
 from src.config import ITER_MESSAGES_TIMEOUT, Config, ExportTarget
 from src.core.connection import PoolType
 from src.exceptions import TelegramConnectionError
+from src.input_peer_cache import InputPeerCache
 from src.logging_context import update_context_prefix
 from src.utils import clear_screen, logger, notify_and_pause, sanitize_filename
 
@@ -90,42 +91,73 @@ class TelegramManager:
         self.connection_manager = connection_manager
         self.cache_manager = cache_manager
         proxy_info = None
-        if config.proxy_type and config.proxy_addr and config.proxy_port:
-            proxy_scheme = config.proxy_type.lower()
+        # Be defensive: tests or simple DummyConfig objects may omit proxy attributes
+        if getattr(config, "proxy_type", None) and getattr(config, "proxy_addr", None) and getattr(config, "proxy_port", None):
+            proxy_scheme = getattr(config, "proxy_type").lower()
             if proxy_scheme not in ["socks4", "socks5", "http"]:
                 logger.warning(
                     f"Unsupported proxy type for Telethon: '{proxy_scheme}'. Ignoring."
                 )
             else:
-                proxy_info = (proxy_scheme, config.proxy_addr, config.proxy_port)
+                proxy_info = (
+                    proxy_scheme,
+                    getattr(config, "proxy_addr"),
+                    getattr(config, "proxy_port"),
+                )
 
         base_timeout = getattr(config.performance, "base_download_timeout", 300.0)
 
+        # Security S-5: Socket/connection timeouts
+        # Telethon uses MTProto with built-in socket timeouts (not aiohttp).
+        # Configuration:
+        #   - timeout=300s: total request timeout (prevents indefinite hangs)
+        #   - connection_retries=20: max connection attempts before failure
+        #   - retry_delay=1s: delay between retry attempts
+        #   - auto_reconnect=True: automatic reconnection on connection loss
+        # This prevents DoS via socket hanging (total max hang: ~20s retries + 300s timeout)
+
         # Legacy automatic conversion support removed; use Telethon .session file for authentication.
         # If you need to migrate a legacy session, convert the file externally and place the resulting .session file in the app directory.
 
-        self.client = TelegramClient(
-            config.session_name,
-            config.api_id,
-            config.api_hash,
-            device_model="Telegram Desktop",
-            app_version="4.14.8",
-            system_version="Windows 10",
-            lang_code="en",
-            system_lang_code="en",
-            connection_retries=20,
-            retry_delay=1,
-            request_retries=25,
-            timeout=base_timeout,
-            flood_sleep_threshold=0,
-            auto_reconnect=True,
-            sequential_updates=True,
-            proxy=proxy_info,
-        )
+        api_id = getattr(config, "api_id", None)
+        api_hash = getattr(config, "api_hash", None)
+
+        if api_id and api_hash:
+            self.client = TelegramClient(
+                getattr(config, "session_name", "tobs_session"),
+                api_id,
+                api_hash,
+                device_model="Telegram Desktop",
+                app_version="4.14.8",
+                system_version="Windows 10",
+                lang_code="en",
+                system_lang_code="en",
+                connection_retries=20,
+                retry_delay=1,
+                request_retries=25,
+                timeout=base_timeout,
+                flood_sleep_threshold=0,
+                auto_reconnect=True,
+                sequential_updates=True,
+                proxy=proxy_info,
+            )
+        else:
+            logger.info("API credentials not provided; TelegramClient not instantiated (testing/offline mode)")
+            self.client = None
+        self._original_client = self.client  # Store original client for operations that don't support Takeout
         self.entity_cache: Dict[str, Any] = {}
         self.topics_cache: Dict[Union[str, int], List[TopicInfo]] = {}
         self.client_connected = False
         self._external_takeout_id: Optional[int] = None
+        
+        # C-3: InputPeer cache for reducing entity resolution API calls
+        cache_size = getattr(config.performance, "input_peer_cache_size", 1000)
+        cache_ttl = getattr(config.performance, "input_peer_cache_ttl", 3600.0)
+        self._input_peer_cache = InputPeerCache(
+            max_size=cache_size,
+            ttl_seconds=cache_ttl
+        )
+        logger.info(f"InputPeerCache enabled: size={cache_size}, ttl={cache_ttl}s")
 
     async def connect(self) -> bool:
         """
@@ -304,6 +336,53 @@ class TelegramManager:
             if entity_id_str.lstrip("-").isdigit():
                 return await self.client.get_entity(int(entity_id_str))
             raise
+    
+    async def get_input_entity_cached(self, entity: Any) -> Any:
+        """
+        Get InputPeer for entity with caching support (C-3 optimization).
+        
+        Reduces redundant get_input_entity API calls by caching InputPeer objects.
+        Uses LRU cache with TTL for automatic expiration.
+        
+        Args:
+            entity: Telegram entity (can be entity object, ID, or username)
+        
+        Returns:
+            InputPeer object (InputPeerUser, InputPeerChannel, or InputPeerChat)
+        """
+        # Extract entity ID for cache key
+        entity_id = None
+        if isinstance(entity, (int, str)):
+            # For raw IDs or usernames, resolve first
+            resolved = await self.resolve_entity(entity)
+            if resolved:
+                entity_id = getattr(resolved, "id", None)
+                entity = resolved
+        else:
+            entity_id = getattr(entity, "id", None)
+        
+        if entity_id is None:
+            # Fallback to direct API call if ID cannot be extracted
+            return await self.client.get_input_entity(entity)
+        
+        # Check cache first
+        cached_peer = self._input_peer_cache.get(entity_id)
+        if cached_peer is not None:
+            logger.debug(f"InputPeerCache HIT for entity_id={entity_id}")
+            return cached_peer
+        
+        # Cache miss - fetch from API
+        logger.debug(f"InputPeerCache MISS for entity_id={entity_id}")
+        input_peer = await self.client.get_input_entity(entity)
+        
+        # Store in cache
+        self._input_peer_cache.set(entity_id, input_peer)
+        
+        return input_peer
+    
+    def get_input_peer_cache_metrics(self) -> dict:
+        """Get InputPeer cache performance metrics."""
+        return self._input_peer_cache.get_metrics()
 
     async def fetch_messages(
         self,
@@ -416,17 +495,26 @@ class TelegramManager:
             if not batch_messages:
                 break  # No more messages
 
-            # Process batch (yield in chronological order)
+            # Process batch (yield in chronological order when reverse=True)
+            if batch_messages:
+                first_id = batch_messages[0].id
+                last_id = batch_messages[-1].id
+                logger.info(f"📦 Batch: {len(batch_messages)} messages, IDs {first_id} → {last_id} (reverse=True)")
+            
             for message in batch_messages:
-                if isinstance(message, Message) and not message.action:
-                    yield message
-                    total_fetched += 1
+                # Accept Telethon Message instances or any message-like object with an id.
+                # Skip service messages that have an 'action' attribute set.
+                if getattr(message, "action", None):
+                    continue
+                yield message
+                total_fetched += 1
 
-                    if effective_limit is not None and total_fetched >= effective_limit:
-                        break
+                if effective_limit is not None and total_fetched >= effective_limit:
+                    break
 
-            # Update offset for next batch (last message is the oldest in this batch)
-            # If we yielded all messages, update offset to the last one
+            # Update offset for next batch
+            # With reverse=True: batch goes from old→new, so last message is newest
+            # Next batch starts AFTER this ID to continue forward in time
             if batch_messages:
                 current_offset_id = batch_messages[-1].id
 
@@ -877,8 +965,27 @@ class TelegramManager:
             # Use configured limit or default to 100
             fetch_limit = limit or 100
 
+            # GetForumTopicsRequest doesn't work with Takeout, use original client
+            # If self.client is a TakeoutSessionWrapper, extract the underlying client
+            client_to_use = self.client
+            if hasattr(self.client, 'client'):
+                # This is TakeoutSessionWrapper, get the underlying client
+                client_to_use = self.client.client
+                logger.info(f"🔍 Detected TakeoutSessionWrapper, using underlying client for GetForumTopicsRequest")
+            elif hasattr(self, '_original_client') and self._original_client:
+                # Fallback to _original_client if set
+                client_to_use = self._original_client
+                logger.info(f"🔍 Using _original_client for GetForumTopicsRequest")
+            else:
+                logger.info(f"🔍 Using self.client directly for GetForumTopicsRequest")
+            
+            # Check if client is connected (is_connected is a property, not a method)
+            if hasattr(client_to_use, 'is_connected'):
+                is_connected = client_to_use.is_connected()
+                logger.info(f"🔍 Client connection status: {is_connected}")
+            
             async def _fetch_topics():
-                return await self.client(
+                return await client_to_use(
                     GetForumTopicsRequest(
                         channel=entity,
                         offset_date=None,
@@ -937,13 +1044,23 @@ class TelegramManager:
         topics: List[TopicInfo] = []
         current_offset = offset_topic
 
+        # GetForumTopicsRequest doesn't work with Takeout, use original client
+        # If self.client is a TakeoutSessionWrapper, extract the underlying client
+        client_to_use = self.client
+        if hasattr(self.client, 'client'):
+            # This is TakeoutSessionWrapper, get the underlying client
+            client_to_use = self.client.client
+        elif hasattr(self, '_original_client') and self._original_client:
+            # Fallback to _original_client if set
+            client_to_use = self._original_client
+
         try:
             while len(topics) < page_size:
                 remaining = page_size - len(topics)
                 fetch_limit = min(remaining, 100)  # Telegram API limit
 
                 async def _fetch_page():
-                    return await self.client(
+                    return await client_to_use(
                         GetForumTopicsRequest(
                             channel=entity,
                             offset_date=None,
@@ -1009,35 +1126,22 @@ class TelegramManager:
         if self.cache_manager:
             cached_count = await self.cache_manager.get(cache_key)
             if cached_count is not None:
-                logger.debug(f"Cache hit for topic message count: {cache_key}")
+                logger.info(f"🔍 Cache hit for topic {topic_id}: returning {cached_count} (cached value)")
                 return cached_count
 
         count = 0
         try:
-            # Prefer GetFullChannelRequest for channels for more stable total
-            if isinstance(entity, Channel):
-                # Fetch full channel info, which often contains message count
-                full_channel = await self.client(GetFullChannelRequest(entity))
-                # read_inbox_max_id gives the latest message ID, which is often total - 1
-                # Subtract 1 because topic ID itself is not a message
-                count = getattr(full_channel.full_chat, "read_inbox_max_id", 0) - 1
-                if count < 0:
-                    count = 0  # Ensure non-negative
-                logger.debug(
-                    f"Fetched topic message count via GetFullChannelRequest: {count}"
-                )
-
-            if count == 0:  # Fallback if not a channel or count still 0
-                result = await self.client.get_messages(
-                    entity, reply_to=topic_id, limit=0
-                )
-                # get_messages(limit=0) returns an object with 'total' attribute
-                count = getattr(result, "total", 0) - 1  # Subtract 1 for topic ID
-                if count < 0:
-                    count = 0  # Ensure non-negative
-                logger.debug(
-                    f"Fetched topic message count via get_messages(limit=0): {count}"
-                )
+            # For forum topics, we MUST use get_messages with reply_to parameter
+            # GetFullChannelRequest returns total channel messages, not per-topic
+            logger.info(f"🔍 Fetching message count for topic {topic_id} via API...")
+            result = await self.client.get_messages(
+                entity, reply_to=topic_id, limit=0
+            )
+            # get_messages(limit=0) returns an object with 'total' attribute
+            count = getattr(result, "total", 0)
+            logger.info(
+                f"✅ Topic {topic_id} has {count} messages (fresh from API)"
+            )
 
         except Exception as e:
             logger.warning(
diff --git i/src/telegram_sharded_client.py w/src/telegram_sharded_client.py
index 0518496..59bc610 100644
--- i/src/telegram_sharded_client.py
+++ w/src/telegram_sharded_client.py
@@ -1,9 +1,10 @@
 import asyncio
 import os
-import pickle
+import msgpack  # S-3: Security fix - replaced pickle with msgpack
 import shutil
 import struct
 import time
+import zlib
 from pathlib import Path
 from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
 
@@ -50,8 +51,10 @@ class ShardedTelegramManager(TelegramManager):
     Extended TelegramManager that supports sharded parallel fetching using Takeout.
     """
 
-    def __init__(self, config: Config, connection_manager: Any = None):
-        super().__init__(config, connection_manager)
+    def __init__(
+        self, config: Config, connection_manager: Any = None, cache_manager: Any = None
+    ):
+        super().__init__(config, connection_manager, cache_manager)
         self.worker_sessions: List[str] = []
         self.worker_clients: List[TelegramClient] = []
         self.takeout_id: Optional[int] = None
@@ -148,7 +151,7 @@ class ShardedTelegramManager(TelegramManager):
             worker_sessions.append(worker_sess_name)
 
         return worker_sessions  # type: ignore
-    
+
     def _extract_dc_id(self, entity_or_peer: Any) -> int:
         """
         Extract DC (datacenter) ID from entity or peer.
@@ -156,51 +159,111 @@ class ShardedTelegramManager(TelegramManager):
         """
         try:
             # Try to get DC from photo
-            if hasattr(entity_or_peer, 'photo') and entity_or_peer.photo:
-                if hasattr(entity_or_peer.photo, 'dc_id'):
+            if hasattr(entity_or_peer, "photo") and entity_or_peer.photo:
+                if hasattr(entity_or_peer.photo, "dc_id"):
                     return int(entity_or_peer.photo.dc_id)
-            
+
             # Try to get from access_hash (channel/user)
-            if hasattr(entity_or_peer, 'access_hash'):
+            if hasattr(entity_or_peer, "access_hash"):
                 # DC ID is typically encoded in access_hash for channels
                 # This is a heuristic, not guaranteed
                 pass
-            
+
             # For InputPeer types
-            if isinstance(entity_or_peer, (types.InputPeerChannel, types.InputPeerUser)):  # type: ignore
+            if isinstance(
+                entity_or_peer, (types.InputPeerChannel, types.InputPeerUser)
+            ):  # type: ignore
                 # Unfortunately, InputPeer doesn't carry DC info directly
                 # We'd need to store it from the original entity
                 pass
-                
+
             return 0  # Unknown DC
         except Exception as e:
             logger.debug(f"Could not extract DC ID: {e}")
             return 0
-    
+
+    def _to_lightweight_message_dict(self, message: types.Message) -> Dict[str, Any]:
+        """Converts a Telethon Message object to a lightweight dictionary."""
+        # This is a minimal set of attributes required for basic display and media handling.
+        # Add more if needed by other parts of the export process.
+        lightweight_dict = {
+            "id": message.id,
+            "peer_id": getattr(message, "peer_id", None),
+            "date": message.date,
+            "message": message.message,
+            "out": message.out,
+            "mentioned": message.mentioned,
+            "media_unread": message.media_unread,
+            "silent": message.silent,
+            "post": message.post,
+            "from_scheduled": message.from_scheduled,
+            "legacy": message.legacy,
+            "edit_hide": message.edit_hide,
+            "pinned": message.pinned,
+            "noforwards": message.noforwards,
+            "reactions": getattr(message, "reactions", None),
+            "replies": getattr(message, "replies", None),
+            "forwards": getattr(message, "forwards", None),
+            "via_bot_id": message.via_bot_id,
+            "reply_to": getattr(message, "reply_to", None),
+            "fwd_from": getattr(message, "fwd_from", None),
+            "via_bot": getattr(message, "via_bot", None),
+            "entities": getattr(message, "entities", None),
+            "media": getattr(message, "media", None),  # Keep media object itself
+            "file": getattr(message, "file", None),  # Keep file object if it exists
+            "photo": getattr(message, "photo", None),  # Keep photo object if it exists
+            "document": getattr(
+                message, "document", None
+            ),  # Keep document object if it exists
+        }
+
+        # Handle sender information
+        if message.sender_id:
+            lightweight_dict["sender_id"] = message.sender_id
+        if message.sender:
+            lightweight_dict["sender_id"] = (
+                message.sender.id
+            )  # Ensure sender_id is there
+            lightweight_dict["sender_username"] = getattr(
+                message.sender, "username", None
+            )
+            lightweight_dict["sender_first_name"] = getattr(
+                message.sender, "first_name", None
+            )
+            lightweight_dict["sender_last_name"] = getattr(
+                message.sender, "last_name", None
+            )
+
+        return lightweight_dict
+
     async def _log_slow_chunks_statistics(self):
         """
         Aggregate and log statistics about slow chunks across all workers.
         Provides insights into problematic ID ranges and their impact.
         """
         all_slow_chunks = []
-        
+
         # Collect slow chunks from all worker stats
         for i, stats in enumerate(self.worker_stats.values()):
             if "slow_chunks" in stats:
                 for chunk in stats["slow_chunks"]:
                     chunk["worker_id"] = i
                     all_slow_chunks.append(chunk)
-        
+
         if not all_slow_chunks:
             logger.info("✅ No slow chunks detected (all chunks completed in <2s)")
             return
-        
+
         # Calculate statistics
         total_slow_chunks = len(all_slow_chunks)
-        split_attempts = sum(1 for c in all_slow_chunks if c.get("action") == "split_attempted")
-        avg_slow_duration = sum(c["duration_sec"] for c in all_slow_chunks) / total_slow_chunks
+        split_attempts = sum(
+            1 for c in all_slow_chunks if c.get("action") == "split_attempted"
+        )
+        avg_slow_duration = (
+            sum(c["duration_sec"] for c in all_slow_chunks) / total_slow_chunks
+        )
         max_slow_chunk = max(all_slow_chunks, key=lambda c: c["duration_sec"])
-        
+
         # DC-aware statistics
         dc_stats: Dict[int, Dict[str, Any]] = {}
         for chunk in all_slow_chunks:
@@ -210,22 +273,28 @@ class ShardedTelegramManager(TelegramManager):
             dc_stats[dc]["count"] += 1
             dc_stats[dc]["total_duration"] += chunk["duration_sec"]
             dc_stats[dc]["chunks"].append(chunk)
-        
+
         # Find most problematic ID ranges (sort by duration)
-        top_slow_chunks = sorted(all_slow_chunks, key=lambda c: c["duration_sec"], reverse=True)[:5]
-        
+        top_slow_chunks = sorted(
+            all_slow_chunks, key=lambda c: c["duration_sec"], reverse=True
+        )[:5]
+
         logger.warning(
             f"🐢 Slow Chunks Summary: {total_slow_chunks} chunks >2s detected, "
             f"{split_attempts} split attempts, avg {avg_slow_duration:.1f}s"
         )
-        
-        max_dc_str = f"DC{max_slow_chunk.get('dc_id', 0)}" if max_slow_chunk.get('dc_id', 0) > 0 else "DC?"
+
+        max_dc_str = (
+            f"DC{max_slow_chunk.get('dc_id', 0)}"
+            if max_slow_chunk.get("dc_id", 0) > 0
+            else "DC?"
+        )
         logger.warning(
             f"   Slowest chunk: {max_slow_chunk['start_id']}-{max_slow_chunk['end_id']} "
             f"took {max_slow_chunk['duration_sec']:.1f}s "
             f"(worker {max_slow_chunk['worker_id']}, {max_slow_chunk['messages']} msgs, {max_dc_str})"
         )
-        
+
         # Log DC-specific statistics
         if dc_stats:
             logger.info("📍 Slow chunks by Datacenter:")
@@ -238,31 +307,36 @@ class ShardedTelegramManager(TelegramManager):
                     f"avg {avg_dc_duration:.1f}s, "
                     f"total {dc_data['total_duration']:.1f}s"
                 )
-        
+
         if len(top_slow_chunks) > 1:
             logger.info("📊 Top 5 slowest ID ranges:")
             for idx, chunk in enumerate(top_slow_chunks, 1):
-                chunk_dc_str = f"DC{chunk.get('dc_id', 0)}" if chunk.get('dc_id', 0) > 0 else "DC?"
+                chunk_dc_str = (
+                    f"DC{chunk.get('dc_id', 0)}" if chunk.get("dc_id", 0) > 0 else "DC?"
+                )
                 logger.info(
                     f"   {idx}. {chunk['start_id']:,}-{chunk['end_id']:,}: "
                     f"{chunk['duration_sec']:.1f}s ({chunk['messages']} msgs, "
                     f"{chunk_dc_str}, worker {chunk['worker_id']}, {chunk['action']})"
                 )
-        
+
         # NEW: Update hot zones database from patterns
         if self.config.enable_hot_zones and all_slow_chunks:
             try:
                 from src.hot_zones_manager import HotZonesManager
+
                 hot_zones_mgr = HotZonesManager(self.config)
-                
+
                 # Analyze each slow chunk and update hot zones
                 for chunk in all_slow_chunks:
                     hot_zones_mgr.analyze_and_update_hot_zones(chunk)
-                
+
                 # Save updated database
                 hot_zones_mgr.save_database()
-                logger.info(f"💾 Updated hot zones database: {hot_zones_mgr.slow_chunk_db_path}")
-                
+                logger.info(
+                    f"💾 Updated hot zones database: {hot_zones_mgr.slow_chunk_db_path}"
+                )
+
                 # Print actionable recommendations
                 recommendations = hot_zones_mgr.get_recommendations()
                 if recommendations:
@@ -329,7 +403,7 @@ class ShardedTelegramManager(TelegramManager):
         """
         Worker loop: fetches messages and writes them to a temporary file.
         Collects telemetry: latency, IO time, request count.
-        
+
         NEW: Supports dynamic work stealing via task_queue.
         If task_queue is provided, worker pulls tasks dynamically instead of using fixed id_ranges.
         """
@@ -356,48 +430,66 @@ class ShardedTelegramManager(TelegramManager):
             with open(output_path, "wb") as f:
                 # NEW: Dynamic work stealing mode
                 if task_queue is not None:
-                    logger.debug(f"👷 Worker {worker_idx} starting in DYNAMIC mode (work stealing)")
-                    
+                    logger.debug(
+                        f"👷 Worker {worker_idx} starting in DYNAMIC mode (work stealing)"
+                    )
+
                     while True:
                         try:
                             # Non-blocking get: if queue is empty, worker is done
                             chunk = task_queue.get_nowait()
                             start_id, end_id = chunk
                             stats["chunks_processed"] += 1
-                            
+
                             logger.debug(
                                 f"👷 Worker {worker_idx} grabbed chunk #{stats['chunks_processed']}: {start_id}-{end_id}"
                             )
-                            
+
                             # Process this chunk with config parameters
                             await self._fetch_chunk(
-                                worker_idx, client, input_peer, start_id, end_id, 
-                                f, stats, takeout_id,
+                                worker_idx,
+                                client,
+                                input_peer,
+                                start_id,
+                                end_id,
+                                f,
+                                stats,
+                                takeout_id,
                                 slow_chunk_threshold=self.config.slow_chunk_threshold,
-                                max_retries=self.config.slow_chunk_max_retries
+                                max_retries=self.config.slow_chunk_max_retries,
                             )
-                            
+
                             task_queue.task_done()
-                            
+
                         except asyncio.QueueEmpty:
                             # No more tasks, worker finishes
-                            logger.debug(f"✅ Worker {worker_idx} finished (queue empty, processed {stats['chunks_processed']} chunks)")
+                            logger.debug(
+                                f"✅ Worker {worker_idx} finished (queue empty, processed {stats['chunks_processed']} chunks)"
+                            )
                             break
-                
+
                 # OLD: Static range assignment mode (fallback)
                 else:
-                    logger.debug(f"👷 Worker {worker_idx} starting in STATIC mode (fixed ranges)")
-                    
+                    logger.debug(
+                        f"👷 Worker {worker_idx} starting in STATIC mode (fixed ranges)"
+                    )
+
                     for start_id, end_id in id_ranges:
                         logger.debug(
                             f"👷 Worker {worker_idx} processing range {start_id}-{end_id}"
                         )
-                        
+
                         await self._fetch_chunk(
-                            worker_idx, client, input_peer, start_id, end_id, 
-                            f, stats, takeout_id,
+                            worker_idx,
+                            client,
+                            input_peer,
+                            start_id,
+                            end_id,
+                            f,
+                            stats,
+                            takeout_id,
                             slow_chunk_threshold=self.config.slow_chunk_threshold,
-                            max_retries=self.config.slow_chunk_max_retries
+                            max_retries=self.config.slow_chunk_max_retries,
                         )
 
         except Exception as e:
@@ -430,12 +522,12 @@ class ShardedTelegramManager(TelegramManager):
     ):
         """
         Fetch a single chunk of messages (start_id to end_id) and write to file.
-        
+
         Features:
         - Adaptive splitting: automatically divides slow chunks into smaller sub-chunks
         - Retry with exponential backoff for failed chunks
         - Detailed timing and warning logs for slow operations
-        
+
         Args:
             slow_chunk_threshold: Time in seconds after which chunk is considered slow (default: 10s)
             max_retries: Maximum retry attempts with adaptive splitting (default: 2)
@@ -443,14 +535,14 @@ class ShardedTelegramManager(TelegramManager):
         chunk_start_time = time.time()
         chunk_messages = 0
         chunk_span = end_id - start_id
-        
+
         # Track slow chunks for statistics
         if "slow_chunks" not in stats:
             stats["slow_chunks"] = []
-        
+
         # Buffer to collect messages BEFORE writing (so we can split if needed)
         message_buffer = []
-        
+
         current_offset_id = end_id + 1
 
         while current_offset_id > start_id:
@@ -467,9 +559,7 @@ class ShardedTelegramManager(TelegramManager):
                 hash=0,
             )
 
-            wrapped_req = InvokeWithTakeoutRequest(
-                takeout_id=takeout_id, query=req
-            )
+            wrapped_req = InvokeWithTakeoutRequest(takeout_id=takeout_id, query=req)
 
             # Measure API request latency
             request_start = time.time()
@@ -494,7 +584,7 @@ class ShardedTelegramManager(TelegramManager):
 
             # Collect messages in buffer instead of writing immediately
             message_buffer.extend(res.messages)
-            
+
             fetched_count = len(res.messages)
             chunk_messages += fetched_count
 
@@ -504,13 +594,19 @@ class ShardedTelegramManager(TelegramManager):
 
             if fetched_count < limit:
                 break
-        
+
         # 🔍 Check chunk performance BEFORE writing
         chunk_duration = time.time() - chunk_start_time
-        
+
         # Case 1: Extremely slow chunk (>slow_chunk_threshold) - SPLIT instead of writing
-        if chunk_duration > slow_chunk_threshold and chunk_span > 1000 and max_retries > 0:
-            dc_str = f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
+        if (
+            chunk_duration > slow_chunk_threshold
+            and chunk_span > 1000
+            and max_retries > 0
+        ):
+            dc_str = (
+                f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
+            )
             logger.warning(
                 f"🐢 Worker {worker_idx} VERY SLOW chunk: {start_id}-{end_id} "
                 f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
@@ -518,21 +614,23 @@ class ShardedTelegramManager(TelegramManager):
             logger.info(
                 f"🔄 Worker {worker_idx} DISCARDING buffer and re-fetching as 4 sub-chunks (adaptive split)..."
             )
-            
+
             # Record this slow chunk for statistics
-            stats["slow_chunks"].append({
-                "start_id": start_id,
-                "end_id": end_id,
-                "duration_sec": chunk_duration,
-                "messages": chunk_messages,
-                "action": "split_attempted",
-                "dc_id": self.current_entity_dc,  # Add DC ID
-            })
-            
+            stats["slow_chunks"].append(
+                {
+                    "start_id": start_id,
+                    "end_id": end_id,
+                    "duration_sec": chunk_duration,
+                    "messages": chunk_messages,
+                    "action": "split_attempted",
+                    "dc_id": self.current_entity_dc,  # Add DC ID
+                }
+            )
+
             # IMPORTANT: Don't write the buffer! Discard it and re-fetch as sub-chunks
             # This prevents duplicate messages
             message_buffer = []
-            
+
             # Split into 4 smaller sub-chunks
             sub_chunk_size = chunk_span // 4
             sub_chunks = []
@@ -540,45 +638,81 @@ class ShardedTelegramManager(TelegramManager):
                 sub_start = start_id + (i * sub_chunk_size)
                 sub_end = start_id + ((i + 1) * sub_chunk_size) if i < 3 else end_id
                 sub_chunks.append((sub_start, sub_end))
-            
+
             # Recursively fetch sub-chunks with reduced retry count
             for sub_start, sub_end in sub_chunks:
                 logger.debug(
                     f"  🔹 Worker {worker_idx} fetching sub-chunk {sub_start}-{sub_end}"
                 )
                 await self._fetch_chunk(
-                    worker_idx, client, input_peer, sub_start, sub_end,
-                    f, stats, takeout_id,
+                    worker_idx,
+                    client,
+                    input_peer,
+                    sub_start,
+                    sub_end,
+                    f,
+                    stats,
+                    takeout_id,
                     slow_chunk_threshold=slow_chunk_threshold,
-                    max_retries=max_retries - 1  # Reduce retry count to prevent infinite recursion
+                    max_retries=max_retries
+                    - 1,  # Reduce retry count to prevent infinite recursion
                 )
-            
+
             # Early return - sub-chunks handle their own stats
             return
-        
+
         # Case 2 & 3: Write messages to file (either moderately slow or fast)
         if message_buffer:
             io_start = time.time()
-            
-            # Use length-prefixed framing for safe reading
-            data = pickle.dumps(message_buffer)
+
+            # Serialization & Compression
+            serialized_data = message_buffer
+            if getattr(self.config, "shard_lightweight_schema_enabled", False):
+                logger.debug(
+                    f"Worker {worker_idx}: Using lightweight schema for serialization"
+                )
+                serialized_data = [
+                    self._to_lightweight_message_dict(msg) for msg in message_buffer
+                ]
+
+            # S-3: Security fix - use msgpack instead of pickle
+            data = msgpack.packb(serialized_data, use_bin_type=True)
+            is_compressed = 0
+
+            if getattr(self.config, "shard_compression_enabled", True):
+                try:
+                    level = getattr(self.config, "shard_compression_level", 1)
+                    compressed = zlib.compress(data, level=level)
+                    # Only use compression if it actually saves space
+                    if len(compressed) < len(data):
+                        data = compressed
+                        is_compressed = 1
+                except Exception as e:
+                    logger.warning(f"Compression failed, using raw data: {e}")
+
+            # Format: [Length: 4 bytes] [Flag: 1 byte] [Data]
             f.write(struct.pack(">I", len(data)))
+            f.write(struct.pack(">B", is_compressed))
             f.write(data)
             f.flush()  # Ensure data is written to disk
-            
+
             # Record IO time and message count
             io_time_ms = (time.time() - io_start) * 1000
             stats["io_time_ms"] += int(io_time_ms)
-            stats["messages"] += chunk_messages  # Update stats ONLY when actually written
-        
+            stats["messages"] += (
+                chunk_messages  # Update stats ONLY when actually written
+            )
+
         # Log performance
         if chunk_duration > 2.0:
-            dc_str = f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
+            dc_str = (
+                f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
+            )
             logger.warning(
                 f"⚠️ Worker {worker_idx} slow chunk: {start_id}-{end_id} "
                 f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
             )
-            
+
             slow_chunk_info = {
                 "start_id": start_id,
                 "end_id": end_id,
@@ -588,15 +722,18 @@ class ShardedTelegramManager(TelegramManager):
                 "dc_id": self.current_entity_dc,  # Add DC ID
             }
             stats["slow_chunks"].append(slow_chunk_info)
-            
+
             # NEW: Record to persistent database if significantly slow
             if chunk_duration > self.config.slow_chunk_threshold:
                 try:
                     from src.hot_zones_manager import HotZonesManager, SlowChunkRecord
+
                     hot_zones_mgr = HotZonesManager(self.config)
-                    
-                    density = (chunk_messages / chunk_span * 1000) if chunk_span > 0 else 0
-                    
+
+                    density = (
+                        (chunk_messages / chunk_span * 1000) if chunk_span > 0 else 0
+                    )
+
                     slow_record = SlowChunkRecord(
                         id_range=(start_id, end_id),
                         duration_sec=chunk_duration,
@@ -606,10 +743,12 @@ class ShardedTelegramManager(TelegramManager):
                         timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         worker_id=worker_idx,
                     )
-                    
+
                     hot_zones_mgr.record_slow_chunk(slow_record)
                     hot_zones_mgr.save_database()
-                    logger.debug(f"💾 Recorded slow chunk to database: {start_id}-{end_id}")
+                    logger.debug(
+                        f"💾 Recorded slow chunk to database: {start_id}-{end_id}"
+                    )
                 except Exception as e:
                     logger.debug(f"⚠️ Failed to record slow chunk to database: {e}")
         else:
@@ -632,8 +771,10 @@ class ShardedTelegramManager(TelegramManager):
         """
 
         # 🔍 CRITICAL DEBUG: Log entry point FIRST
-        logger.info(f"🔍 ShardedTelegramManager.fetch_messages() CALLED - entity={entity}, limit={limit}, page={page}, min_id={min_id}")
-        
+        logger.info(
+            f"🔍 ShardedTelegramManager.fetch_messages() CALLED - entity={entity}, limit={limit}, page={page}, min_id={min_id}"
+        )
+
         logger.debug(
             f"fetch_messages called: limit={limit}, page={page}, min_id={min_id}"
         )
@@ -664,15 +805,17 @@ class ShardedTelegramManager(TelegramManager):
 
         # --- Sharding Implementation ---
         logger.info("⚡ Sharding activated! Starting parallel fetch...")
-        
+
         shard_start_time = time.time()
 
         try:
             # 1. Resolve Entity & Bounds
             step_start = time.time()
             resolved_entity = await self.client.get_entity(entity)
-            logger.info(f"⏱️  Entity resolved in {(time.time() - step_start)*1000:.0f}ms")
-            
+            logger.info(
+                f"⏱️  Entity resolved in {(time.time() - step_start) * 1000:.0f}ms"
+            )
+
             # Extract DC ID for diagnostics
             dc_id = self._extract_dc_id(resolved_entity)
             self.current_entity_dc = dc_id  # Store for worker access
@@ -680,7 +823,7 @@ class ShardedTelegramManager(TelegramManager):
                 logger.info(f"📍 Entity DC: DC{dc_id}")
             else:
                 logger.debug("📍 Entity DC: Unknown (will log per-chunk if available)")
-            
+
             input_peer = utils.get_input_peer(resolved_entity)  # type: ignore
 
             # Get bounds (max_id)
@@ -690,7 +833,9 @@ class ShardedTelegramManager(TelegramManager):
                 logger.info("⏱️  Empty chat, returning")
                 return
             max_id = messages[0].id
-            logger.info(f"⏱️  Max ID ({max_id}) fetched in {(time.time() - step_start)*1000:.0f}ms")
+            logger.info(
+                f"⏱️  Max ID ({max_id}) fetched in {(time.time() - step_start) * 1000:.0f}ms"
+            )
 
             # Get ACTUAL minimum message ID (not just 1)
             # Fetch oldest message to determine real range
@@ -706,10 +851,14 @@ class ShardedTelegramManager(TelegramManager):
                 fetch_time_ms = (time.time() - step_start) * 1000
                 if oldest_messages:
                     effective_min = oldest_messages[0].id
-                    logger.info(f"⏱️  Oldest message ID ({effective_min}) fetched in {fetch_time_ms:.0f}ms")
+                    logger.info(
+                        f"⏱️  Oldest message ID ({effective_min}) fetched in {fetch_time_ms:.0f}ms"
+                    )
                 else:
                     effective_min = 1  # Fallback if no messages
-                    logger.warning(f"⚠️  No oldest message found, using fallback min_id=1 (took {fetch_time_ms:.0f}ms)")
+                    logger.warning(
+                        f"⚠️  No oldest message found, using fallback min_id=1 (took {fetch_time_ms:.0f}ms)"
+                    )
             else:
                 effective_min = min_id
                 logger.info(f"📌 Using provided min_id: {effective_min}")
@@ -738,11 +887,15 @@ class ShardedTelegramManager(TelegramManager):
             step_start = time.time()
             self.worker_stats = {}  # Reset stats for this run
             self.takeout_id = await self._setup_takeout()
-            logger.info(f"⏱️  Takeout setup in {(time.time() - step_start)*1000:.0f}ms")
-            
+            logger.info(
+                f"⏱️  Takeout setup in {(time.time() - step_start) * 1000:.0f}ms"
+            )
+
             step_start = time.time()
             self.worker_sessions = await self._prepare_workers()
-            logger.info(f"⏱️  Worker sessions prepared in {(time.time() - step_start)*1000:.0f}ms")
+            logger.info(
+                f"⏱️  Worker sessions prepared in {(time.time() - step_start) * 1000:.0f}ms"
+            )
 
             # Initialize worker clients
             step_start = time.time()
@@ -761,59 +914,86 @@ class ShardedTelegramManager(TelegramManager):
                 )
                 await client.connect()
                 self.worker_clients.append(client)
-                logger.debug(f"⏱️  Worker {i} connected in {(time.time() - client_start)*1000:.0f}ms")
-            logger.info(f"⏱️  All {len(self.worker_clients)} worker clients connected in {(time.time() - step_start)*1000:.0f}ms")
+                logger.debug(
+                    f"⏱️  Worker {i} connected in {(time.time() - client_start) * 1000:.0f}ms"
+                )
+            logger.info(
+                f"⏱️  All {len(self.worker_clients)} worker clients connected in {(time.time() - step_start) * 1000:.0f}ms"
+            )
+
+            # Optionally pre-warm workers to the entity DC for faster per-DC routing
+            if (
+                getattr(self.config, "dc_aware_routing_enabled", False)
+                and self.current_entity_dc > 0
+                and getattr(self.config, "dc_prewarm_enabled", True)
+            ):
+                try:
+                    from src.telegram_dc_utils import prewarm_workers  # lazy import
+
+                    prewarm_start = time.time()
+                    prewarm_results = await prewarm_workers(
+                        self.worker_clients,
+                        resolved_entity,
+                        timeout=self.config.dc_prewarm_timeout,
+                        dc_id=self.current_entity_dc,
+                    )
+                    prewarm_time = time.time() - prewarm_start
+                    prewarmed_count = sum(1 for ok in prewarm_results.values() if ok)
+                    logger.info(
+                        f"♻️ DC pre-warm: {prewarmed_count}/{len(self.worker_clients)} workers warmed to DC{self.current_entity_dc} in {prewarm_time:.2f}s"
+                    )
+                except Exception as e:
+                    logger.warning(f"DC pre-warm failed or not available: {e}")
 
             # 3. Calculate Chunks for Dynamic Work Stealing with Hot Zones & Density Awareness
             # NEW: Use HotZonesManager for adaptive chunking
             from src.hot_zones_manager import HotZonesManager
-            
+
             step_start = time.time()
             hot_zones_mgr = HotZonesManager(self.config)
-            logger.info(f"⏱️  Hot zones manager initialized in {(time.time() - step_start)*1000:.0f}ms")
-            
+            logger.info(
+                f"⏱️  Hot zones manager initialized in {(time.time() - step_start) * 1000:.0f}ms"
+            )
+
             # NEW: Estimate message density for adaptive chunking
             estimated_density = 50.0  # Default
             if self.config.enable_density_estimation:
                 step_start = time.time()
                 logger.info("🔍 Estimating message density for adaptive chunking...")
                 estimated_density = await hot_zones_mgr.estimate_density(
-                    self.client,
-                    resolved_entity,
-                    effective_min,
-                    max_id
+                    self.client, resolved_entity, effective_min, max_id
                 )
                 logger.info(
                     f"📊 Estimated density: {estimated_density:.1f} msgs/1K IDs "
-                    f"(took {(time.time() - step_start)*1000:.0f}ms)"
+                    f"(took {(time.time() - step_start) * 1000:.0f}ms)"
                 )
-            
+
             total_span = max_id - effective_min
             datacenter = f"DC{dc_id}" if dc_id > 0 else "Unknown"
-            
+
             # Create task queue and populate with adaptive-sized chunks
             task_queue: asyncio.Queue[Any] = asyncio.Queue()
             current_id = effective_min
             chunks_created = 0
-            
+
             logger.info(
                 f"📊 Creating adaptive chunks from ID range {effective_min}-{max_id} "
                 f"(span: {total_span:,} IDs, density: {estimated_density:.1f} msgs/1K)"
             )
-            
+
             # NEW: Variable-sized chunks based on hot zones and density
             while current_id < max_id:
                 # Query optimal chunk size for current position
                 optimal_chunk_size = hot_zones_mgr.get_optimal_chunk_size(
-                    current_id,
-                    max_id,
-                    datacenter
+                    current_id, max_id, datacenter
                 )
-                
+
                 # Apply density-based override if no hot zone matched
                 if optimal_chunk_size == self.config.shard_chunk_size:
                     # Use density-based chunk size
-                    optimal_chunk_size = hot_zones_mgr.get_chunk_size_for_density(estimated_density)
+                    optimal_chunk_size = hot_zones_mgr.get_chunk_size_for_density(
+                        estimated_density
+                    )
                     if estimated_density > 100:
                         logger.debug(
                             f"🎯 High density ({estimated_density:.1f}), using chunk size: {optimal_chunk_size}"
@@ -822,11 +1002,11 @@ class ShardedTelegramManager(TelegramManager):
                     logger.debug(
                         f"🔥 Hot zone detected at {current_id}, using chunk size: {optimal_chunk_size}"
                     )
-                
+
                 remaining_span = max_id - current_id
                 this_chunk_size = min(optimal_chunk_size, remaining_span)
                 chunk_end = min(current_id + this_chunk_size, max_id)
-                
+
                 if current_id < chunk_end:
                     await task_queue.put((current_id, chunk_end))
                     chunks_created += 1
@@ -834,9 +1014,9 @@ class ShardedTelegramManager(TelegramManager):
                         f"Chunk {chunks_created}: {current_id}-{chunk_end} "
                         f"({chunk_end - current_id} IDs, adaptive size: {optimal_chunk_size})"
                     )
-                
+
                 current_id = chunk_end
-            
+
             logger.info(
                 f"✅ Created {chunks_created} adaptive chunks (sizes: 5K-50K based on hot zones + density)"
             )
@@ -852,7 +1032,7 @@ class ShardedTelegramManager(TelegramManager):
             for i in range(self.worker_count):
                 p = temp_dir / f"shard_{i}.bin"
                 worker_files.append(p)
-                
+
                 # NEW: Pass task_queue instead of fixed ranges
                 task = asyncio.create_task(
                     self._worker_task(
@@ -866,9 +1046,13 @@ class ShardedTelegramManager(TelegramManager):
                     )
                 )
                 tasks.append(task)
-            
-            logger.info(f"⏱️  {len(tasks)} worker tasks created in {(time.time() - step_start)*1000:.0f}ms")
-            logger.info(f"⚡ Total sharding initialization time: {(time.time() - shard_start_time)*1000:.0f}ms")
+
+            logger.info(
+                f"⏱️  {len(tasks)} worker tasks created in {(time.time() - step_start) * 1000:.0f}ms"
+            )
+            logger.info(
+                f"⚡ Total sharding initialization time: {(time.time() - shard_start_time) * 1000:.0f}ms"
+            )
             logger.info("🚀 Starting message merge from worker shards...")
 
             # 5. Yield from Files in Order with Profiling
@@ -928,6 +1112,25 @@ class ShardedTelegramManager(TelegramManager):
 
                         length = struct.unpack(">I", header)[0]
 
+                        # Read compression flag (1 byte)
+                        flag_data = f.read(1)
+                        worker_bytes += len(flag_data)
+                        while len(flag_data) < 1:
+                            if task.done():
+                                exc = task.exception()
+                                if exc:
+                                    raise exc
+                                flag_data += f.read(1 - len(flag_data))
+                                break
+
+                            await asyncio.sleep(0.1)
+                            flag_data += f.read(1 - len(flag_data))
+
+                        if len(flag_data) < 1:
+                            break  # Truncated
+
+                        is_compressed = struct.unpack(">B", flag_data)[0]
+
                         # Read body
                         body = b""
                         while len(body) < length:
@@ -959,17 +1162,98 @@ class ShardedTelegramManager(TelegramManager):
 
                         # Measure deserialization time
                         deserialize_start = time.time()
-                        batch = pickle.loads(body)
+
+                        # Decompress if needed
+                        if is_compressed:
+                            try:
+                                body = zlib.decompress(body)
+                            except Exception as e:
+                                logger.error(
+                                    f"Decompression failed for worker {i}: {e}"
+                                )
+                                raise
+
+                        # S-3: Security fix - use msgpack instead of pickle
+                        batch = msgpack.unpackb(body, raw=False)
+
+                        if getattr(
+                            self.config, "shard_lightweight_schema_enabled", False
+                        ):
+                            # Reconstruct Message objects from lightweight dictionaries
+                            reconstructed_batch = []
+                            for item in batch:
+                                if isinstance(item, dict):
+                                    # Create a minimal Message object. This relies on knowing the structure Telethon expects.
+                                    # A dummy peer is used if peer_id is not directly available, as it's often required.
+                                    peer = item.get(
+                                        "peer_id", types.PeerChannel(channel_id=1)
+                                    )  # Default to PeerChannel if not found
+
+                                    msg = types.Message(
+                                        id=item.get("id", 0),
+                                        peer_id=peer,
+                                        date=item.get("date", None),
+                                        message=item.get("message", ""),
+                                        out=item.get("out", False),
+                                        mentioned=item.get("mentioned", False),
+                                        media_unread=item.get("media_unread", False),
+                                        silent=item.get("silent", False),
+                                        post=item.get("post", False),
+                                        from_scheduled=item.get(
+                                            "from_scheduled", False
+                                        ),
+                                        legacy=item.get("legacy", False),
+                                        edit_hide=item.get("edit_hide", False),
+                                        pinned=item.get("pinned", False),
+                                        noforwards=item.get("noforwards", False),
+                                        reactions=item.get("reactions", None),
+                                        replies=item.get("replies", None),
+                                        forwards=item.get("forwards", None),
+                                        via_bot_id=item.get("via_bot_id", None),
+                                        reply_to=item.get("reply_to", None),
+                                        fwd_from=item.get("fwd_from", None),
+                                        via_bot=item.get("via_bot", None),
+                                        entities=item.get("entities", None),
+                                        media=item.get("media", None),
+                                    )
+                                    # Attach file/photo/document objects if they were preserved
+                                    if item.get("file"):
+                                        msg.file = item["file"]
+                                    if item.get("photo"):
+                                        msg.photo = item["photo"]
+                                    if item.get("document"):
+                                        msg.document = item["document"]
+
+                                    # Reconstruct sender if present in lightweight dict
+                                    if item.get("sender_id"):
+                                        msg.sender_id = item["sender_id"]
+                                        if item.get("sender_username") or item.get(
+                                            "sender_first_name"
+                                        ):
+                                            msg.sender = types.User(
+                                                id=item["sender_id"],
+                                                username=item.get("sender_username"),
+                                                first_name=item.get(
+                                                    "sender_first_name"
+                                                ),
+                                                last_name=item.get("sender_last_name"),
+                                            )
+                                    reconstructed_batch.append(msg)
+                                else:
+                                    reconstructed_batch.append(item)
+                            batch = reconstructed_batch
                         total_deserialize_time_ms += (
                             time.time() - deserialize_start
                         ) * 1000
 
-                        for msg in sorted(batch, key=lambda m: m.id):  # Sort by ID to ensure chronological order (oldest first)
+                        for msg in sorted(
+                            batch, key=lambda m: m.id
+                        ):  # Sort by ID to ensure chronological order (oldest first)
                             # Re-attach worker client to the message for parallel media download
                             msg._client = self.worker_clients[i]
                             yield msg
                             count += 1
-                            
+
                             # 🔍 NEW: Log merge progress every second to detect stalls
                             now = time.time()
                             if now - last_log_time >= 1.0:
@@ -980,7 +1264,7 @@ class ShardedTelegramManager(TelegramManager):
                                 )
                                 last_log_time = now
                                 last_count = count
-                            
+
                             if limit and count >= limit:
                                 break
 
@@ -1005,7 +1289,7 @@ class ShardedTelegramManager(TelegramManager):
                 f"deserialize time {total_deserialize_time_ms:.0f}ms, "
                 f"total time {merge_duration:.2f}s"
             )
-            
+
             # 🔍 NEW: Log slow chunks statistics
             await self._log_slow_chunks_statistics()
 
