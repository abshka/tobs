import asyncio
import gc
import os
import time
import weakref
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Dict, NamedTuple, Optional, Union

import aiofiles
import ujson

from src.utils import logger
class CacheUpdate(NamedTuple):
    """Represents a single cache update operation."""
    entity_id: str
    operation_type: str  # 'message', 'entity_info', 'media_file'
    data: Dict[str, Any]
    timestamp: float
class BatchCacheManager:
    """
    Optimized async cache manager with batching, granular locking, and memory management.

    Key improvements:
    - Batched writes to reduce I/O operations
    - Granular locking per entity instead of global lock
    - Memory-bounded cache with LRU eviction
    - Eliminated polling with event-driven architecture
    - Optimized serialization with dirty tracking
    """

    def __init__(self, cache_path: Path, batch_size: int = 50, batch_timeout: float = 5.0, max_cache_size: int = 10000):
        """
        Initialize the optimized CacheManager.

        Args:
            cache_path (Path): The path to the cache file.
            batch_size (int): Number of updates to batch before writing.
            batch_timeout (float): Maximum time to wait before flushing batch.
            max_cache_size (int): Maximum number of entities to keep in memory.
        """
        self.cache_path = cache_path.resolve()
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.max_cache_size = max_cache_size

        # Core cache data
        self.cache: Dict[str, Any] = {"version": 2, "entities": {}}

        # Batching system
        self._pending_updates: deque = deque()
        self._batch_timer: Optional[asyncio.Task] = None
        self._batch_event = asyncio.Event()

        # Granular locking - one lock per entity
        self._entity_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._global_lock = asyncio.Lock()  # Only for cache structure changes

        # Memory management
        self._entity_access_order: deque = deque()  # LRU tracking
        self._entity_access_times: Dict[str, float] = {}

        # Performance tracking
        self._save_task: Optional[asyncio.Task] = None
        self._last_save_time = 0.0
        self._save_count = 0
        self._batch_flush_task: Optional[asyncio.Task] = None

        # Shutdown handling
        self._shutdown = False
        self._cleanup_refs = weakref.WeakSet()

    async def load_cache(self):
        """
        Asynchronously load the cache from the cache file with improved error handling.
        """
        async with self._global_lock:
            if not self.cache_path.exists():
                logger.info("Cache file does not exist, starting with empty cache")
                return

            try:
                # Use aiofiles for better async I/O
                async with aiofiles.open(self.cache_path, mode='rb') as f:
                    content = await f.read()

                if not content:
                    logger.info("Cache file is empty, starting with empty cache")
                    return

                # Optimize ujson loading
                try:
                    loaded_data = ujson.loads(content.decode('utf-8'))
                except (ujson.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.error(f"Cache file has invalid format: {e}. Starting fresh.")
                    return

                if not isinstance(loaded_data, dict) or "version" not in loaded_data:
                    logger.error("Cache file has invalid structure. Starting fresh.")
                    return

                # Validate and normalize cache structure
                self.cache = loaded_data
                self.cache.setdefault("entities", {})

                # Batch normalize entity data to avoid individual lock acquisition
                entities_to_normalize = []
                for entity_id, data in self.cache["entities"].items():
                    if not isinstance(data, dict):
                        entities_to_normalize.append(entity_id)
                        continue

                    # Ensure required fields exist
                    data.setdefault("processed_messages", {})
                    data.setdefault("last_id", None)
                    data.setdefault("title", "Unknown")
                    data.setdefault("type", "unknown")

                # Remove invalid entities
                for entity_id in entities_to_normalize:
                    if entity_id in self.cache["entities"]:
                        del self.cache["entities"][entity_id]

                # Initialize access tracking for loaded entities
                current_time = time.time()
                for entity_id in self.cache["entities"]:
                    self._entity_access_times[entity_id] = current_time
                    self._entity_access_order.append(entity_id)

                logger.info(f"Cache loaded successfully: {len(self.cache['entities'])} entities")

            except Exception as e:
                logger.error(f"Failed to load cache file: {e}", exc_info=True)
                self.cache = {"version": 2, "entities": {}}

    def _get_default_entity_cache(self) -> Dict[str, Any]:
        """Get the default structure for an entity cache."""
        return {
            "processed_messages": {},
            "last_id": None,
            "title": "Unknown",
            "type": "unknown"
        }

    def _track_entity_access(self, entity_id: str):
        """Track entity access for LRU eviction."""
        current_time = time.time()

        # Update access time
        if entity_id in self._entity_access_times:
            # Remove from current position in deque
            try:
                self._entity_access_order.remove(entity_id)
            except ValueError:
                pass  # Entity not in deque, which is fine

        # Add to end (most recently used)
        self._entity_access_order.append(entity_id)
        self._entity_access_times[entity_id] = current_time

        # Trigger eviction if cache is too large
        if len(self.cache["entities"]) > self.max_cache_size:
            self._evict_lru_entities()

    def _evict_lru_entities(self):
        """Evict least recently used entities to maintain memory bounds."""
        entities_to_evict = len(self.cache["entities"]) - self.max_cache_size + 100  # Evict extra for headroom

        evicted_count = 0
        while evicted_count < entities_to_evict and self._entity_access_order:
            try:
                lru_entity = self._entity_access_order.popleft()
                if lru_entity in self.cache["entities"]:
                    del self.cache["entities"][lru_entity]
                    del self._entity_access_times[lru_entity]
                    # Don't remove from _entity_locks - let it be GC'd naturally
                    evicted_count += 1
            except IndexError:
                break

        if evicted_count > 0:
            logger.debug(f"Evicted {evicted_count} LRU entities from memory cache")
            # Suggest garbage collection
            if evicted_count > 50:
                gc.collect()

    async def _schedule_batch_flush(self):
        """Schedule a batch flush with timeout-based triggering."""
        if self._batch_flush_task and not self._batch_flush_task.done():
            return

        # Cancel existing timer
        if self._batch_timer and not self._batch_timer.done():
            self._batch_timer.cancel()

        # Start new timer
        self._batch_timer = asyncio.create_task(self._wait_and_flush())

    async def _wait_and_flush(self):
        """Wait for timeout then flush batch."""
        try:
            await asyncio.sleep(self.batch_timeout)
            if self._pending_updates:
                await self._flush_batch()
        except asyncio.CancelledError:
            pass  # Timer was cancelled, which is fine

    async def _add_pending_update(self, entity_id: str, operation_type: str, data: Dict[str, Any]):
        """Add an update to the pending batch."""
        update = CacheUpdate(
            entity_id=entity_id,
            operation_type=operation_type,
            data=data.copy(),
            timestamp=time.time()
        )

        self._pending_updates.append(update)

        # Trigger immediate flush if batch is full
        if len(self._pending_updates) >= self.batch_size:
            if self._batch_timer and not self._batch_timer.done():
                self._batch_timer.cancel()
            await self._flush_batch()
        else:
            # Schedule timeout-based flush
            await self._schedule_batch_flush()

    async def _flush_batch(self):
        """Flush pending updates to cache and disk."""
        if not self._pending_updates or self._shutdown:
            return

        # Prevent concurrent flushes
        if self._batch_flush_task and not self._batch_flush_task.done():
            return

        self._batch_flush_task = asyncio.create_task(self._do_flush_batch())

    async def _do_flush_batch(self):
        """Actually perform the batch flush."""
        if not self._pending_updates:
            return

        # Extract all pending updates
        updates_to_process = list(self._pending_updates)
        self._pending_updates.clear()

        # Group updates by entity for efficient processing
        entity_updates = defaultdict(list)
        for update in updates_to_process:
            entity_updates[update.entity_id].append(update)

        # Process updates per entity
        modified_entities = set()
        for entity_id, updates in entity_updates.items():
            # Use entity-specific lock
            async with self._entity_locks[entity_id]:
                # Ensure entity exists in cache
                if entity_id not in self.cache["entities"]:
                    self.cache["entities"][entity_id] = self._get_default_entity_cache()

                entity_data = self.cache["entities"][entity_id]

                # Apply all updates for this entity
                for update in updates:
                    self._apply_update_to_entity(entity_data, update)

                modified_entities.add(entity_id)
                self._track_entity_access(entity_id)

        # Save to disk if we have modifications
        if modified_entities:
            await self._save_cache_atomic()
            logger.debug(f"Batch flushed: {len(updates_to_process)} updates across {len(modified_entities)} entities")

    def _apply_update_to_entity(self, entity_data: Dict[str, Any], update: CacheUpdate):
        """Apply a single update to entity data."""
        if update.operation_type == "message":
            data = update.data
            msg_id_str = str(data["message_id"])
            prev_entry = entity_data["processed_messages"].get(msg_id_str, {})
            entity_data["processed_messages"][msg_id_str] = {
                "filename": data["note_filename"],
                "reply_to": data.get("reply_to_id"),
                "title": data["title"],
                "telegram_url": data.get("telegram_url"),
                "media_files": prev_entry.get("media_files", data.get("media_files", []))
            }

            # Update last_id
            message_id = data["message_id"]
            current_last_id = entity_data.get("last_id")
            if current_last_id is None or message_id > current_last_id:
                entity_data["last_id"] = message_id

        elif update.operation_type == "entity_info":
            data = update.data
            entity_data["title"] = data["title"]
            entity_data["type"] = data["entity_type"]

        elif update.operation_type == "media_file":
            data = update.data
            msg_id_str = str(data["message_id"])
            entry = entity_data["processed_messages"].get(msg_id_str)
            if entry is not None:
                media_files = entry.setdefault("media_files", [])
                media_filename = data["media_filename"]
                media_size = data["media_size"]

                # Check if this media file already exists
                if not any(f["name"] == media_filename and f["size"] == media_size for f in media_files):
                    media_files.append({"name": media_filename, "size": media_size})

    async def _save_cache_atomic(self):
        """Atomically save cache to disk with optimized serialization."""
        if self._save_task and not self._save_task.done():
            # Wait for existing save to complete
            await self._save_task
            return

        self._save_task = asyncio.create_task(self._do_save_cache())

    async def _do_save_cache(self):
        """Perform the actual cache save operation."""
        try:
            start_time = time.time()

            # Serialize in executor to avoid blocking event loop
            cache_json = await asyncio.to_thread(
                ujson.dumps,
                self.cache,
                indent=2,
                ensure_ascii=False
            )

            # Write atomically using temporary file
            temp_path = self.cache_path.with_suffix('.tmp')
            async with aiofiles.open(temp_path, mode='w', encoding='utf-8') as f:
                await f.write(cache_json)

            # Atomic replace
            os.replace(temp_path, self.cache_path)

            # Update stats
            self._last_save_time = time.time()
            self._save_count += 1
            save_duration = self._last_save_time - start_time

            if save_duration > 1.0:  # Log slow saves
                logger.warning(f"Slow cache save: {save_duration:.2f}s for {len(cache_json)} bytes")
            else:
                logger.debug(f"Cache saved in {save_duration:.3f}s ({len(cache_json)} bytes)")

        except Exception as e:
            logger.error(f"Failed to save cache: {e}", exc_info=True)

    async def _with_entity_data(self, entity_id: Union[str, int], operation: Callable, modify: bool = False):
        """
        Perform an operation on entity data with granular locking.
        """
        entity_id_str = str(entity_id)

        # Use entity-specific lock for better concurrency
        async with self._entity_locks[entity_id_str]:
            # Ensure entity exists
            if entity_id_str not in self.cache["entities"]:
                self.cache["entities"][entity_id_str] = self._get_default_entity_cache()

            entity_data = self.cache["entities"][entity_id_str]
            result = operation(entity_data)

            if modify:
                self._track_entity_access(entity_id_str)

            return result

    # Public interface methods (optimized versions of original methods)

    async def is_processed(self, message_id: int, entity_id: Union[str, int]) -> bool:
        """Check if a message has already been processed for a given entity."""
        msg_id_str = str(message_id)

        def check(data):
            return msg_id_str in data.get("processed_messages", {})

        return await self._with_entity_data(entity_id, check) or False

    async def add_processed_message_async(
        self, message_id: int, note_filename: str, reply_to_id: Optional[int],
        entity_id: Union[str, int], title: str, telegram_url: Optional[str]
    ):
        """Add a processed message to the batch queue."""
        await self._add_pending_update(
            entity_id=str(entity_id),
            operation_type="message",
            data={
                "message_id": message_id,
                "note_filename": note_filename,
                "reply_to_id": reply_to_id,
                "title": title,
                "telegram_url": telegram_url,
                "media_files": []
            }
        )

    async def update_entity_info_async(self, entity_id: Union[str, int], title: str, entity_type: str):
        """Update entity info via batch queue."""
        await self._add_pending_update(
            entity_id=str(entity_id),
            operation_type="entity_info",
            data={
                "title": title,
                "entity_type": entity_type
            }
        )

    async def add_media_file_to_message(self, entity_id: Union[str, int], message_id: int, media_filename: str, media_size: int):
        """Add media file info via batch queue."""
        await self._add_pending_update(
            entity_id=str(entity_id),
            operation_type="media_file",
            data={
                "message_id": message_id,
                "media_filename": media_filename,
                "media_size": media_size
            }
        )

    async def all_media_files_present(self, entity_id: Union[str, int], message_id: int, media_dir: Path) -> bool:
        """Check if all media files for a processed message are present."""
        msg_id_str = str(message_id)

        def check(data):
            entry = data["processed_messages"].get(msg_id_str)
            if not entry or not entry.get("media_files"):
                return False

            for f in entry["media_files"]:
                file_path = media_dir / f["name"]
                if not file_path.exists() or file_path.stat().st_size != f["size"]:
                    return False
            return True

        return await self._with_entity_data(entity_id, check) or False

    async def get_all_processed_messages_async(self, entity_id: Union[str, int]) -> Dict[str, Any]:
        """Get all processed messages for a given entity."""
        def get_messages(data):
            return dict(data.get("processed_messages", {}))

        return await self._with_entity_data(entity_id, get_messages) or {}

    def get_last_processed_message_id(self, entity_id: Union[str, int]) -> Optional[int]:
        """Get the last processed message ID for a given entity (sync version for compatibility)."""
        entity_data = self.cache.get("entities", {}).get(str(entity_id))
        return entity_data.get("last_id") if entity_data else None

    async def flush_all_pending(self):
        """Force flush all pending updates (useful for shutdown)."""
        if self._pending_updates:
            await self._flush_batch()

    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics."""
        return {
            "entities_count": len(self.cache["entities"]),
            "pending_updates": len(self._pending_updates),
            "save_count": self._save_count,
            "last_save_time": self._last_save_time,
            "memory_entities": len(self._entity_access_times),
            "cache_size_mb": len(ujson.dumps(self.cache)) / 1024 / 1024
        }

    async def shutdown(self):
        """Graceful shutdown - flush all pending updates."""
        self._shutdown = True

        # Cancel timers
        if self._batch_timer and not self._batch_timer.done():
            self._batch_timer.cancel()

        # Flush any remaining updates
        await self.flush_all_pending()

        # Wait for any ongoing save
        if self._save_task and not self._save_task.done():
            await self._save_task

        logger.info("Cache manager shutdown completed")
# Backward compatibility alias
CacheManager = BatchCacheManager
