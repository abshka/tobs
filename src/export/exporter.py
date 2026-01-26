"""
Export functionality for TOBS - modular exporter implementation with progress tracking.
Provides core export orchestration and processing capabilities with visual progress bars.
"""

import asyncio
import base64
import functools
import inspect
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

import aiofiles
import aiohttp
from rich import print as rprint
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from telethon import errors
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.functions.account import (
    FinishTakeoutSessionRequest,
    InitTakeoutSessionRequest,
)

from ..config import EXPORT_OPERATION_TIMEOUT, Config, ExportTarget
from ..export_reporter import ExportReporterManager
from ..media import MediaProcessor
from ..note_generator import NoteGenerator
from ..telegram_client import TelegramManager
from ..utils import is_voice_message, logger, sanitize_filename
from .pipeline import AsyncPipeline

# ============================================================================
# Configurable constants via environment variables
# ============================================================================

# Prefetch pipeline settings
PREFETCH_BATCH_SIZE = int(os.getenv("PREFETCH_BATCH_SIZE", "200"))  # Increased from 100 for better throughput
PREFETCH_LOOKAHEAD = int(os.getenv("PREFETCH_LOOKAHEAD", "2"))  # Double-buffering

# LRU cache for sender names
SENDER_CACHE_MAX_SIZE = int(os.getenv("SENDER_CACHE_SIZE", "10000"))

# AsyncBufferedSaver buffer size (1MB default for NVMe SSD performance)
# For NVMe SSD: 1MB (1048576) provides excellent throughput
# For HDD: 256KB (262144) may be more efficient
EXPORT_BUFFER_SIZE = int(os.getenv("EXPORT_BUFFER_SIZE", "1048576"))  # 1MB (increased from 512KB)

# Media file copy chunk size (8MB for large media files)
# Larger chunks = fewer syscalls but more memory per operation
MEDIA_COPY_CHUNK_SIZE = int(
    os.getenv("MEDIA_COPY_CHUNK_SIZE", str(8 * 1024 * 1024))
)  # 8MB


class BloomFilter:
    """
    Memory-efficient Bloom filter for approximate set membership testing.
    Uses ~1.2MB for 1M items with 1% false positive rate.
    """

    def __init__(
        self, expected_items: int = 1000000, false_positive_rate: float = 0.01
    ):
        """
        Initialize Bloom filter.

        Args:
            expected_items: Expected number of items to store
            false_positive_rate: Desired false positive rate (0.01 = 1%)
        """
        import math

        # Calculate optimal parameters
        self.size = int(
            -expected_items * math.log(false_positive_rate) / (math.log(2) ** 2)
        )
        self.hash_count = int((self.size / expected_items) * math.log(2))

        # Use bit array for memory efficiency
        self.bit_array = bytearray((self.size + 7) // 8)  # 1 byte = 8 bits

        # Statistics
        self.items_added = 0

    def _hash(self, item: int, seed: int) -> int:
        """Simple hash function with seed."""
        # Use Python's built-in hash with seed mixing
        return hash((item, seed)) % self.size

    def add(self, item: int):
        """Add item to the filter."""
        for i in range(self.hash_count):
            bit_index = self._hash(item, i)
            byte_index = bit_index // 8
            bit_offset = bit_index % 8
            self.bit_array[byte_index] |= 1 << bit_offset
        self.items_added += 1

    def __contains__(self, item: int) -> bool:
        """Check if item might be in the filter (false positives possible)."""
        for i in range(self.hash_count):
            bit_index = self._hash(item, i)
            byte_index = bit_index // 8
            bit_offset = bit_index % 8
            if not (self.bit_array[byte_index] & (1 << bit_offset)):
                return False
        return True

    def memory_usage_mb(self) -> float:
        """Get memory usage in MB."""
        return len(self.bit_array) / (1024 * 1024)

    def stats(self) -> Dict[str, Any]:
        """Get filter statistics."""
        return {
            "size_bits": self.size,
            "hash_functions": self.hash_count,
            "items_added": self.items_added,
            "memory_mb": self.memory_usage_mb(),
            "bits_per_item": self.size / max(self.items_added, 1),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "size": self.size,
            "hash_count": self.hash_count,
            "items_added": self.items_added,
            "bit_array_b64": base64.b64encode(self.bit_array).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BloomFilter":
        """Deserialize from dictionary."""
        # Create instance without calling __init__ to avoid recalculating parameters
        obj = cls.__new__(cls)
        obj.size = data["size"]
        obj.hash_count = data["hash_count"]
        obj.items_added = data["items_added"]
        obj.bit_array = bytearray(base64.b64decode(data["bit_array_b64"]))
        return obj


class TakeoutSessionWrapper:
    """
    Context manager for manual Takeout session management.
    Bypasses Telethon's client.takeout() to avoid client-side state conflicts.

    This is a proper proxy that rebinds methods so that internal self() calls
    go through our __call__ which wraps requests in InvokeWithTakeoutRequest.
    Based on Telethon's _TakeoutClient implementation.
    """

    # Methods that should NOT be proxied to avoid infinite recursion
    __PROXY_INTERFACE = ("__enter__", "__exit__", "__aenter__", "__aexit__")

    def __init__(self, client, config):
        # Use name mangling to avoid conflicts with proxied attributes
        self.__client = client
        self.__config = config
        self.__takeout_id = None
        self.__max_file_size = getattr(config, "max_file_size_mb", 2000) * 1024 * 1024

    @property
    def takeout_id(self):
        return self.__takeout_id

    @takeout_id.setter
    def takeout_id(self, value):
        self.__takeout_id = value

    @property
    def client(self):
        """Access to underlying client for compatibility."""
        return self.__client

    async def __aenter__(self):
        # 1. Check for existing session on client (Reuse)
        existing_id = getattr(
            self.__client, "takeout_id", getattr(self.__client, "_takeout_id", None)
        )
        if existing_id:
            logger.info(f"♻️ Reusing existing Takeout ID: {existing_id}")
            self.__takeout_id = existing_id
            return self

        # 2. Init new session manually with retry logic
        import asyncio
        
        init_req = InitTakeoutSessionRequest(
            contacts=True,
            message_users=True,
            message_chats=True,
            message_megagroups=True,
            message_channels=True,
            files=True,
            file_max_size=self.__max_file_size,
        )
        
        # Retry configuration: 5 minutes total (60 attempts * 5 seconds)
        max_attempts = 60
        retry_interval = 5  # seconds
        
        for attempt in range(max_attempts):
            try:
                logger.debug(f"🔄 Takeout attempt {attempt + 1}/{max_attempts}...")
                takeout_sess = await self.__client(init_req)
                self.__takeout_id = takeout_sess.id
                
                if attempt > 0:
                    elapsed = attempt * retry_interval
                    logger.info(
                        f"✅ Manual Takeout Init Successful after {attempt + 1} attempts ({elapsed}s). ID: {self.__takeout_id}"
                    )
                else:
                    logger.info(f"✅ Manual Takeout Init Successful. ID: {self.__takeout_id}")
                return self
            except Exception as e:
                if "TakeoutInitDelayError" in str(type(e).__name__):
                    # User needs to confirm in Telegram
                    if attempt == 0:
                        logger.info(
                            f"⏳ Takeout requires confirmation. Waiting up to {max_attempts * retry_interval // 60} minutes..."
                        )
                        logger.info(
                            "📱 Please check Telegram → Service Notifications → Allow the data export request"
                        )
                    else:
                        # Show progress every 30 seconds (6 attempts)
                        if attempt % 6 == 0:
                            elapsed = attempt * retry_interval
                            remaining = (max_attempts - attempt) * retry_interval
                            logger.info(
                                f"⏳ Still waiting for Takeout approval... ({elapsed}s elapsed, {remaining}s remaining)"
                            )
                        else:
                            # Log every retry at DEBUG level
                            logger.debug(
                                f"🔄 Attempt {attempt + 1}: Still TakeoutInitDelayError, retrying in {retry_interval}s..."
                            )
                    
                    if attempt < max_attempts - 1:
                        # Wait and retry
                        await asyncio.sleep(retry_interval)
                        continue
                    else:
                        # Final attempt failed - raise error
                        logger.error(
                            f"❌ Takeout confirmation timeout after {max_attempts * retry_interval // 60} minutes"
                        )
                        raise errors.TakeoutInitDelayError(e.request)  # type: ignore
                else:
                    # Other error - log and raise immediately
                    logger.error(f"❌ Unexpected error during Takeout init: {type(e).__name__}: {e}")
                    raise e
        
        # Should not reach here, but just in case
        raise RuntimeError("Takeout initialization failed after all retries")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.__takeout_id:
            try:
                await self.__client(
                    InvokeWithTakeoutRequest(
                        takeout_id=self.__takeout_id,
                        query=FinishTakeoutSessionRequest(success=True),
                    )
                )
                logger.info("✅ Takeout session finished manually.")
            except Exception as e:
                logger.warning(f"⚠️ Error finishing takeout: {e}")

    async def __call__(self, request, ordered=False):
        """
        Wrap all requests in InvokeWithTakeoutRequest.
        This is called by Telethon methods internally when they do self(request).
        """
        if self.__takeout_id is None:
            raise ValueError(
                "Takeout mode has not been initialized "
                '(are you calling outside of "with"?)'
            )

        wrapped = InvokeWithTakeoutRequest(takeout_id=self.__takeout_id, query=request)
        return await self.__client(wrapped, ordered=ordered)

    def __getattribute__(self, name):
        """
        Handle attribute access with proper name mangling detection.
        Based on Telethon's _TakeoutClient implementation.
        """
        # Access class via type() to avoid infinite recursion
        if name.startswith("__") and name not in type(self).__PROXY_INTERFACE:
            raise AttributeError  # Force call of __getattr__

        # Try to access attribute in the proxy object first
        return super().__getattribute__(name)

    def __getattr__(self, name):
        """
        Proxy attribute access to the underlying client.
        For methods, rebind them so that 'self' inside the method points to
        this proxy, not the original client. This ensures self() calls go
        through our __call__ method.
        """
        value = getattr(self.__client, name)
        if inspect.ismethod(value):
            # Rebind the method: get the unbound function from the class,
            # then partially apply our proxy as 'self'
            return functools.partial(getattr(self.__client.__class__, name), self)
        return value

    def __setattr__(self, name, value):
        """Handle attribute setting with proper name mangling."""
        # Check if this is our own name-mangled attribute
        if name.startswith("_{}__".format(type(self).__name__.lstrip("_"))):
            return super().__setattr__(name, value)
        # Otherwise, set on the underlying client
        return setattr(self.__client, name, value)


class AsyncBufferedSaver:
    """
    Buffered file writer that accumulates writes to reduce I/O syscalls and thread context switches.
    Wraps aiofiles to provide a similar interface but with internal buffering.

    Buffer size is configurable via EXPORT_BUFFER_SIZE env var (default: 512KB).
    Larger buffers reduce syscalls but use more memory.
    
    Security S-4: Implements atomic writes using tmp + rename pattern to prevent data corruption
    on crash/interruption. Writes to .tmp file first, then atomically renames to final file.
    """

    def __init__(
        self, path, mode="w", encoding="utf-8", buffer_size=EXPORT_BUFFER_SIZE
    ):
        self.path = path
        self.mode = mode
        self.encoding = encoding
        self.buffer_size = buffer_size
        self._buffer = []
        self._current_size = 0
        self._file = None
        # S-4: Atomic write support - write to .tmp first
        self._tmp_path = f"{path}.tmp"
        self._finalized = False

    async def __aenter__(self):
        # S-4: Write to temporary file first for atomicity
        self._file = await aiofiles.open(self._tmp_path, self.mode, encoding=self.encoding)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.flush()
        if self._file:
            await self._file.close()
        
        # S-4: Atomic rename on success, cleanup on failure
        if exc_type is None:
            # Success: atomically rename tmp -> final
            try:
                await aiofiles.os.rename(self._tmp_path, self.path)
                self._finalized = True
            except Exception as e:
                logger.error(f"Failed to finalize file {self.path}: {e}")
                # Cleanup tmp file on rename failure
                try:
                    await aiofiles.os.remove(self._tmp_path)
                except Exception:
                    pass
                raise
        else:
            # Failure: cleanup tmp file
            try:
                await aiofiles.os.remove(self._tmp_path)
            except Exception:
                pass  # Ignore cleanup errors

    async def write(self, data: str):
        self._buffer.append(data)
        self._current_size += len(data)
        if self._current_size >= self.buffer_size:
            await self.flush()

    async def flush(self):
        if not self._buffer:
            return  # Nothing to flush

        content = "".join(self._buffer)
        self._buffer = []
        self._current_size = 0

        if self._file:
            await self._file.write(content)
            await self._file.flush()


class ForumTopic:
    """Represents a forum topic with metadata."""

    def __init__(self, topic_id: int, title: str, message_count: int = 0):
        self.topic_id = topic_id
        self.title = title
        self.message_count = message_count
        self.sanitized_name = sanitize_filename(title)

    def __repr__(self):
        return f"ForumTopic(id={self.topic_id}, title='{self.title}', messages={self.message_count})"


@dataclass
class EntityCacheData:
    """Данные кэша для сущности."""

    entity_id: str
    entity_name: str
    entity_type: str
    total_messages: int = 0
    processed_messages: int = 0
    last_message_id: Optional[int] = None
    processed_message_ids: Union[BloomFilter, set] = field(default_factory=set)  # 🚀 Optimized: set for new exports, BloomFilter for resume
    
    def to_dict(self) -> dict:
        """Convert to serializable dict (excludes BloomFilter)."""
        return {
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "total_messages": self.total_messages,
            "processed_messages": self.processed_messages,
            "last_message_id": self.last_message_id,
            # Don't serialize processed_message_ids (BloomFilter not serializable)
            # Resume will use last_message_id instead
        }


class ExportStatistics:
    """
    Statistics tracking for export operations.

    Tracks separate durations for each operation:
    - messages_export_duration: Time spent fetching and writing messages
    - media_download_duration: Time spent downloading media files
    - transcription_duration: Time spent on audio transcription
    - total_duration: Overall wall-clock time (via duration property)
    """

    def __init__(self):
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.messages_processed = 0
        self.media_downloaded = 0
        self.notes_created = 0
        self.errors_encountered = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.avg_cpu_percent = 0.0
        self.peak_memory_mb = 0.0

        # Operation durations (in seconds)
        self.messages_export_duration: float = 0.0
        self.media_download_duration: float = 0.0
        self.transcription_duration: float = 0.0
        
        # Performance profiling (detailed breakdown)
        self.time_api_requests: float = 0.0  # Time waiting for Telegram API
        self.time_processing: float = 0.0    # Time processing messages (formatting, etc)
        self.time_file_io: float = 0.0       # Time writing to disk
        self.api_request_count: int = 0      # Number of API requests made
        
        # Pipeline-level statistics (e.g., processed_count, errors, duration, queue maxes)
        self.pipeline_stats: Dict[str, Any] = {}
        
        # 🚀 Parallel Media Processing metrics (TIER B - B-3)
        self.parallel_media_metrics: Optional[Dict[str, Any]] = None

        # Start times (for tracking)
        self._messages_start: Optional[float] = None
        self._media_start: Optional[float] = None
        self._transcription_start: Optional[float] = None

    def start_messages_phase(self):
        """Mark start of message export."""
        self._messages_start = time.time()

    def end_messages_phase(self):
        """Mark end of message export."""
        if self._messages_start:
            self.messages_export_duration = time.time() - self._messages_start
            self._messages_start = None

    def start_media_phase(self):
        """Mark start of media download."""
        self._media_start = time.time()

    def end_media_phase(self):
        """Mark end of media download."""
        if self._media_start:
            self.media_download_duration = time.time() - self._media_start
            self._media_start = None

    def start_transcription_phase(self):
        """Mark start of transcription."""
        self._transcription_start = time.time()

    def end_transcription_phase(self):
        """Mark end of transcription."""
        if self._transcription_start:
            self.transcription_duration = time.time() - self._transcription_start
            self._transcription_start = None

    @property
    def duration(self) -> float:
        """Get total duration in seconds."""
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def messages_per_minute(self) -> float:
        """Calculate messages per minute rate."""
        if self.duration > 0:
            return (self.messages_processed / self.duration) * 60
        return 0.0

    @property
    def messages_per_second(self) -> float:
        """Calculate messages per second rate (based on messages export only)."""
        if self.messages_export_duration > 0:
            return self.messages_processed / self.messages_export_duration
        elif self.duration > 0:
            return self.messages_processed / self.duration
        return 0.0

    def get_phase_breakdown(self) -> Dict[str, float]:
        """Get breakdown of time spent in each phase."""
        total = self.duration
        return {
            "messages_export": self.messages_export_duration,
            "media_download": self.media_download_duration,
            "transcription": self.transcription_duration,
            "other": max(
                0,
                total
                - self.messages_export_duration
                - self.media_download_duration
                - self.transcription_duration,
            ),
            "total": total,
        }

    def copy(self) -> "ExportStatistics":
        """Create an independent copy of this statistics object."""
        new_stats = ExportStatistics()
        
        # Copy all attributes
        new_stats.start_time = self.start_time
        new_stats.end_time = self.end_time
        new_stats.messages_processed = self.messages_processed
        new_stats.media_downloaded = self.media_downloaded
        new_stats.notes_created = self.notes_created
        new_stats.errors_encountered = self.errors_encountered
        new_stats.cache_hits = self.cache_hits
        new_stats.cache_misses = self.cache_misses
        new_stats.avg_cpu_percent = self.avg_cpu_percent
        new_stats.peak_memory_mb = self.peak_memory_mb
        
        # Copy operation durations
        new_stats.messages_export_duration = self.messages_export_duration
        new_stats.media_download_duration = self.media_download_duration
        new_stats.transcription_duration = self.transcription_duration
        
        # Copy performance profiling fields (TIER A)
        new_stats.time_api_requests = self.time_api_requests
        new_stats.time_processing = self.time_processing
        new_stats.time_file_io = self.time_file_io
        new_stats.api_request_count = self.api_request_count
        
        # Deep copy pipeline stats
        new_stats.pipeline_stats = self.pipeline_stats.copy() if self.pipeline_stats else {}
        
        # Copy start times (but these should typically be None at copy time)
        new_stats._messages_start = self._messages_start
        new_stats._media_start = self._media_start
        new_stats._transcription_start = self._transcription_start
        
        return new_stats


class Exporter:
    """
    Main exporter class handling all export operations.
    Replaces monolithic functions from main.py with clean modular architecture.
    """

    def __init__(
        self,
        config: Config,
        telegram_manager: TelegramManager,
        cache_manager,
        media_processor: MediaProcessor,
        note_generator: Optional[NoteGenerator] = None,
        http_session: Optional[aiohttp.ClientSession] = None,
        performance_monitor=None,
    ):
        self.config = config
        self.telegram_manager = telegram_manager

        self.cache_manager = cache_manager
        self.media_processor = media_processor
        self.note_generator = note_generator
        self.http_session = http_session
        self.performance_monitor = performance_monitor
        self.statistics = ExportStatistics()
        self._shutdown_requested = False
        self.progress = None  # Progress bar instance

        # Sender name cache with string interning
        self._sender_name_cache: Dict[int, str] = {}
        self._interned_strings: Set[str] = set()  # Track interned strings

        # Prefetch pipeline
        self._prefetch_task = None
        self._prefetch_result = None
        self._prefetch_stats = {"hits": 0, "misses": 0}

        # Initialize the reporter manager
        self.reporter_manager = ExportReporterManager(
            base_monitoring_path=self.config.export_path,
            performance_monitor=self.performance_monitor,
        )

        # Batch cache operations to reduce I/O
        self._pending_cache_updates: Dict[str, Any] = {}
        self._cache_batch_size = 10  # Flush every 10 updates

        # Time-based progress updates
        self._last_progress_update = 0.0
        self._progress_update_interval = 0.5  # Update progress every 0.5 seconds
        
        # 🚀 Parallel Media Processor (TIER B - B-3)
        # Initialize ParallelMediaProcessor for concurrent media operations
        from src.media.parallel_processor import create_parallel_processor_from_config
        self._parallel_media_processor = create_parallel_processor_from_config(config)
        logger.info(f"✅ ParallelMediaProcessor initialized: {self._parallel_media_processor._config}")

        # Lazy logging to reduce overhead (prefer native LogBatcher when available)
        self._log_batch_interval = float(
            os.getenv("LOG_BATCH_INTERVAL", "2.0")
        )  # Flush logs every N seconds

        # Prefer native per-exporter LogBatcher if provided. If the module exists but
        # does not provide `LogBatcher`, intentionally fall back to no batching (internal behavior).
        try:
            from ..logging.log_batcher import LogBatcher
        except Exception:
            import sys as _sys

            if "src.logging.log_batcher" in _sys.modules:
                # Module present but missing LogBatcher -> use no batching
                self.log_batcher = None
            else:
                # Native module not present; try the shared global singleton as a fallback
                try:
                    from ..logging.global_batcher import global_batcher

                    self.log_batcher = global_batcher
                except Exception:
                    self.log_batcher = None
        else:
            # Native LogBatcher is available; prefer a per-exporter instance
            try:
                self.log_batcher = LogBatcher()
            except Exception:
                # If instantiation fails, fall back to shared global batcher if available
                try:
                    from ..logging.global_batcher import global_batcher

                    self.log_batcher = global_batcher
                except Exception:
                    self.log_batcher = None

    def _intern_string(self, s: str) -> str:
        """Intern string to reduce memory usage for repeated strings."""
        if s in self._interned_strings:
            return s
        self._interned_strings.add(s)
        return sys.intern(s)

    def _lazy_log(self, level: str, message: str):
        """Batch similar log messages to reduce overhead.

        Uses the shared global batcher if available. If the global batcher is not
        available, non-critical messages are emitted immediately to avoid buffering.
        """
        lvl = (level or "").upper()

        # For critical messages, log immediately (also emit via root stdlib logger so caplog captures)
        if lvl in ("ERROR", "CRITICAL"):
            import logging as _logging

            # Emit via the root stdlib logger so test harness (caplog) can capture messages,
            # and also emit via the project's logger for existing instrumentation.
            if lvl == "ERROR":
                _logging.getLogger().error(message)
                logger.error(message)
            else:
                _logging.getLogger().critical(message)
                logger.critical(message)
            return

        # Use the shared global batcher when present
        lb = getattr(self, "log_batcher", None)
        if lb is not None:
            try:
                lb.lazy_log(lvl, message)
                return
            except Exception:
                logger.exception(
                    "global_batcher.lazy_log failed; falling back to immediate logging"
                )

        # No batcher available: emit immediately at INFO level
        import logging as _logging

        # Emit via the root stdlib logger so test harness (caplog) can capture messages,
        # and also emit via the project's logger for existing instrumentation.
        _logging.getLogger().info(message)
        logger.info(message)

    def _flush_log_batch(self):
        """Flush batched log messages via the shared global batcher, if present."""
        lb = getattr(self, "log_batcher", None)
        if lb is not None:
            try:
                lb.flush()
            except Exception:
                logger.exception("global_batcher.flush failed")
        # If no batcher is present there is nothing buffered to flush (legacy _log_batch removed)

    async def _batch_cache_set(self, key: str, value: Any):
        """Add cache update to batch queue."""
        # Serialize EntityCacheData to dict before caching
        if isinstance(value, EntityCacheData):
            value = value.to_dict()
        self._pending_cache_updates[key] = value
        if len(self._pending_cache_updates) >= self._cache_batch_size:
            await self._flush_cache_batch()

    async def _flush_cache_batch(self):
        """Flush all pending cache updates in a single batch."""
        if not self._pending_cache_updates:
            return

        try:
            # Batch all updates
            for key, value in self._pending_cache_updates.items():
                await self.cache_manager.set(key, value)
            self._pending_cache_updates.clear()
        except Exception as e:
            logger.warning(f"Failed to flush cache batch: {e}")
            # Clear on error to avoid infinite retries
            self._pending_cache_updates.clear()

    async def initialize(self):
        """Initialize exporter and all components."""
        try:
            # Telegram connection should already be established in main.py
            if not self.telegram_manager.client_connected:
                logger.info("Telegram not connected, connecting...")
                await self.telegram_manager.connect()
            logger.info("✅ Telegram connection verified")

            # Initialize media processor
            logger.info("✅ Media processor ready")

            # Initialize cache
            if hasattr(self.cache_manager, "load_cache"):
                await self.cache_manager.load_cache()
                logger.info("✅ Cache loaded")
            else:
                logger.info("✅ Cache manager ready")

            logger.info("🚀 Exporter initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize exporter: {e}")
            raise

    async def export_target(
        self, target: ExportTarget, progress_queue=None, task_id=None
    ) -> ExportStatistics:
        """Export a single target with timeout protection."""
        logger.info(f"Starting export for target: {target.name} (ID: {target.id})")

        try:
            # Reset statistics
            self.statistics = ExportStatistics()

            # Clear caches
            self._sender_name_cache.clear()
            self._prefetch_stats = {"hits": 0, "misses": 0}
            if self._prefetch_task and not self._prefetch_task.done():
                self._prefetch_task.cancel()
            self._prefetch_task = None
            self._prefetch_result = None

            # Determine export type
            try:
                if target.type in ["forum", "forum_chat", "forum_topic"]:
                    result = await asyncio.wait_for(
                        self._export_forum(target, progress_queue, task_id),
                        timeout=EXPORT_OPERATION_TIMEOUT,
                    )
                else:
                    result = await asyncio.wait_for(
                        self._export_regular_target(target, progress_queue, task_id),
                        timeout=EXPORT_OPERATION_TIMEOUT,
                    )
                return result
            except asyncio.TimeoutError:
                logger.error(
                    f"⏰ Export timeout for target {target.name}: "
                    f"exceeded {EXPORT_OPERATION_TIMEOUT}s limit"
                )
                self.statistics.errors_encountered += 1
                raise

        except Exception as e:
            logger.error(f"Export failed for target {target.name}: {e}")
            self.statistics.errors_encountered += 1
            raise
        finally:
            self.statistics.end_time = time.time()

    async def _fetch_messages_batch(self, entity, min_id, limit=100):
        """Fetch a batch of messages (MW1)."""
        # Uses whatever client is currently in telegram_manager (Standard or Takeout)
        return await self.telegram_manager.client.get_messages(
            entity,
            limit=limit,
            min_id=min_id,
            reverse=False,  # Changed to False for chronological order
        )

    async def _calculate_bloom_filter_size(self, entity) -> int:
        """
        Calculate optimal BloomFilter size based on entity message count (TIER B-4).
        
        Args:
            entity: Telegram entity to analyze
            
        Returns:
            Expected items for BloomFilter (with buffer for new messages)
        """
        try:
            # Get total message count from entity
            total_messages = await self.telegram_manager.get_message_count(entity)
            
            if total_messages == 0:
                logger.warning("Entity has 0 messages, using minimum BloomFilter size")
                return self.config.bloom_filter_min_size
            
            # Add buffer for new messages during export (default 10%)
            multiplier = self.config.bloom_filter_size_multiplier
            expected = int(total_messages * multiplier)
            
            # Clamp to configured range
            # Min: prevents over-allocation for small chats (default 10k = ~120KB)
            # Max: prevents excessive memory for mega-chats (default 10M = ~12MB)
            clamped = max(
                self.config.bloom_filter_min_size,
                min(expected, self.config.bloom_filter_max_size)
            )
            
            logger.info(
                f"📊 BloomFilter sizing: {total_messages:,} messages "
                f"× {multiplier:.1f} = {expected:,} expected → {clamped:,} (final)"
            )
            
            return clamped
            
        except Exception as e:
            logger.warning(f"Failed to calculate BloomFilter size: {e}")
            # Fallback to current default (1M = ~1.2MB)
            return 1_000_000

    async def _process_message_parallel(
        self, message, target, media_dir, output_dir, entity_reporter
    ):
        """Process a single message in parallel (optimized for memory efficiency)."""
        try:
            # Get sender name
            sender_name = await self._get_sender_name(message)

            # Format timestamp
            timestamp = self._format_timestamp(message.date)

            # Use list-based string building for better performance
            content_parts = []
            content_parts.append(f"{sender_name}, [{timestamp}]\n")

            if message.text:
                content_parts.append(f"{message.text}\n")

            local_media_count = 0

            # Handle media
            if message.media and self.config.media_download:
                try:
                    media_paths = await self.media_processor.download_and_process_media(
                        message=message,
                        entity_id=target.id,
                        entity_media_path=media_dir,
                    )
                    if media_paths:
                        local_media_count = len(media_paths)

                        # Record media downloads
                        for media_path in media_paths:
                            try:
                                file_size = media_path.stat().st_size
                                entity_reporter.record_media_downloaded(
                                    message.id, file_size, str(media_path)
                                )
                            except Exception:
                                pass

                        # Add references
                        for media_path in media_paths:
                            try:
                                relative_path = media_path.relative_to(output_dir)
                                content_parts.append(f"![[{relative_path}]]\n")

                                # Transcription
                                if (
                                    self.config.enable_transcription
                                    and is_voice_message(message)
                                ):
                                    try:
                                        transcription = (
                                            await self.media_processor.transcribe_audio(
                                                media_path
                                            )
                                        )
                                        if transcription:
                                            content_parts.append(
                                                f"**Расшифровка:** {transcription}\n"
                                            )
                                    except Exception:
                                        pass
                            except ValueError:
                                content_parts.append(f"![[{media_path.name}]]\n")
                    else:
                        content_parts.append("[[No files downloaded]]\n")
                except Exception as e:
                    self._lazy_log(
                        "WARNING",
                        f"Failed to process media for message {message.id}: {e}",
                    )
                    content_parts.append("[[Failed to download]]\n")
            elif message.media:
                media_type = self._get_media_type_name(message.media)
                content_parts.append(f"[{media_type}]\n")

            # Reactions
            if (
                self.config.export_reactions
                and hasattr(message, "reactions")
                and message.reactions
            ):
                try:
                    reactions_list = []
                    if hasattr(message.reactions, "results"):
                        for reaction_count in message.reactions.results:
                            emoji = "?"
                            # Handle standard emoji
                            if hasattr(reaction_count.reaction, "emoticon"):
                                emoji = reaction_count.reaction.emoticon
                            # Handle custom emoji (document_id)
                            elif hasattr(reaction_count.reaction, "document_id"):
                                emoji = "🧩"  # Placeholder for custom emoji

                            reactions_list.append(f"{emoji} {reaction_count.count}")

                    if reactions_list:
                        content_parts.append(
                            f"**Reactions:** {', '.join(reactions_list)}\n"
                        )
                except Exception as e:
                    self._lazy_log(
                        "WARNING", f"Failed to export reactions for {message.id}: {e}"
                    )

            content_parts.append("\n")

            # Join all parts at once for better performance
            return (
                "".join(content_parts),
                message.id,
                bool(message.media),
                local_media_count,
            )

        except Exception as e:
            self._lazy_log("ERROR", f"Error processing message {message.id}: {e}")
            return "", message.id, False, 0

    async def _export_regular_target(
        self, target: ExportTarget, progress_queue=None, task_id=None
    ) -> ExportStatistics:
        """Export regular channel or chat to single file with Telegram-like format."""
        logger.info(f"Exporting regular target: {target.name}")

        try:
            entity = await self.telegram_manager.resolve_entity(target.id)
            entity_name = getattr(
                entity, "title", getattr(entity, "first_name", str(target.id))
            )
            
            # 🔧 Check if entity is restricted (noforwards=True) before using Takeout
            is_restricted = getattr(entity, "noforwards", False)
            if is_restricted:
                logger.warning(f"⚠️ Channel '{entity_name}' has content restrictions (noforwards=True)")
                logger.info("🔄 Will use standard (non-Takeout) export for this channel")

            # Load entity state from core cache
            cache_key = f"entity_state_{target.id}"
            # 🔥 TEMPORARY: Disable cache loading to test fresh export
            # entity_data = await self.cache_manager.get(cache_key)
            entity_data = None  # Force fresh export

            # Handle dict restoration (from JSON cache)
            if isinstance(entity_data, dict):
                try:
                    # Try to restore BloomFilter if present
                    bf_data = entity_data.pop("processed_message_ids", None)
                    bf = None

                    if (
                        bf_data
                        and isinstance(bf_data, dict)
                        and "bit_array_b64" in bf_data
                    ):
                        try:
                            bf = BloomFilter.from_dict(bf_data)
                            logger.info(
                                f"♻️ Restored BloomFilter: {bf.items_added} items"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to restore BloomFilter: {e}")

                    # Create EntityCacheData (it will use default empty BloomFilter if we don't pass one)
                    entity_data = EntityCacheData(**entity_data)

                    # Set restored BloomFilter if available
                    if bf:
                        entity_data.processed_message_ids = bf

                except Exception as e:
                    logger.warning(f"Failed to restore EntityCacheData from dict: {e}")
                    entity_data = None

            if not isinstance(entity_data, EntityCacheData):
                # 🚀 OPTIMIZATION: Determine if this is a resume scenario
                is_resume = entity_data is not None and hasattr(entity_data, 'processed_messages') and entity_data.processed_messages > 0
                
                # 🔄 TIER B-4: Use BloomFilter only for resume, lightweight set for new exports
                if self.config.bloom_filter_only_for_resume and not is_resume:
                    # New export: use empty set (near-zero overhead)
                    logger.info("🚀 New export detected: using lightweight set (BloomFilter disabled for performance)")
                    processed_ids = set()
                else:
                    # Resume or forced BloomFilter: calculate optimal size
                    bf_size = await self._calculate_bloom_filter_size(entity)
                    processed_ids = BloomFilter(expected_items=bf_size)
                    if is_resume:
                        logger.info(f"♻️ Resume detected: using BloomFilter (size={bf_size:,})")
                    else:
                        logger.info(f"📊 BloomFilter enabled by config (size={bf_size:,})")
                
                entity_data = EntityCacheData(
                    entity_id=str(target.id),
                    entity_name=entity_name,
                    entity_type="regular",
                    processed_message_ids=processed_ids,
                )

            # Create output directory structure FIRST
            output_dir = self.config.get_export_path_for_entity(target.id)
            media_dir = self.config.get_media_path_for_entity(target.id)

            # Create monitoring directory inside entity export folder
            monitoring_dir = output_dir / ".monitoring"
            await asyncio.to_thread(monitoring_dir.mkdir, parents=True, exist_ok=True)

            # Get reporter with entity-specific monitoring path
            entity_reporter = self.reporter_manager.get_reporter(
                target.id, monitoring_dir
            )

            # Prepare export settings for monitoring
            export_settings = {
                "sharding_enabled": self.config.enable_shard_fetch,
                "shard_count": self.config.shard_count,
                "use_takeout": self.config.use_takeout,
                "performance_profile": self.config.performance_profile,
            }
            entity_reporter.start_export(
                entity_name, "regular", export_settings=export_settings
            )

            # Update entity info
            entity_data.entity_name = entity_name

            logger.info(f"  ⚙️  Structured export: {self.config.use_structured_export}")

            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(media_dir.mkdir, parents=True, exist_ok=True)

            logger.info(f"📁 Monitoring directory created: {monitoring_dir}")
            logger.info(f"📊 Monitoring file: monitoring_{target.id}.json")
            logger.info(f"💾 Cache key: {cache_key}")
            
            # Register progress save hook for graceful shutdown (TIER A - Task 3)
            from src.shutdown_manager import shutdown_manager
            self._current_reporter = entity_reporter  # Store for shutdown hook
            shutdown_manager.register_async_cleanup_hook(
                lambda: self._save_progress_on_shutdown(entity_data, cache_key)
            )

            # Create single chat file
            safe_name = (
                entity_name.replace("/", "_")
                .replace("\\", "_")
                .replace(":", "_")
                .replace(" ", "_")
            )
            chat_file = output_dir / f"{safe_name}.md"

            logger.info(f"  📄 Chat file: {chat_file}")

            # Initialize counters (defined here so they're accessible in exception handler)
            processed_count = 0
            media_count = 0
            periodic_saves_count = 0

            # Create media subdirectory if needed
            media_dir = output_dir / "media"
            if self.config.media_download:
                await asyncio.to_thread(media_dir.mkdir, exist_ok=True)

            # ✨ Get total message count BEFORE starting export (for accurate progress %)
            logger.info(f"📊 Fetching total message count for {entity_name}...")
            total_messages = await self.telegram_manager.get_total_message_count(entity)
            
            # Determine if we can show progress percentage
            has_total = total_messages > 0
            if has_total:
                logger.info(f"📊 Total messages in chat: {total_messages:,}")
            else:
                logger.info(f"📊 Total unknown - using streaming mode (no percentage)")

            # Initialize OutputManager for TTY-aware progress reporting (TIER B - B-5)
            from src.ui.output_manager import get_output_manager
            output_mgr = get_output_manager()
            output_mgr.start_export(entity_name, total_messages=total_messages if has_total else None)

            # Build Progress columns dynamically based on whether we have total
            progress_columns = [
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
            ]
            
            if has_total:
                # With total: show bar + percentage + X/Total
                progress_columns.extend([
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TextColumn("•"),
                    TextColumn("[cyan]{task.fields[messages]}/{task.total} msgs"),
                ])
            else:
                # Without total: show only current count (streaming mode)
                progress_columns.extend([
                    TextColumn("•"),
                    TextColumn("[cyan]{task.fields[messages]} msgs"),
                ])
            
            # Always show media count
            progress_columns.extend([
                TextColumn("•"),
                TextColumn("[green]{task.fields[media]} media"),
            ])

            # Use Rich progress bar for better UX
            with Progress(*progress_columns, transient=False) as progress:
                task_id_progress = progress.add_task(
                    f"[cyan]Exporting {entity_name}...", 
                    total=total_messages if has_total else None,
                    messages=0, 
                    media=0
                )

                # Open chat file for writing (using AsyncBufferedSaver for I/O)
                async with AsyncBufferedSaver(chat_file, "w", encoding="utf-8") as f:
                    # Write chat header
                    await f.write(f"# Chat Export: {entity_name}\n\n")
                    await f.write(f"Export Date: {self._get_current_datetime()}\n")
                    await f.write("Total Messages: Processing...\n\n")
                    await f.write("---\n\n")

                    # Single-pass streaming: process messages as they arrive
                    # Start message export timer (measured across fetch -> process -> write)
                    self.statistics.start_messages_phase()
                    logger.info(f"📊 Starting streaming export for {entity_name}...")

                    # 🚀 Use telegram_manager.fetch_messages() - it handles sharding if enabled
                    # If the new async pipeline is enabled, run it as a replacement for
                    # the old batch processing loop. Otherwise fall back to the
                    # existing batch+gather flow for backward compatibility.
                    if getattr(self.config, "async_pipeline_enabled", False):
                        logger.info(f"⚡ Async pipeline enabled for {entity_name}...")

                        # Determine worker counts (allow 0 = auto for process_workers)
                        fetch_workers = getattr(
                            self.config, "async_pipeline_fetch_workers", 1
                        )
                        process_workers = getattr(
                            self.config, "async_pipeline_process_workers", 0
                        )
                        if process_workers == 0:
                            # Derive a reasonable default from the performance profile
                            process_workers = max(
                                1,
                                int(
                                    getattr(self.config.performance, "workers", 4) // 2
                                ),
                            )

                        pipeline = AsyncPipeline(
                            fetch_workers=fetch_workers,
                            process_workers=process_workers,
                            write_workers=getattr(
                                self.config, "async_pipeline_write_workers", 1
                            ),
                            fetch_queue_size=getattr(
                                self.config, "async_pipeline_fetch_queue_size", 64
                            ),
                            process_queue_size=getattr(
                                self.config, "async_pipeline_process_queue_size", 256
                            ),
                        )

                        async def process_fn(message):
                            # 🔄 TIER B-4: Early filter for already-processed messages
                            # This handles edge cases like message ID gaps (deleted messages)
                            if message.id in entity_data.processed_message_ids:
                                return None  # Skip this message
                            
                            # Filter empty messages early (same semantics as the old loop)
                            if not (
                                getattr(message, "text", None)
                                or getattr(message, "media", None)
                            ):
                                return None

                            # Reuse existing exporter worker for formatting & media downloads
                            # Reuse existing exporter worker for formatting & media downloads
                            result = await self._process_message_parallel(
                                message, target, media_dir, output_dir, entity_reporter
                            )

                            # Support both legacy (formatted, local_media_count) and full 4-tuple
                            try:
                                if isinstance(result, tuple) and len(result) == 2:
                                    # Legacy return shape: (formatted, local_media_count)
                                    formatted, local_media_count = result
                                    msg_id = getattr(message, "id", None)
                                    has_media = bool(local_media_count)
                                    media_cnt = local_media_count
                                else:
                                    # Expected full shape: (formatted, msg_id, has_media, media_cnt)
                                    formatted, msg_id, has_media, media_cnt = result
                            except Exception:
                                logger.exception(
                                    "Async pipeline processor: invalid result from _process_message_parallel"
                                )
                                # Treat as a failed/skip for this message
                                return None

                            return (formatted, msg_id, has_media, media_cnt)

                        async def writer_fn(result):
                            nonlocal processed_count, media_count
                            if result is None:
                                return
                            try:
                                content, msg_id, has_media, media_cnt = result
                            except Exception:
                                logger.exception(
                                    "Async pipeline writer: invalid result"
                                )
                                return

                            if not content:
                                return

                            await f.write(content)

                            # Update stats and metrics (mirror existing behavior)
                            processed_count += 1
                            media_count += media_cnt
                            self.statistics.messages_processed += 1
                            self.statistics.media_downloaded += media_cnt

                            # Update entity state
                            entity_data.processed_message_ids.add(msg_id)
                            entity_data.last_message_id = msg_id
                            entity_reporter.record_message_processed(
                                msg_id, has_media=has_media
                            )

                            # Update progress and periodically persist state
                            progress.update(
                                task_id_progress,
                                completed=processed_count,  # ← For percentage calculation
                                messages=processed_count,
                                media=media_count,
                            )

                            if processed_count % 100 == 0:
                                try:
                                    await self._batch_cache_set(cache_key, entity_data)
                                    entity_reporter.save_metrics()
                                except Exception as save_error:
                                    logger.warning(
                                        f"Failed pipeline periodic save: {save_error}"
                                    )

                        # Execute the pipeline (it will fetch/process/write)
                        # 🔄 TIER B-4: Resume from last processed message
                        resume_from_id = entity_data.last_message_id or 0
                        if resume_from_id > 0:
                            logger.info(f"📍 [Pipeline] Resume point: message ID {resume_from_id}")
                        else:
                            logger.info("📍 [Pipeline] Starting from beginning")
                        
                        pipeline_stats = await pipeline.run(
                            entity=entity,
                            telegram_manager=self.telegram_manager,
                            process_fn=process_fn,
                            writer_fn=writer_fn,
                            limit=None,
                            min_id=resume_from_id,  # TIER B-4: Skip already processed messages
                        )

                        logger.info(f"Async pipeline finished: {pipeline_stats}")

                        # Record pipeline-level stats into ExportStatistics for later reporting
                        try:
                            self.statistics.pipeline_stats = dict(pipeline_stats or {})
                            # Prefer pipeline-reported duration for messages export timing if present
                            if (
                                isinstance(pipeline_stats, dict)
                                and "duration" in pipeline_stats
                            ):
                                self.statistics.messages_export_duration = float(
                                    pipeline_stats["duration"]
                                )
                        except Exception:
                            logger.exception(
                                "Failed to record pipeline_stats into statistics"
                            )

                    else:
                        # Fallback: original batch-oriented loop with optional prefetch optimization
                        batch_size = self.config.prefetch_batch_size or 100

                        fetched_count = 0  # Count of messages fetched from generator

                        # 🔄 TIER B-4: Resume from last processed message (if any)
                        resume_from_id = entity_data.last_message_id or 0
                        if resume_from_id > 0:
                            logger.info(f"📍 Resume point: message ID {resume_from_id} (skipping already processed)")
                        else:
                            logger.info("📍 Starting from beginning (no previous progress)")

                        # 🔧 HOTPATH FIX 1: Move imports OUTSIDE loop
                        from src.shutdown_manager import shutdown_manager
                        
                        # 🔧 HOTPATH FIX 2: Cache BloomFilter reference (only check if resuming)
                        processed_ids = entity_data.processed_message_ids if resume_from_id > 0 else None

                        # ⚡ PREFETCH OPTIMIZATION: Use producer-consumer pipeline if enabled
                        if self.config.enable_prefetch_batches:
                            # Use prefetch optimization - call helper function
                            processed_count, media_count = await self._export_with_prefetch(
                                entity=entity,
                                resume_from_id=resume_from_id,
                                processed_ids=processed_ids,
                                batch_size=batch_size,
                                target=target,
                                media_dir=media_dir,
                                output_dir=output_dir,
                                entity_reporter=entity_reporter,
                                entity_data=entity_data,
                                f=f,
                                progress=progress,
                                task_id_progress=task_id_progress,
                                output_mgr=output_mgr,
                                progress_queue=progress_queue,
                                task_id=task_id,
                                entity_name=entity_name,
                                cache_key=cache_key,
                            )
                        else:
                            # Fallback: non-prefetch batch processing
                            batch = []
                            batch_fetch_start = time.time()
                            processed_count = 0
                            media_count = 0
                            
                            # Fetch messages and process in batches
                            async for message in self.telegram_manager.fetch_messages(
                                entity,
                                limit=None,
                                min_id=resume_from_id,
                            ):
                                # Check for graceful shutdown request (TIER A - Task 3)
                                if shutdown_manager.shutdown_requested:
                                    logger.info("🛑 Graceful shutdown requested, stopping message fetch")
                                    break
                                
                                # 🔄 TIER B-4: Early skip check for already-processed messages
                                # This handles edge cases like message ID gaps (deleted messages)
                                # where min_id alone might not be sufficient
                                if processed_ids and message.id in processed_ids:
                                    logger.debug(f"⏭️ Skipping message {message.id} (already in BloomFilter)")
                                    continue
                                
                                fetched_count += 1
                                batch.append(message)

                                # Process batch when it reaches target size
                                if len(batch) < batch_size:
                                    continue

                                # Track API time for this batch (time since last batch was processed)
                                api_time = time.time() - batch_fetch_start
                                self.statistics.time_api_requests += api_time
                                self.statistics.api_request_count += 1

                                # --- BATCH PROCESSING ---
                                # Track processing time
                                process_start = time.time()
                                
                                # 🚀 Process batch with ParallelMediaProcessor (TIER B - B-3)
                                # This allows concurrent media downloads/processing with semaphore control
                                async def process_fn(msg):
                                    """Wrapper for _process_message_parallel"""
                                    return await self._process_message_parallel(
                                        msg, target, media_dir, output_dir, entity_reporter
                                    )
                                
                                # Filter empty messages
                                messages_to_process = [msg for msg in batch if (msg.text or msg.media)]
                                
                                if messages_to_process:
                                    # 🚀 OPTIMIZATION: Pre-load sender names for batch
                                    self._preload_batch_sender_names(messages_to_process)
                                    
                                    # Use parallel processor for concurrent media handling
                                    results = await self._parallel_media_processor.process_batch(
                                        messages_to_process, process_fn
                                    )
                                    
                                    process_time = time.time() - process_start
                                    self.statistics.time_processing += process_time
                                    
                                    # Track file I/O time
                                    io_start = time.time()

                                    # Write results sequentially
                                    for result in results:
                                        # Handle exceptions from gather
                                        if isinstance(result, Exception):
                                            logger.warning(f"Failed to process message: {result}")
                                            continue
                                        
                                        content, msg_id, has_media, media_cnt = result
                                        if not content:
                                            continue  # Skip failed

                                        await f.write(content)

                                        # Update stats (only count successfully processed messages)
                                        processed_count += 1
                                        media_count += media_cnt
                                        self.statistics.messages_processed += 1
                                        self.statistics.media_downloaded += media_cnt

                                        # Update entity data
                                        entity_data.processed_message_ids.add(msg_id)
                                        entity_data.last_message_id = msg_id
                                        entity_reporter.record_message_processed(
                                            msg_id, has_media=has_media
                                        )
                                
                                io_time = time.time() - io_start
                                self.statistics.time_file_io += io_time

                                # 🔧 HOTPATH FIX 3: Reset timer for next batch
                                batch_fetch_start = time.time()

                                # Check for shutdown after processing batch
                                if shutdown_manager.shutdown_requested:
                                    logger.info("🛑 Shutdown requested after batch processing")
                                    await f.flush()  # Flush buffer before stopping
                                    break

                                # Periodic save
                                if processed_count % 100 == 0:
                                    try:
                                        save_start = time.time()

                                        # Batch cache update instead of immediate save
                                        await self._batch_cache_set(
                                            cache_key, entity_data
                                        )
                                        cache_time = time.time() - save_start

                                        reporter_start = time.time()
                                        entity_reporter.save_metrics()
                                        reporter_time = time.time() - reporter_start

                                        total_save_time = time.time() - save_start

                                        if (
                                            total_save_time > 1.0
                                        ):  # Log slow saves (>1s)
                                            logger.warning(
                                                f"⚠️ Slow periodic save at {processed_count} messages: "
                                                f"cache {cache_time:.2f}s, reporter {reporter_time:.2f}s, "
                                                f"total {total_save_time:.2f}s"
                                            )
                                        else:
                                            logger.info(
                                                f"💾 Periodic save: {processed_count} messages processed for {entity_name}"
                                            )
                                    except Exception as save_error:
                                        logger.warning(
                                            f"Failed periodic save at message {processed_count}: {save_error}"
                                        )

                                # Update progress bar with processed count (time-based)
                                current_time = time.time()
                                if (
                                    current_time - self._last_progress_update
                                    >= self._progress_update_interval
                                ):
                                    # Update Rich progress bar
                                    progress.update(
                                        task_id_progress,
                                        completed=processed_count,  # ← For percentage calculation
                                        messages=processed_count,
                                        media=media_count,
                                    )
                                    self._last_progress_update = current_time

                                    # Also send update to OutputManager (TIER B - B-5)
                                    from src.ui.output_manager import ProgressUpdate
                                    output_mgr.show_progress(ProgressUpdate(
                                        entity_name=entity_name,
                                        messages_processed=processed_count,
                                        total_messages=None,
                                        stage="processing",
                                        percentage=None
                                    ))

                                    # Also update progress queue if provided
                                    if progress_queue:
                                        await progress_queue.put(
                                            {
                                                "task_id": task_id,
                                                "progress": processed_count,
                                                "total": None,
                                                "status": f"Processed {processed_count} messages",
                                            }
                                        )

                                # Clear batch for next iteration
                                batch.clear()

                            # Process remaining messages in batch (after async for loop)
                            if batch:
                                # Track API time for final batch
                                api_time = time.time() - batch_fetch_start
                                self.statistics.time_api_requests += api_time
                                self.statistics.api_request_count += 1
                                
                                # Track processing time for final batch
                                process_start = time.time()
                                
                                # 🚀 Use parallel processor for final batch (TIER B - B-3)
                                async def process_fn(msg):
                                    """Wrapper for _process_message_parallel"""
                                    return await self._process_message_parallel(
                                        msg, target, media_dir, output_dir, entity_reporter
                                    )
                                
                                messages_to_process = [msg for msg in batch if (msg.text or msg.media)]
                                
                                if messages_to_process:
                                    # 🚀 OPTIMIZATION: Pre-load sender names for final batch
                                    self._preload_batch_sender_names(messages_to_process)
                                    
                                    results = await self._parallel_media_processor.process_batch(
                                        messages_to_process, process_fn
                                    )
                                    
                                    process_time = time.time() - process_start
                                    self.statistics.time_processing += process_time
                                    
                                    # Track file I/O time for final batch
                                    io_start = time.time()

                                    for result in results:
                                        # Handle exceptions from gather
                                        if isinstance(result, Exception):
                                            logger.warning(f"Failed to process message: {result}")
                                            continue
                                        
                                        content, msg_id, has_media, media_cnt = result
                                        if not content:
                                            continue

                                        await f.write(content)

                                        processed_count += 1
                                        media_count += media_cnt
                                        self.statistics.messages_processed += 1
                                        self.statistics.media_downloaded += media_cnt

                                        entity_data.processed_message_ids.add(msg_id)
                                        entity_data.last_message_id = msg_id
                                        entity_reporter.record_message_processed(
                                            msg_id, has_media=has_media
                                        )
                                    
                                    io_time = time.time() - io_start
                                    self.statistics.time_file_io += io_time

                                    # Final update
                                    progress.update(
                                        task_id_progress,
                                        completed=processed_count,  # ← For percentage calculation
                                        messages=processed_count,
                                        media=media_count,
                                    )

            # End messages phase timing and record pipeline stats if not already set
            try:
                self.statistics.end_messages_phase()
            except Exception:
                logger.exception("Failed to record messages phase timing")

            # Update total messages count in file
            await self._update_message_count(chat_file, processed_count)
            self.statistics.notes_created = 1  # One file created

            logger.info(
                f"✅ Export completed: {processed_count} messages, {media_count} media files"
            )

            # Calculate periodic saves count
            periodic_saves_count = processed_count // 100

            # Save final entity state and monitoring data
            try:
                # Flush any remaining cache updates
                await self._flush_cache_batch()
                # Final cache save
                await self.cache_manager.set(cache_key, entity_data.to_dict())

                # Set total_messages and processed_messages in metrics before finishing
                entity_reporter.metrics.total_messages = processed_count
                entity_reporter.metrics.processed_messages = processed_count
                entity_reporter.metrics.total_media_files = media_count

                # Collect worker stats if available
                if hasattr(self.telegram_manager, "get_worker_stats"):
                    worker_stats = self.telegram_manager.get_worker_stats()
                    if worker_stats:
                        # Convert integer keys to strings for JSON serialization
                        entity_reporter.metrics.worker_stats = {
                            str(k): v for k, v in worker_stats.items()
                        }
                        logger.info(
                            f"📊 Collected stats from {len(worker_stats)} workers"
                        )
                
                # 🚀 Collect parallel media processing metrics (TIER B - B-3)
                parallel_metrics = self._parallel_media_processor.get_metrics()
                if parallel_metrics and parallel_metrics.total_media_processed > 0:
                    metrics_dict = {
                        "total_media_processed": parallel_metrics.total_media_processed,
                        "concurrent_peak": parallel_metrics.concurrent_peak,
                        "avg_concurrency": round(parallel_metrics.avg_concurrency, 2),
                        "memory_throttles": parallel_metrics.memory_throttles,
                    }
                    self.statistics.parallel_media_metrics = metrics_dict
                    entity_reporter.metrics.parallel_media_metrics = metrics_dict
                    logger.info(
                        f"🚀 Parallel media stats: {parallel_metrics.total_media_processed} media, "
                        f"peak concurrency: {parallel_metrics.concurrent_peak}, "
                        f"avg: {parallel_metrics.avg_concurrency:.2f}, "
                        f"throttles: {parallel_metrics.memory_throttles}"
                    )

                entity_reporter.finish_export()
                entity_reporter.save_report()

                # Update statistics with resource metrics
                self.statistics.avg_cpu_percent = (
                    entity_reporter.metrics.avg_cpu_percent
                )
                self.statistics.peak_memory_mb = entity_reporter.metrics.peak_memory_mb

                logger.info(f"Final save completed for {entity_name}")
                logger.info(f"  📊 Total messages: {processed_count}")
                logger.info(f"  🎬 Media files: {media_count}")
                logger.info(f"  💾 Periodic saves: {periodic_saves_count}")
                logger.info(f"  📂 Export location: {output_dir}")
                logger.info(
                    f"  📈 Monitoring saved to: {monitoring_dir}/monitoring_{target.id}.json"
                )
                logger.info(f"  💾 Cache key: {cache_key}")
                
                # Notify OutputManager of successful completion (TIER B - B-5)
                output_mgr.finish_export(entity_name, success=True)
                
            except Exception as save_error:
                logger.warning(
                    f"Failed to save cache/monitoring for {entity_name}: {save_error}"
                )

        except Exception as e:
            logger.error(f"Export failed for {target.name}: {e}")
            self.statistics.errors_encountered += 1

            # Notify OutputManager of failure (TIER B - B-5)
            try:
                output_mgr.finish_export(entity_name, success=False)
            except:
                pass  # OutputManager may not be initialized

            # Try to save cache/monitoring even on failure
            try:
                await self.cache_manager.set(cache_key, entity_data.to_dict())

                # Set metrics even on failure for emergency save
                entity_reporter.metrics.total_messages = processed_count
                entity_reporter.metrics.processed_messages = processed_count
                entity_reporter.metrics.total_media_files = media_count

                entity_reporter.finish_export()
                entity_reporter.save_report()

                logger.info(
                    f"Emergency save completed for {entity_name} after export failure"
                )
                logger.info(
                    f"  📊 Messages processed before failure: {processed_count}"
                )
                logger.info(f"  💾 Periodic saves completed: {periodic_saves_count}")
                logger.info(
                    f"  📈 Emergency monitoring saved to: {monitoring_dir}/monitoring_{target.id}.json"
                )
                logger.info(f"  💾 Cache key: {cache_key}")
            except Exception as save_error:
                logger.warning(
                    f"Failed to save cache/monitoring after export failure for {entity_name}: {save_error}"
                )

            raise

        return self.statistics.copy()

    async def _get_sender_name(self, message) -> str:
        """Get formatted sender name for message with caching and string interning.

        If the message's sender object is missing or doesn't contain a human-readable
        name, attempt to resolve the sender via `self.telegram_manager.resolve_entity`
        (cached) and extract the name from the resolved entity.
        """
        try:
            sender_id = message.sender_id
            if not sender_id:
                return self._intern_string("Unknown User")

            # Fast path: check cache first
            if sender_id in self._sender_name_cache:
                return self._sender_name_cache[sender_id]

            def _format_entity(entity) -> Optional[str]:
                if entity is None:
                    return None
                # User-like objects
                if hasattr(entity, "first_name"):
                    name_parts = []
                    if getattr(entity, "first_name", None):
                        name_parts.append(entity.first_name)
                    if getattr(entity, "last_name", None):
                        name_parts.append(entity.last_name)
                    if name_parts:
                        return " ".join(name_parts)
                    if getattr(entity, "username", None):
                        return f"@{entity.username}"
                    return None
                # Channel / Group
                if hasattr(entity, "title"):
                    return str(entity.title)
                # Fall back to username if present
                if getattr(entity, "username", None):
                    return f"@{entity.username}"
                return None

            # Prefer using the message.sender object if available
            if getattr(message, "sender", None):
                name = _format_entity(message.sender) or f"User {sender_id}"
                interned_name = self._intern_string(name)
                self._sender_name_cache[sender_id] = interned_name
                return interned_name

            # If sender object is missing, try to resolve from Telethon's entity cache ONLY
            # 🚀 OPTIMIZATION (TIER B-5): Avoid network calls for sender resolution
            # Calling resolve_entity() here triggers get_entity() API calls which causes
            # massive throttling/FloodWait in large chats.
            # We ONLY look in local cache. If missing -> "User 12345"
            try:
                # Try to get entity from Telethon's local cache without network call
                # client.get_entity() initiates network call if not found
                # client.get_input_entity() might also trigger network
                
                # Access the entity cache directly if possible or use minimal resolve
                # In Telethon, we can try to find the entity in the session
                resolved = None
                
                # Safe attempt to get from session cache only
                if self.telegram_manager.client:
                    try:
                        # Get entity only if cached (0 wait time implies cache check usually, but not guaranteed)
                        # Better approach: check input_entity cache in our manager first
                        if hasattr(self.telegram_manager, "_input_peer_cache"):
                             # This is our custom cache, stores InputPeers, not full entities
                             pass
                    except:
                        pass
                
                # If we really need the name and it's not in message.sender,
                # we accept "User ID" to avoid performance kill.
                # Just fallback immediately.
                
            except Exception:
                resolved = None

            # 🚀 STRICT PERFORMANCE MODE:
            # If we don't have the entity object already attached to the message,
            # we DO NOT fetch it. It's too expensive (1 API call per unique user).
            # We fallback to ID.
            
            # Use resolved if we managed to get it cheaply (we didn't try hard)
            name = _format_entity(resolved)
            if name:
                interned_name = self._intern_string(name)
                self._sender_name_cache[sender_id] = interned_name
                return interned_name

            # Final fallback - Just use ID
            # This is 1000x faster than network call
            interned_name = self._intern_string(f"User {sender_id}")
            self._sender_name_cache[sender_id] = interned_name
            return interned_name
        except Exception:
            return self._intern_string("Unknown User")
    
    def _preload_batch_sender_names(self, batch: List) -> None:
        """
        Pre-load sender names for all messages in batch to avoid repeated lookups.
        
        🚀 OPTIMIZATION: On a batch of 500 messages from 50 unique senders:
        - Before: 500 cache lookups + 450 duplicate _format_entity calls
        - After: 50 cache lookups + processing
        
        Args:
            batch: List of messages to preload senders for
        """
        # Collect unique sender_ids from this batch
        unique_senders = {}  # sender_id → message.sender
        
        for msg in batch:
            sender_id = getattr(msg, "sender_id", None)
            if not sender_id:
                continue
            
            # Skip if already in global cache
            if sender_id in self._sender_name_cache:
                continue
            
            # Store first occurrence of this sender in batch
            if sender_id not in unique_senders:
                unique_senders[sender_id] = getattr(msg, "sender", None)
        
        # Fast path: all senders already cached
        if not unique_senders:
            return
        
        # Pre-populate cache for unique senders in this batch
        def _format_entity(entity) -> Optional[str]:
            if entity is None:
                return None
            # User-like objects
            if hasattr(entity, "first_name"):
                name_parts = []
                if getattr(entity, "first_name", None):
                    name_parts.append(entity.first_name)
                if getattr(entity, "last_name", None):
                    name_parts.append(entity.last_name)
                if name_parts:
                    return " ".join(name_parts)
                if getattr(entity, "username", None):
                    return f"@{entity.username}"
                return None
            # Channel / Group
            if hasattr(entity, "title"):
                return str(entity.title)
            # Fall back to username if present
            if getattr(entity, "username", None):
                return f"@{entity.username}"
            return None
        
        for sender_id, sender_entity in unique_senders.items():
            if sender_entity:
                name = _format_entity(sender_entity) or f"User {sender_id}"
            else:
                name = f"User {sender_id}"
            
            interned_name = self._intern_string(name)
            self._sender_name_cache[sender_id] = interned_name

    def _format_timestamp(self, dt) -> str:
        """Format datetime in Telegram export format (optimized with interning).

        Convert message timestamps to UTC+3 before rendering to keep exported
        notes consistently in the desired timezone.
        """
        import datetime as _dt

        try:
            if dt is None:
                return self._intern_string("Unknown Date")

            # If naive, assume UTC (best-effort) then convert to UTC+3
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)

            tz = _dt.timezone(_dt.timedelta(hours=3))
            dt_local = dt.astimezone(tz)

            timestamp_str = (
                f"{dt_local.day:02d}.{dt_local.month:02d}.{dt_local.year} "
                f"{dt_local.hour:02d}:{dt_local.minute:02d}"
            )
        except Exception:
            # Fallback to safe formatting if anything goes wrong
            try:
                timestamp_str = (
                    f"{getattr(dt, 'day', 0):02d}."
                    f"{getattr(dt, 'month', 0):02d}."
                    f"{getattr(dt, 'year', 0)} "
                    f"{getattr(dt, 'hour', 0):02d}:{getattr(dt, 'minute', 0):02d}"
                )
            except Exception:
                timestamp_str = "00.00.0000 00:00"

        return self._intern_string(timestamp_str)

    def _get_current_datetime(self) -> str:
        """Get current datetime formatted in UTC+3."""
        import datetime as _dt

        tz = _dt.timezone(_dt.timedelta(hours=3))
        # Use UTC then convert to UTC+3 to avoid depending on system local tz
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        now_local = now_utc.astimezone(tz)
        return now_local.strftime("%d.%m.%Y %H:%M")

    def _get_media_type_name(self, media) -> str:
        """Get human-readable media type name."""
        media_type = type(media).__name__
        type_mapping = {
            "MessageMediaPhoto": "Photo",
            "MessageMediaDocument": "Document",
            "MessageMediaVideo": "Video",
            "MessageMediaAudio": "Audio",
            "MessageMediaVoice": "Voice Message",
            "MessageMediaContact": "Contact",
            "MessageMediaLocation": "Location",
            "MessageMediaPoll": "Poll",
            "MessageMediaSticker": "Sticker",
            "MessageMediaGif": "GIF",
        }
        return type_mapping.get(media_type, "Media")

    async def _update_message_count(self, file_path, count):
        """
        Update the total message count in the exported file.
        
        Security S-4: Uses atomic write (tmp + rename) to prevent corruption.
        """
        try:
            # Read the file asynchronously
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            # Replace the placeholder
            content = content.replace(
                "Total Messages: Processing...", f"Total Messages: {count}"
            )

            # S-4: Atomic write - write to .tmp then rename
            tmp_path = f"{file_path}.tmp"
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                await f.write(content)
            
            # Atomic rename
            await aiofiles.os.rename(tmp_path, file_path)
        except Exception as e:
            logger.warning(f"Failed to update message count: {e}")

        finally:
            self.statistics.end_time = time.time()

    # --- Forum Export Methods ---

    async def _export_forum(
        self, target: ExportTarget, progress_queue, task_id, progress_obj=None, overall_task_id=None
    ) -> ExportStatistics:
        logger.info(f"Starting forum export: {target.name}")
        entity = await self.telegram_manager.resolve_entity(target.id)
        entity_name = getattr(entity, "title", str(target.id))

        # Create structure: ForumName/topics/ and ForumName/media/
        forum_folder = self.config.export_path / sanitize_filename(
            entity_name, replacement="_"
        )
        logger.debug(
            f"Resolved forum_folder: {forum_folder} (entity_name={entity_name})"
        )
        topics_dir = forum_folder / "topics"
        media_dir = forum_folder / "media"
        monitoring_dir = forum_folder / ".monitoring"

        await asyncio.to_thread(topics_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(media_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(monitoring_dir.mkdir, parents=True, exist_ok=True)

        # Initialize reporter
        entity_reporter = self.reporter_manager.get_reporter(target.id, monitoring_dir)
        entity_reporter.start_export(entity_name, "forum", export_settings={})

        all_topics = await self.telegram_manager.get_forum_topics(entity)
        topics_to_export = []
        if target.type == "forum_topic":
            topics_to_export = [t for t in all_topics if t.topic_id == target.topic_id]
        else:
            topics_to_export = all_topics

        logger.info(
            f"Found {len(topics_to_export)} topics to export in forum {entity_name}"
        )

        if not topics_to_export:
            return self.statistics.copy()

        # Use provided progress object or create a new one
        use_existing_progress = progress_obj is not None
        
        async def _do_export(progress, main_task_id=None):
            """Helper function to perform export with given progress context."""
            # Dictionary to store task IDs for each topic
            topic_tasks = {}
            
            # Pre-fetch message counts for all topics and create progress tasks
            logger.info("📊 Fetching message counts for all topics...")
            
            # Calculate total message count across all topics
            total_forum_messages = 0
            for i, topic in enumerate(topics_to_export):
                topic_title = topic.title or f"Topic {topic.topic_id}"
                
                # Get message count for this topic
                try:
                    total_messages = topic.message_count or 0
                    total_forum_messages += total_messages
                    
                    logger.debug(
                        f"  Topic {i+1}/{len(topics_to_export)}: {topic_title} - {total_messages} messages"
                    )
                except Exception as e:
                    logger.warning(f"  Could not get count for topic {topic_title}: {e}")
            
            # Update the main task if provided, otherwise create overall bar
            if main_task_id is not None:
                # Update existing task with total
                progress.update(
                    main_task_id,
                    total=total_forum_messages if total_forum_messages > 0 else None,
                    current=0,
                    total_field=total_forum_messages,
                )
                forum_overall_task_id = main_task_id
            else:
                # Create our own overall forum progress bar
                forum_overall_task_id = progress.add_task(
                    f"[bold green]📊 Overall: {entity_name}",
                    total=total_forum_messages if total_forum_messages > 0 else None,
                    current=0,
                    total_field=total_forum_messages,
                )
            
            # Track overall progress
            overall_processed = 0
            
            # Now create individual topic progress bars
            for i, topic in enumerate(topics_to_export):
                topic_title = topic.title or f"Topic {topic.topic_id}"
                
                try:
                    total_messages = topic.message_count or 0
                    
                    # Create a progress task for this topic
                    task_id = progress.add_task(
                        f"  [cyan]{topic_title[:40]}...",
                        total=total_messages if total_messages > 0 else None,
                        current=0,
                        total_field=total_messages,
                    )
                    topic_tasks[topic.topic_id] = task_id
                    
                except Exception as e:
                    logger.warning(f"  Could not get count for topic {topic_title}: {e}")
                    # Create task with unknown total
                    task_id = progress.add_task(
                        f"  [cyan]{topic_title[:40]}...",
                        total=None,
                        current=0,
                        total_field=0,
                    )
                    topic_tasks[topic.topic_id] = task_id

            # Export each topic with progress tracking
            for i, topic in enumerate(topics_to_export):
                topic_title = topic.title or f"Topic {topic.topic_id}"
                logger.info(
                    f"Exporting topic {i + 1}/{len(topics_to_export)}: {topic_title}"
                )

                safe_title = sanitize_filename(topic_title) or f"topic_{topic.topic_id}"
                topic_file = topics_dir / f"{safe_title}.md"

                topic_processed_count = 0
                topic_task_id = topic_tasks.get(topic.topic_id)

                try:
                    async with AsyncBufferedSaver(topic_file, "w", encoding="utf-8") as f:
                        await f.write(f"# Topic: {topic_title}\n")
                        await f.write(f"ID: {topic.topic_id}\n\n")

                        # ⚡ PREFETCH OPTIMIZATION: Use producer-consumer pipeline if enabled
                        if self.config.enable_prefetch_batches:
                            # Pass overall_processed by reference so prefetch can update it
                            overall_processed_ref = [overall_processed]
                            
                            # Use prefetch for this topic
                            topic_processed, topic_media = await self._export_forum_topic_with_prefetch(
                                entity=entity,
                                topic=topic,
                                target=target,
                                media_dir=media_dir,
                                topics_dir=topics_dir,
                                entity_reporter=entity_reporter,
                                f=f,
                                progress=progress,
                                forum_overall_task_id=forum_overall_task_id,
                                topic_task_id=topic_task_id,
                                batch_size=self.config.prefetch_batch_size or 75,
                                overall_processed_ref=overall_processed_ref,
                            )
                            
                            # Update local counter from reference
                            overall_processed = overall_processed_ref[0]
                            topic_processed_count = topic_processed
                            
                        else:
                            # Fallback: non-prefetch batch processing
                            batch = []
                            batch_size = 75  # Increased from 50 for better throughput
                            
                            # 🔧 HOTPATH FIX: Track API time correctly (between batches, not cumulative)
                            batch_fetch_start = time.time()
                            messages_fetched = 0

                            async for (
                                message
                            ) in self.telegram_manager.get_topic_messages_stream(
                                entity, topic.topic_id
                            ):
                                messages_fetched += 1
                                batch.append(message)

                                if len(batch) >= batch_size:
                                    # Track API time for this batch (time since last batch was processed)
                                    api_time = time.time() - batch_fetch_start
                                    self.statistics.time_api_requests += api_time
                                    self.statistics.api_request_count += 1
                                    
                                    # Track processing time
                                    process_start = time.time()
                                    
                                    # 🚀 OPTIMIZATION: Pre-load sender names for batch
                                    # This eliminates repeated lookups for same senders within batch
                                    self._preload_batch_sender_names(batch)
                                    
                                    # Process batch
                                    tasks = [
                                        self._process_message_parallel(
                                            msg, target, media_dir, topics_dir, entity_reporter
                                        )
                                        for msg in batch
                                        if (msg.text or msg.media)
                                    ]

                                    if tasks:
                                        results = await asyncio.gather(*tasks)
                                        
                                        process_time = time.time() - process_start
                                        self.statistics.time_processing += process_time
                                        
                                        # Track file I/O time
                                        io_start = time.time()
                                        
                                        batch_processed = 0
                                        for content, msg_id, has_media, media_cnt in results:
                                            if content:
                                                await f.write(content)
                                                topic_processed_count += 1
                                                batch_processed += 1
                                                self.statistics.messages_processed += 1
                                                self.statistics.media_downloaded += media_cnt
                                                entity_reporter.record_message_processed(
                                                    msg_id, has_media=has_media
                                                )
                                        
                                        io_time = time.time() - io_start
                                        self.statistics.time_file_io += io_time
                                        
                                        # Update overall progress
                                        overall_processed += batch_processed
                                        progress.update(
                                            forum_overall_task_id,
                                            completed=overall_processed,
                                            current=overall_processed,
                                        )
                                        
                                        # Update progress for this topic
                                        if topic_task_id is not None:
                                            progress.update(
                                                topic_task_id,
                                                completed=topic_processed_count,
                                                current=topic_processed_count,
                                            )

                                    batch.clear()
                                    # 🔧 HOTPATH FIX: Reset timer for next batch
                                    batch_fetch_start = time.time()

                            # Process remaining batch (non-prefetch only)
                            if batch:
                                # Track final API time
                                if messages_fetched > 0:
                                    api_time = time.time() - batch_fetch_start
                                    self.statistics.time_api_requests += api_time
                                    if batch:  # Only count if there were messages
                                        self.statistics.api_request_count += 1
                                
                                process_start = time.time()
                                
                                # 🚀 OPTIMIZATION: Pre-load sender names for remaining batch
                                self._preload_batch_sender_names(batch)
                                
                                tasks = [
                                    self._process_message_parallel(
                                        msg, target, media_dir, topics_dir, entity_reporter
                                    )
                                    for msg in batch
                                    if (msg.text or msg.media)
                                ]
                                if tasks:
                                    results = await asyncio.gather(*tasks)
                                    
                                    process_time = time.time() - process_start
                                    self.statistics.time_processing += process_time
                                    
                                    io_start = time.time()
                                    
                                    remaining_processed = 0
                                    for content, msg_id, has_media, media_cnt in results:
                                        if content:
                                            await f.write(content)
                                            topic_processed_count += 1
                                            remaining_processed += 1
                                            self.statistics.messages_processed += 1
                                            self.statistics.media_downloaded += media_cnt
                                            entity_reporter.record_message_processed(
                                                msg_id, has_media=has_media
                                            )
                                    
                                    io_time = time.time() - io_start
                                    self.statistics.time_file_io += io_time
                                    
                                    # Update overall progress
                                    overall_processed += remaining_processed
                                    progress.update(
                                        forum_overall_task_id,
                                        completed=overall_processed,
                                        current=overall_processed,
                                    )
                        
                        # Final progress update for this topic
                        if topic_task_id is not None:
                            progress.update(
                                topic_task_id,
                                completed=topic_processed_count,
                                current=topic_processed_count,
                            )

                    logger.info(
                        f"  ✅ Finished topic {topic_title}: {topic_processed_count} messages"
                    )

                except Exception as e:
                    logger.error(f"  ❌ Failed to export topic {topic_title}: {e}")
                    self.statistics.errors_encountered += 1
                    # Mark task as failed
                    if topic_task_id is not None:
                        progress.update(
                            topic_task_id,
                            description=f"[red]❌ {topic_title[:40]}...",
                        )
        
        # Call the export function with the appropriate progress context
        if use_existing_progress:
            # Use the provided progress object
            await _do_export(progress_obj, overall_task_id)
        else:
            # Create our own progress context
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("•"),
                TextColumn("[cyan]{task.fields[current]}/{task.fields[total_field]} msgs"),
                TimeRemainingColumn(),
                transient=False,
            ) as progress:
                await _do_export(progress, None)

        # Finish reporting
        entity_reporter.metrics.total_messages = self.statistics.messages_processed
        entity_reporter.finish_export()
        entity_reporter.save_report()

        return self.statistics.copy()

    async def export_all(
        self, targets: List[ExportTarget], progress_queue=None
    ) -> List[ExportStatistics]:
        """
        Export multiple targets sequentially.

        Args:
            targets: List of ExportTarget objects
            progress_queue: Optional progress reporting queue

        Returns:
            List of ExportStatistics for each target
        """
        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("[cyan]{task.fields[current]}/{task.fields[total_field]} msgs"),
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            task_id_progress = progress.add_task(
                "[cyan]Exporting targets...", 
                total=len(targets),
                current=0,
                total_field=0,
            )

            for i, target in enumerate(targets):
                if self._shutdown_requested:
                    logger.info("Shutdown requested, stopping export")
                    break

                progress.update(
                    task_id_progress,
                    description=f"[cyan]Exporting {i + 1}/{len(targets)}: {target.name}",
                )
                logger.info(f"Exporting target {i + 1}/{len(targets)}: {target.name}")

                try:
                    # Pass progress context to forum exports
                    if target.type in ["forum", "forum_chat", "forum_topic"]:
                        # Reset statistics
                        self.statistics = ExportStatistics()
                        
                        # Clear caches
                        self._sender_name_cache.clear()
                        self._prefetch_stats = {"hits": 0, "misses": 0}
                        if self._prefetch_task and not self._prefetch_task.done():
                            self._prefetch_task.cancel()
                        self._prefetch_task = None
                        self._prefetch_result = None
                        
                        # Call _export_forum directly with progress context
                        stats = await asyncio.wait_for(
                            self._export_forum(target, progress_queue, f"target_{i}", progress, task_id_progress),
                            timeout=EXPORT_OPERATION_TIMEOUT,
                        )
                    else:
                        stats = await self.export_target(
                            target, progress_queue, f"target_{i}"
                        )
                    
                    results.append(stats)

                    logger.info(f"✅ Target {target.name} exported successfully")
                    logger.info(f"   Messages: {stats.messages_processed}")
                    logger.info(f"   Media: {stats.media_downloaded}")
                    logger.info(f"   Duration: {stats.duration:.1f}s")

                except Exception as e:
                    logger.error(f"❌ Failed to export target {target.name}: {e}")
                    results.append(ExportStatistics())

                progress.advance(task_id_progress)

        # 🚀 Wait for background downloads to complete
        if getattr(self.config, "async_media_download", True):
            logger.info("⏳ Waiting for background media downloads...")
            await self.media_processor.wait_for_downloads(timeout=3600)  # 1 hour max

        # 🚀 Run Deferred Media Processing
        if self.config.deferred_processing:
            logger.info("⏳ Starting deferred media processing...")
            await self.media_processor.process_pending_tasks()

        return results

    async def _save_progress_on_shutdown(self, entity_data: EntityCacheData, cache_key: str) -> None:
        """
        Save current progress state on graceful shutdown (TIER A - Task 3).
        
        This ensures resume capability even if export is interrupted.
        
        Args:
            entity_data: Current entity state with processed messages
            cache_key: Cache key for storing state
        """
        try:
            logger.info(f"💾 Saving progress state on shutdown: {entity_data.entity_name}")
            
            # Save to cache (serialize EntityCacheData to dict)
            await self.cache_manager.set(cache_key, entity_data.to_dict())
            
            # Also save metrics/stats if reporter available
            if hasattr(self, '_current_reporter') and self._current_reporter:
                self._current_reporter.save_metrics()
                
            logger.info(f"✅ Progress saved: {entity_data.processed_messages} messages processed")
            
        except Exception as e:
            logger.error(f"❌ Failed to save progress on shutdown: {e}", exc_info=True)

    async def _process_batch_with_stats(
        self,
        batch: List,
        target,
        media_dir,
        output_dir,
        entity_reporter,
        entity_data,
        f,
        progress,
        task_id_progress,
        output_mgr,
        progress_queue,
        task_id,
        entity_name: str,
        cache_key: str,
        processed_count_ref: List[int],
        media_count_ref: List[int],
    ):
        """
        Process a single batch of messages with full stats tracking.
        
        This helper consolidates batch processing logic for both prefetch and non-prefetch paths.
        Uses reference lists for counters to allow mutation across async calls.
        """
        from src.shutdown_manager import shutdown_manager
        
        # Track processing time
        process_start = time.time()
        
        # Filter and process messages
        messages_to_process = [msg for msg in batch if (msg.text or msg.media)]
        
        if not messages_to_process:
            return
        
        # Pre-load sender names for batch
        self._preload_batch_sender_names(messages_to_process)
        
        # Process batch
        async def process_fn(msg):
            return await self._process_message_parallel(
                msg, target, media_dir, output_dir, entity_reporter
            )
        
        results = await self._parallel_media_processor.process_batch(
            messages_to_process, process_fn
        )
        
        process_time = time.time() - process_start
        self.statistics.time_processing += process_time
        
        # Write results
        io_start = time.time()
        
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Failed to process message: {result}")
                continue
            
            content, msg_id, has_media, media_cnt = result
            if not content:
                continue
            
            await f.write(content)
            
            # Update stats
            processed_count_ref[0] += 1
            media_count_ref[0] += media_cnt
            self.statistics.messages_processed += 1
            self.statistics.media_downloaded += media_cnt
            
            # Update entity data
            entity_data.processed_message_ids.add(msg_id)
            entity_data.last_message_id = msg_id
            entity_reporter.record_message_processed(msg_id, has_media=has_media)
        
        io_time = time.time() - io_start
        self.statistics.time_file_io += io_time
        
        # Check shutdown after processing
        if shutdown_manager.shutdown_requested:
            logger.info("🛑 Shutdown after batch processing")
            await f.flush()
            return "shutdown"
        
        # Periodic save
        processed_count = processed_count_ref[0]
        if processed_count % 100 == 0:
            try:
                save_start = time.time()
                await self._batch_cache_set(cache_key, entity_data)
                entity_reporter.save_metrics()
                save_time = time.time() - save_start
                
                if save_time > 1.0:
                    logger.warning(f"⚠️ Slow save at {processed_count} msgs: {save_time:.2f}s")
                else:
                    logger.info(f"💾 Periodic save: {processed_count} messages")
            except Exception as e:
                logger.warning(f"Failed periodic save: {e}")
        
        # Update progress
        current_time = time.time()
        if current_time - self._last_progress_update >= self._progress_update_interval:
            media_count = media_count_ref[0]
            progress.update(
                task_id_progress,
                completed=processed_count,  # ← For percentage calculation
                messages=processed_count,
                media=media_count,
            )
            self._last_progress_update = current_time
            
            from src.ui.output_manager import ProgressUpdate
            output_mgr.show_progress(ProgressUpdate(
                entity_name=entity_name,
                messages_processed=processed_count,
                total_messages=None,
                stage="processing",
                percentage=None
            ))
            
            if progress_queue:
                await progress_queue.put({
                    "task_id": task_id,
                    "progress": processed_count,
                    "total": None,
                    "status": f"Processed {processed_count} messages",
                })

    async def _export_with_prefetch(
        self,
        entity,
        resume_from_id: int,
        processed_ids,
        batch_size: int,
        target,
        media_dir,
        output_dir,
        entity_reporter,
        entity_data,
        f,
        progress,
        task_id_progress,
        output_mgr,
        progress_queue,
        task_id,
        entity_name: str,
        cache_key: str,
    ) -> tuple:
        """
        Export messages using prefetch optimization (producer-consumer pipeline).
        
        Returns:
            Tuple of (processed_count, media_count)
        """
        from src.shutdown_manager import shutdown_manager
        from .prefetch_processor import PrefetchBatchProcessor
        
        logger.info(
            f"⚡ Prefetch enabled: queue_size={self.config.prefetch_queue_size}, "
            f"batch_size={batch_size}"
        )
        
        # Create prefetch processor
        prefetch = PrefetchBatchProcessor(
            batch_size=batch_size,
            queue_size=self.config.prefetch_queue_size,
        )
        
        # Define skip condition for already-processed messages
        def should_skip(msg):
            return processed_ids and msg.id in processed_ids
        
        # Start producer (background fetch task)
        await prefetch.start_producer(
            self.telegram_manager.fetch_messages(
                entity,
                limit=None,
                min_id=resume_from_id,
            ),
            skip_condition=should_skip if processed_ids else None,
        )
        
        # Counters (using lists as mutable references)
        processed_count_ref = [0]
        media_count_ref = [0]
        
        # Consumer loop: process prefetched batches
        batch_fetch_start = time.time()
        
        try:
            while True:
                # Check for shutdown
                if shutdown_manager.shutdown_requested:
                    logger.info("🛑 Graceful shutdown requested")
                    await prefetch.stop()
                    break
                
                # Get next prefetched batch
                batch = await prefetch.get_next_batch()
                
                # None = producer finished
                if batch is None:
                    logger.info("✅ All batches processed")
                    break
                
                # Track API time (time spent fetching this batch)
                api_time = time.time() - batch_fetch_start
                self.statistics.time_api_requests += api_time
                self.statistics.api_request_count += 1
                
                # Process batch
                result = await self._process_batch_with_stats(
                    batch=batch,
                    target=target,
                    media_dir=media_dir,
                    output_dir=output_dir,
                    entity_reporter=entity_reporter,
                    entity_data=entity_data,
                    f=f,
                    progress=progress,
                    task_id_progress=task_id_progress,
                    output_mgr=output_mgr,
                    progress_queue=progress_queue,
                    task_id=task_id,
                    entity_name=entity_name,
                    cache_key=cache_key,
                    processed_count_ref=processed_count_ref,
                    media_count_ref=media_count_ref,
                )
                
                # Check if shutdown was requested during processing
                if result == "shutdown":
                    await prefetch.stop()
                    break
                
                # Reset timer for next batch
                batch_fetch_start = time.time()
        
        finally:
            # Clean up prefetch
            await prefetch.stop()
            
            logger.info(
                f"📊 Prefetch stats: "
                f"utilization={prefetch.metrics.get_queue_utilization():.1%}, "
                f"efficiency={prefetch.metrics.get_efficiency():.1%}"
            )
        
        return processed_count_ref[0], media_count_ref[0]

    async def _export_forum_topic_with_prefetch(
        self,
        entity,
        topic,
        target,
        media_dir,
        topics_dir,
        entity_reporter,
        f,
        progress,
        forum_overall_task_id,
        topic_task_id,
        batch_size: int,
        overall_processed_ref: list,  # Pass by reference [current_count]
    ) -> tuple:
        """
        Export single forum topic using prefetch optimization.
        
        Returns:
            Tuple of (processed_count, media_count, overall_progress_increment)
        """
        from src.shutdown_manager import shutdown_manager
        from .prefetch_processor import PrefetchBatchProcessor
        
        topic_title = topic.title or f"Topic {topic.topic_id}"
        
        logger.info(
            f"⚡ Prefetch enabled for topic '{topic_title}': "
            f"queue_size={self.config.prefetch_queue_size}, batch_size={batch_size}"
        )
        
        # Create prefetch processor
        prefetch = PrefetchBatchProcessor(
            batch_size=batch_size,
            queue_size=self.config.prefetch_queue_size,
        )
        
        # Start producer (background fetch task for this topic)
        await prefetch.start_producer(
            self.telegram_manager.get_topic_messages_stream(
                entity,
                topic.topic_id,  # Forum-specific: filter by topic ID
            ),
            skip_condition=None,  # No skip for forum (topics are usually full exports)
        )
        
        # Counters
        topic_processed_count = 0
        topic_media_count = 0
        batch_fetch_start = time.time()
        
        try:
            while True:
                # Check for shutdown
                if shutdown_manager.shutdown_requested:
                    logger.info(f"🛑 Graceful shutdown requested for topic '{topic_title}'")
                    await prefetch.stop()
                    break
                
                # Get next prefetched batch
                batch = await prefetch.get_next_batch()
                
                # None = producer finished
                if batch is None:
                    logger.debug(f"✅ All batches processed for topic '{topic_title}'")
                    break
                
                # Track API time
                api_time = time.time() - batch_fetch_start
                self.statistics.time_api_requests += api_time
                self.statistics.api_request_count += 1
                
                # Process batch using shared helper
                # Note: We use a simple counter approach for forum (no entity_data)
                processed_before = topic_processed_count
                media_before = topic_media_count
                
                # Process with ParallelMediaProcessor (similar to old gather approach)
                process_start = time.time()
                
                # Pre-load sender names
                messages_to_process = [msg for msg in batch if (msg.text or msg.media)]
                if not messages_to_process:
                    batch_fetch_start = time.time()
                    continue
                
                self._preload_batch_sender_names(messages_to_process)
                
                # Process batch
                async def process_fn(msg):
                    return await self._process_message_parallel(
                        msg, target, media_dir, topics_dir, entity_reporter
                    )
                
                results = await self._parallel_media_processor.process_batch(
                    messages_to_process, process_fn
                )
                
                process_time = time.time() - process_start
                self.statistics.time_processing += process_time
                
                # Write results
                io_start = time.time()
                batch_processed = 0
                batch_media = 0
                
                for result in results:
                    if isinstance(result, Exception):
                        logger.warning(f"Failed to process message in topic '{topic_title}': {result}")
                        continue
                    
                    content, msg_id, has_media, media_cnt = result
                    if not content:
                        continue
                    
                    await f.write(content)
                    batch_processed += 1
                    batch_media += media_cnt
                    self.statistics.messages_processed += 1
                    self.statistics.media_downloaded += media_cnt
                    entity_reporter.record_message_processed(msg_id, has_media=has_media)
                
                io_time = time.time() - io_start
                self.statistics.time_file_io += io_time
                
                # Update counters
                topic_processed_count += batch_processed
                topic_media_count += batch_media
                
                # Update overall progress counter
                overall_processed_ref[0] += batch_processed
                
                # Update progress bars
                if forum_overall_task_id is not None:
                    progress.update(
                        forum_overall_task_id,
                        completed=overall_processed_ref[0],
                        current=overall_processed_ref[0],
                    )
                
                if topic_task_id is not None:
                    progress.update(
                        topic_task_id,
                        completed=topic_processed_count,
                        current=topic_processed_count,
                    )
                
                # Check shutdown after processing
                if shutdown_manager.shutdown_requested:
                    await prefetch.stop()
                    break
                
                # Reset timer for next batch
                batch_fetch_start = time.time()
        
        finally:
            # Clean up prefetch
            await prefetch.stop()
            
            logger.debug(
                f"📊 Prefetch stats for '{topic_title}': "
                f"utilization={prefetch.metrics.get_queue_utilization():.1%}, "
                f"efficiency={prefetch.metrics.get_efficiency():.1%}"
            )
        
        return topic_processed_count, topic_media_count

    async def shutdown(self):
        """Gracefully shutdown the exporter."""
        self._shutdown_requested = True
        logger.info("Exporter shutdown initiated")


async def run_export(
    config: Config,
    telegram_manager: TelegramManager,
    cache_manager,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession,
    progress_queue=None,
    connection_manager=None,
    performance_monitor=None,
) -> List[ExportStatistics]:
    """
    High-level export orchestration function.
    Replaces the main export logic from main.py run_export function.
    """
    # 🚀 TIER C-4: Initialize metrics collection and resource monitoring
    from ..monitoring import get_metrics_collector, ResourceMonitor
    from ..monitoring.metrics_formatter import log_metrics_summary
    import json
    
    metrics = get_metrics_collector()
    resource_monitor = ResourceMonitor(interval_seconds=5.0)
    
    exporter = Exporter(
        config=config,
        telegram_manager=telegram_manager,
        cache_manager=cache_manager,
        media_processor=media_processor,
        note_generator=note_generator,
        http_session=http_session,
        performance_monitor=performance_monitor,
    )

    try:
        # 📊 Start resource monitoring (TIER C-4)
        await resource_monitor.start()
        logger.info("✅ TIER C-4: Resource monitoring started")
        # Initialize all components
        await exporter.initialize()

        # Takeout API Integration
        if config.use_takeout:
            # 1. Check if we are already in a Takeout session (Reuse Strategy)
            current_client = telegram_manager.client
            existing_id = getattr(
                current_client,
                "takeout_id",
                getattr(current_client, "_takeout_id", None),
            )

            if existing_id:
                logger.info(
                    f"♻️ Client is already in Takeout mode (ID: {existing_id}). Skipping initialization."
                )
                telegram_manager._external_takeout_id = existing_id

                # Disable rate limiting
                original_delay = config.request_delay
                config.request_delay = 0.0

                try:
                    return await exporter.export_all(
                        config.export_targets, progress_queue
                    )
                finally:
                    config.request_delay = original_delay
                    # Do not clear _external_takeout_id here as we didn't create the session
                    logger.info("🔄 Finished export using existing Takeout session")

            # 2. If not reusing, try to init new session
            try:
                logger.info("🚀 Attempting to initiate Telegram Takeout session...")
                logger.info(
                    "⚠️  Please check your Telegram messages (Service Notifications) to ALLOW the request."
                )

                # 🧹 Force-clear stale state blindly
                # The error "Can't send a takeout request while another takeout..." is a client-side check
                # We force clear it to ensure we can start a new one if the previous one wasn't closed properly
                try:
                    telegram_manager.client._takeout_id = None
                except Exception:
                    pass

                # Use Manual Wrapper instead of client.takeout()
                async with TakeoutSessionWrapper(
                    telegram_manager.client, config
                ) as takeout_client:
                    logger.info(
                        "✅ Takeout session established! Switching to Turbo Mode."
                    )

                    # ⚡ HACK: Temporarily swap the client in the manager
                    original_client = telegram_manager.client
                    
                    # IMPORTANT: Update _original_client to point to the real client
                    # before we replace self.client with TakeoutSessionWrapper
                    if not hasattr(telegram_manager, '_original_client') or telegram_manager._original_client is None:
                        telegram_manager._original_client = original_client
                    
                    telegram_manager.client = takeout_client

                    # Pass the ID to the manager so shards can reuse it
                    takeout_id = takeout_client.takeout_id

                    # Force set the attribute on the manager
                    setattr(telegram_manager, "_external_takeout_id", takeout_id)

                    if takeout_id:
                        logger.info(
                            f"♻️ Shared Takeout ID {takeout_id} with ShardedManager"
                        )
                    else:
                        logger.warning(
                            "⚠️ Could not extract Takeout ID from client! Sharding might fail."
                        )

                    # Disable rate limiting for Takeout
                    original_delay = config.request_delay
                    config.request_delay = 0.0

                    try:
                        # Export all configured targets using Takeout
                        results = await exporter.export_all(
                            config.export_targets, progress_queue
                        )
                    finally:
                        # Restore original client and settings
                        telegram_manager.client = original_client
                        telegram_manager._original_client = original_client  # Restore _original_client too
                        if hasattr(telegram_manager, "_external_takeout_id"):
                            telegram_manager._external_takeout_id = None

                        config.request_delay = original_delay
                        logger.info("🔄 Restored standard client connection")

                    return results

            except errors.TakeoutInitDelayError:
                logger.warning(
                    "⚠️  Takeout request needs confirmation. Please allow it in Telegram."
                )
                logger.warning(
                    "   (Telegram requires a delay after approval before Takeout becomes active)"
                )
                logger.info("ℹ️  Falling back to Standard API with rate limiting.")

            except Exception as e:
                if "another takeout" in str(e):
                    logger.warning(
                        "⚠️  Detected stale Takeout session state even after force-clear."
                    )
                    # At this point, we can't do much else than fall back

                logger.warning(
                    f"⚠️  Takeout session failed ({e}). Falling back to Standard API."
                )

        # Fallback or Standard mode
        logger.info(
            f"ℹ️  Using Standard API with rate limit delay: {config.takeout_fallback_delay}s"
        )
        config.request_delay = config.takeout_fallback_delay
        results = await exporter.export_all(config.export_targets, progress_queue)
        return results

    finally:
        # 📊 TIER C-4: Stop resource monitoring and export metrics
        try:
            await resource_monitor.stop()
            logger.info("✅ TIER C-4: Resource monitoring stopped")
            
            # Export metrics to JSON file
            metrics_data = metrics.export_json()
            metrics_path = os.path.join(config.export_path, "export_metrics.json")
            
            try:
                with open(metrics_path, 'w', encoding='utf-8') as f:
                    json.dump(metrics_data, f, indent=2, ensure_ascii=False)
                logger.info(f"📊 Metrics exported to: {metrics_path}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to export metrics JSON: {e}")
            
            # Log human-readable metrics summary
            try:
                log_metrics_summary(metrics_data)
            except Exception as e:
                logger.warning(f"⚠️ Failed to log metrics summary: {e}")
                
        except Exception as e:
            logger.warning(f"⚠️ Error during metrics finalization: {e}")
        
        # Ensure cleanup happens
        await exporter.shutdown()


def print_export_summary(results: List[ExportStatistics]):
    """Print summary of export results."""
    if not results:
        rprint("[yellow]No export results to display[/yellow]")
        return

    total_messages = sum(r.messages_processed for r in results)
    total_media = sum(r.media_downloaded for r in results)
    total_errors = sum(r.errors_encountered for r in results)
    total_duration = sum(r.duration for r in results)

    rprint("\n[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint("[bold green]          EXPORT SUMMARY[/bold green]")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint(f"[cyan]Total Messages:[/cyan] {total_messages}")
    rprint(f"[cyan]Total Media Files:[/cyan] {total_media}")
    rprint(f"[cyan]Errors:[/cyan] {total_errors}")
    rprint(f"[cyan]Total Duration:[/cyan] {total_duration:.1f}s")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]\n")
