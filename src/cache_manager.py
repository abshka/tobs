import json
import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Any, Optional, Union
from src.utils import logger
import aiofiles
from functools import partial
import multiprocessing

class CacheManager:
    def __init__(self, cache_path: Path):
        """
        Initializes the CacheManager for a central cache file handling multiple entities.

        Args:
            cache_path: The Path object pointing to the JSON cache file.
        """
        self.cache_path = cache_path.resolve()
        # Main cache structure: Dict[entity_id_str, EntityCacheData]
        # EntityCacheData: {"processed_messages": {msg_id: data}, "last_id": int, "title": str, "type": str}
        self.cache: Dict[str, Any] = {"version": 1, "entities": {}}
        self._cache_lock = threading.RLock() # Lock for synchronous access to self.cache
        self._async_lock = asyncio.Lock() # Lock for async operations modifying the cache file

        # Executors for I/O and CPU-bound tasks
        cpu_cores = multiprocessing.cpu_count() or 1
        self._thread_pool = ThreadPoolExecutor(max_workers=max(8, cpu_cores * 2), thread_name_prefix="CacheThread")
        self._process_pool = ProcessPoolExecutor(max_workers=max(2, cpu_cores // 2)) # For heavy JSON parsing/dumping

        self._save_task: Optional[asyncio.Task] = None
        self._dirty = False # Flag to indicate if cache needs saving

        logger.info(f"Cache Manager initialized. Cache file: {self.cache_path}")

    async def load_cache(self):
        """Loads cache data from the JSON file asynchronously."""
        async with self._async_lock: # Ensure only one load/save operation at a time
            if not self.cache_path.exists():
                logger.warning(f"Cache file not found at {self.cache_path}. Starting with an empty cache.")
                self.cache = {"version": 1, "entities": {}}
                return

            loop = asyncio.get_running_loop()
            try:
                logger.info(f"Loading cache from {self.cache_path}...")
                # Read file async
                async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                    content = await f.read()

                if not content:
                    logger.warning(f"Cache file {self.cache_path} is empty. Starting fresh.")
                    self.cache = {"version": 1, "entities": {}}
                    return

                # Parse JSON in process pool (CPU intensive for large files)
                loaded_data = await loop.run_in_executor(
                    self._process_pool,
                    json.loads,
                    content
                )

                # Basic validation and migration (if needed)
                if isinstance(loaded_data, dict) and "version" in loaded_data:
                     # Add migration logic here if cache structure changes
                    with self._cache_lock:
                        self.cache = loaded_data
                        # Ensure entities key exists
                        self.cache.setdefault("entities", {})
                        # Ensure nested structure for each entity
                        for entity_id, data in self.cache["entities"].items():
                            if not isinstance(data, dict):
                                logger.warning(f"Invalid data structure for entity {entity_id} in cache. Resetting.")
                                self.cache["entities"][entity_id] = self._get_default_entity_cache()
                            else:
                                # Ensure required sub-keys exist
                                data.setdefault("processed_messages", {})
                                data.setdefault("last_id", None)
                                data.setdefault("title", "Unknown")
                                data.setdefault("type", "unknown")
                                data.setdefault("processed_count", len(data.get("processed_messages", {})))

                    total_entities = len(self.cache["entities"])
                    total_messages = sum(len(e.get("processed_messages", {})) for e in self.cache["entities"].values())
                    logger.info(f"Cache loaded successfully. Version: {self.cache.get('version', 'N/A')}. "
                                f"{total_entities} entities, {total_messages} total processed messages.")
                else:
                     logger.error(f"Cache file {self.cache_path} has invalid format. Starting fresh.")
                     self.cache = {"version": 1, "entities": {}}


            except json.JSONDecodeError:
                logger.error(f"Error decoding JSON from cache file {self.cache_path}. Cache might be corrupted. Starting fresh.")
                self.cache = {"version": 1, "entities": {}}
            except Exception as e:
                logger.error(f"Failed to load cache file {self.cache_path}: {e}", exc_info=True)
                # Fallback to empty cache on unexpected error
                self.cache = {"version": 1, "entities": {}}
            finally:
                self._dirty = False # Cache is clean after loading

    def _get_default_entity_cache(self) -> Dict[str, Any]:
        """Returns the default structure for a new entity in the cache."""
        return {
            "processed_messages": {}, # {msg_id_str: {"filename": str, "reply_to": Optional[int]}}
            "replies_pointing_here": {}, # {parent_msg_id_str: [child_msg_id_str,...]} -> For faster reply linking lookup
            "last_id": None,
            "title": "Unknown",
            "type": "unknown",
            "processed_count": 0
        }

    async def save_cache(self):
        """Saves the current cache state to the JSON file asynchronously if dirty."""
        if not self._dirty:
             # logger.debug("Cache not modified, skipping save.")
             return

        async with self._async_lock: # Ensure only one save operation at a time
             if not self._dirty: # Double check after acquiring lock
                 return

             loop = asyncio.get_running_loop()
             try:
                 # Perform deep copy under thread lock for safety before async operations
                 with self._cache_lock:
                     cache_copy = json.loads(json.dumps(self.cache)) # Simple deep copy via json

                 logger.info(f"Saving cache to {self.cache_path}...")

                 # Serialize JSON in process pool
                 json_data = await loop.run_in_executor(
                     self._process_pool,
                     partial(json.dumps, cache_copy, indent=2, ensure_ascii=False) # Use indent=2 for smaller files
                 )

                 # Ensure parent directory exists (run in thread pool)
                 await loop.run_in_executor(
                     self._thread_pool,
                     lambda: self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                 )

                 # Atomic write using temp file + rename
                 temp_path = self.cache_path.with_suffix('.tmp')
                 async with aiofiles.open(temp_path, mode='w', encoding='utf-8') as f:
                     await f.write(json_data)

                 # Ensure file is flushed (best effort) before rename
                 # await loop.run_in_executor(self._thread_pool, self._fsync_dir, temp_path)

                 # Atomic rename (run in thread pool)
                 await loop.run_in_executor(self._thread_pool, os.replace, temp_path, self.cache_path)

                 logger.info("Cache saved successfully.")
                 self._dirty = False # Mark as clean after successful save

             except Exception as e:
                 logger.error(f"Failed to save cache to {self.cache_path}: {e}", exc_info=True)
                 # Keep dirty flag true so it retries on next schedule

    async def schedule_background_save(self):
        """Schedules a cache save operation if needed and not already running."""
        if not self._dirty:
            return # No changes to save

        if self._save_task and not self._save_task.done():
            # logger.debug("Save task already scheduled or running.")
            return # Save already in progress or scheduled

        # Simple debounce: wait a short period before actually saving
        await asyncio.sleep(0.5) # Wait 500ms

        if not self._dirty: # Check again after sleep
            return

        # Schedule the save task
        self._save_task = asyncio.create_task(self.save_cache())

        # Optional: Callback to clear task reference
        def _clear_task(task):
            try:
                task.result() # Raise exception if save failed
            except Exception as e:
                 logger.warning(f"Background save task failed: {e}")
            finally:
                # Check if this is still the current task before clearing
                 if self._save_task is task:
                    self._save_task = None

        self._save_task.add_done_callback(_clear_task)
        # logger.debug("Background save task scheduled.")

    def _fsync_dir(self, filepath: Path):
        """Ensures directory entry for the file is flushed (Linux/macOS)."""
        # This is often unnecessary with journaling filesystems but can help ensure atomicity
        if hasattr(os, 'fsync'):
            dir_fd = None
            try:
                dir_fd = os.open(os.path.dirname(filepath), os.O_DIRECTORY)
                os.fsync(dir_fd)
            except OSError as e:
                logger.warning(f"Could not fsync directory for {filepath}: {e}")
            finally:
                if dir_fd is not None:
                    os.close(dir_fd)

    async def is_processed_async(self, message_id: int, entity_id: Union[str, int]) -> bool:
        """Asynchronous check if a message ID has been processed for a specific entity."""
        msg_id_str = str(message_id)
        entity_id_str = str(entity_id)

        # Access cache directly (assuming reads are relatively safe without lock for bool check)
        # For higher safety, use run_in_executor with the sync version:
        # loop = asyncio.get_running_loop()
        # return await loop.run_in_executor(self._thread_pool, self.is_processed, message_id, entity_id)

        # Direct read attempt (usually safe for checking existence)
        with self._cache_lock: # Use lock for consistency
            entity_data = self.cache.get("entities", {}).get(entity_id_str)
            if entity_data:
                return msg_id_str in entity_data.get("processed_messages", {})
            return False

    def is_processed(self, message_id: int, entity_id: Union[str, int]) -> bool:
        """Synchronous check if a message ID has been processed for a specific entity."""
        msg_id_str = str(message_id)
        entity_id_str = str(entity_id)
        with self._cache_lock:
            entity_data = self.cache.get("entities", {}).get(entity_id_str)
            if entity_data:
                return msg_id_str in entity_data.get("processed_messages", {})
            return False

    async def add_processed_message_async(
        self,
        message_id: int,
        note_filename: str,
        reply_to_id: Optional[int],
        entity_id: Union[str, int]
    ):
        """Asynchronously adds a processed message to the cache for a specific entity."""
        loop = asyncio.get_running_loop()
        # Run the synchronous update logic in the thread pool to avoid blocking async loop
        await loop.run_in_executor(
            self._thread_pool,
            self.add_processed_message,
            message_id, note_filename, reply_to_id, entity_id
        )
        # Schedule a background save after modification
        await self.schedule_background_save()


    def add_processed_message(
        self,
        message_id: int,
        note_filename: str,
        reply_to_id: Optional[int],
        entity_id: Union[str, int]
    ):
        """Synchronously adds a processed message to the cache for a specific entity."""
        msg_id_str = str(message_id)
        entity_id_str = str(entity_id)

        with self._cache_lock:
            # Ensure entity exists in cache
            if entity_id_str not in self.cache.get("entities", {}):
                self.cache.setdefault("entities", {})[entity_id_str] = self._get_default_entity_cache()

            entity_data = self.cache["entities"][entity_id_str]

            # Add message data
            if msg_id_str not in entity_data["processed_messages"]:
                entity_data["processed_messages"][msg_id_str] = {
                    "filename": note_filename,
                    "reply_to": reply_to_id
                }
                entity_data["processed_count"] = entity_data.get("processed_count", 0) + 1
                self._dirty = True # Mark cache as dirty
            else:
                # Message already exists, maybe update? For now, do nothing.
                # logger.trace(f"Message {msg_id_str} already in cache for entity {entity_id_str}.")
                pass

            # Update last processed ID for the entity
            current_last_id = entity_data.get("last_id")
            if current_last_id is None or message_id > current_last_id:
                entity_data["last_id"] = message_id
                self._dirty = True

            # Store reply relationship for linking (parent_id -> list_of_children_ids)
            if reply_to_id:
                reply_to_str = str(reply_to_id)
                entity_data.setdefault("replies_pointing_here", {})
                entity_data["replies_pointing_here"].setdefault(reply_to_str, [])
                if msg_id_str not in entity_data["replies_pointing_here"][reply_to_str]:
                    entity_data["replies_pointing_here"][reply_to_str].append(msg_id_str)
                    self._dirty = True


    async def update_entity_info_async(self, entity_id: Union[str, int], title: str, entity_type: str):
        """Asynchronously update entity information (title, type) in the cache."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._thread_pool,
            self.update_entity_info,
            entity_id, title, entity_type
        )
        await self.schedule_background_save()

    def update_entity_info(self, entity_id: Union[str, int], title: str, entity_type: str):
        """Synchronously update entity information (title, type) in the cache."""
        entity_id_str = str(entity_id)
        with self._cache_lock:
             if entity_id_str not in self.cache.get("entities", {}):
                self.cache.setdefault("entities", {})[entity_id_str] = self._get_default_entity_cache()

             entity_data = self.cache["entities"][entity_id_str]
             if entity_data.get("title") != title or entity_data.get("type") != entity_type:
                 entity_data["title"] = title
                 entity_data["type"] = entity_type
                 self._dirty = True

    async def get_note_filename_async(self, message_id: int, entity_id: Union[str, int]) -> Optional[str]:
        """Asynchronous method to retrieve note filename for a specific entity."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self.get_note_filename,
            message_id, entity_id
        )

    def get_note_filename(self, message_id: int, entity_id: Union[str, int]) -> Optional[str]:
        """Retrieves the note filename for a given message ID within a specific entity."""
        msg_id_str = str(message_id)
        entity_id_str = str(entity_id)
        with self._cache_lock:
            entity_data = self.cache.get("entities", {}).get(entity_id_str)
            if entity_data:
                return entity_data.get("processed_messages", {}).get(msg_id_str, {}).get("filename")
            return None

    async def get_all_processed_messages_async(self, entity_id: Union[str, int]) -> Dict[str, Any]:
        """Asynchronous method to get all processed messages for a specific entity."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self.get_all_processed_messages,
            entity_id
        )

    def get_all_processed_messages(self, entity_id: Union[str, int]) -> Dict[str, Any]:
        """Gets all processed messages for a specific entity."""
        entity_id_str = str(entity_id)
        with self._cache_lock:
            entity_data = self.cache.get("entities", {}).get(entity_id_str)
            if entity_data:
                # Return a copy to prevent external modification
                return dict(entity_data.get("processed_messages", {}))
            return {}

    def get_last_processed_message_id(self, entity_id: Union[str, int]) -> Optional[int]:
        """Finds the highest message ID processed for a specific entity."""
        entity_id_str = str(entity_id)
        with self._cache_lock:
            entity_data = self.cache.get("entities", {}).get(entity_id_str)
            if entity_data:
                return entity_data.get("last_id") # Use cached last_id
            return None

    async def get_entity_stats_async(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all entities asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self.get_entity_stats
        )

    def get_entity_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all entities."""
        with self._cache_lock:
            # Return a copy of the entity data (without messages)
            stats = {}
            for entity_id, data in self.cache.get("entities", {}).items():
                 stats[entity_id] = {
                     "title": data.get("title", "Unknown"),
                     "type": data.get("type", "unknown"),
                     "processed_count": data.get("processed_count", 0),
                     "last_id": data.get("last_id")
                 }
            return stats
