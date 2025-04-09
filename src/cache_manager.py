import json
import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Any, Optional, Set
from src.utils import logger
import aiofiles
import threading
from functools import partial
import multiprocessing

class CacheManager:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: Dict[str, Any] = {"processed_messages": {}, "replies": {}}
        self.processed_ids: Set[str] = set()  # Changed to Set[str] to match actual usage
        self._cache_lock = threading.RLock()  # Add lock for thread safety
        # Use more workers for better parallelism, based on CPU cores
        self._executor = ThreadPoolExecutor(max_workers=max(8, multiprocessing.cpu_count() * 2))
        # Process pool for CPU-bound tasks like JSON parsing of large files
        self._process_pool = ProcessPoolExecutor(max_workers=max(2, multiprocessing.cpu_count()))
        # Memory cache for faster lookups
        self._lookup_cache = {}
        # Background task for periodic saves
        self._background_tasks = set()

    async def load_cache(self):
        """Loads cache data from the JSON file."""
        if not self.cache_path.exists():
            logger.warning(f"Cache file not found at {self.cache_path}. Starting fresh.")
            return

        loop = asyncio.get_running_loop()
        try:
            # Run file I/O in a thread pool for better performance
            async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                content = await f.read()
                if not content:
                    logger.warning(f"Cache file {self.cache_path} is empty. Starting fresh.")
                    return

                # Use process pool for CPU-intensive JSON parsing of potentially large files
                self.cache = await loop.run_in_executor(
                    self._process_pool,
                    json.loads,
                    content
                )

                # Run key setup and validation tasks concurrently
                await asyncio.gather(
                    self._setup_cache_keys(),
                    self._validate_cache_data()
                )

                # Populate processed_ids set in a separate thread for better parallelism
                await loop.run_in_executor(
                    self._executor,
                    self._populate_processed_ids
                )

                logger.info(f"Loaded cache from {self.cache_path}. {len(self.processed_ids)} messages processed previously.")

        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from cache file {self.cache_path}. Cache might be corrupted. Starting fresh.")
            await self._reset_cache()
        except Exception as e:
            logger.error(f"Failed to load cache file {self.cache_path}: {e}")
            await self._reset_cache()

    async def _setup_cache_keys(self):
        """Ensure all required keys exist in the cache."""
        with self._cache_lock:
            self.cache.setdefault("processed_messages", {})
            self.cache.setdefault("replies", {})

    async def _validate_cache_data(self):
        """Validate cache data integrity."""
        with self._cache_lock:
            # Additional validation could be added here
            pass

    def _populate_processed_ids(self):
        """Populate the processed_ids set from the cache."""
        with self._cache_lock:
            self.processed_ids = set(self.cache["processed_messages"].keys())
            # Pre-build lookup cache for frequent operations
            self._lookup_cache = {
                "last_id": self._calculate_last_id(),
                "reply_counts": {k: len(v) for k, v in self.cache["replies"].items()}
            }

    def _calculate_last_id(self):
        """Calculate the last processed message ID."""
        if not self.cache["processed_messages"]:
            return None
        try:
            return max(int(k) for k in self.cache["processed_messages"].keys())
        except ValueError:
            return None

    async def _reset_cache(self):
        """Reset cache to initial state."""
        with self._cache_lock:
            self.cache = {"processed_messages": {}, "replies": {}}
            self.processed_ids = set()
            self._lookup_cache = {}

    async def save_cache(self):
        """Saves the current cache state to the JSON file."""
        try:
            loop = asyncio.get_running_loop()

            # Make a copy of the cache atomically to reduce lock contention
            with self._cache_lock:
                cache_copy = dict(self.cache)

            # Serialize JSON in a process pool for CPU-intensive operations with large datasets
            json_data = await loop.run_in_executor(
                self._process_pool,
                partial(json.dumps, cache_copy, indent=4, ensure_ascii=False)
            )

            # Write to temporary file first to avoid corruption on failure
            temp_path = self.cache_path.with_suffix('.tmp')

            # Write to file asynchronously
            async with aiofiles.open(temp_path, mode='w', encoding='utf-8') as f:
                await f.write(json_data)

            # Ensure file is fully written to disk
            await loop.run_in_executor(self._executor, self._fsync_dir, temp_path)

            # Atomic rename for safer file operations
            await loop.run_in_executor(self._executor,
                                     self._atomic_replace,
                                     temp_path,
                                     self.cache_path)

            logger.info(f"Cache saved to {self.cache_path}")
        except Exception as e:
            logger.error(f"Failed to save cache to {self.cache_path}: {e}")

    def _fsync_dir(self, filepath):
        """Ensure file changes are flushed to disk."""
        try:
            import os
            dir_fd = os.open(os.path.dirname(filepath), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (ImportError, AttributeError, OSError):
            pass  # Skip if not available on this platform

    def _atomic_replace(self, src, dst):
        """Atomically replace dst with src."""
        import os
        try:
            os.replace(src, dst)  # Atomic on most platforms
        except:
            # Fallback for platforms without atomic replace
            import shutil
            shutil.copy2(src, dst)
            os.unlink(src)

    async def schedule_background_save(self):
        """Schedule a background save operation."""
        task = asyncio.create_task(self.save_cache())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def is_processed_async(self, message_id: int) -> bool:
        """Asynchronous version to check if a message ID has been processed."""
        msg_id_str = str(message_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: msg_id_str in self.processed_ids
        )

    def is_processed(self, message_id: int) -> bool:
        """Checks if a message ID has already been processed."""
        msg_id_str = str(message_id)
        # Using processed_ids set for faster lookup without acquiring the full lock
        return msg_id_str in self.processed_ids

    async def add_processed_message_async(self, message_id: int, note_filename: str, reply_to_id: Optional[int]):
        """Asynchronous version to add a processed message."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._executor,
            lambda: self.add_processed_message(message_id, note_filename, reply_to_id)
        )
        # Schedule background save to avoid blocking the main thread
        asyncio.create_task(self.schedule_background_save())

    def add_processed_message(self, message_id: int, note_filename: str, reply_to_id: Optional[int]):
        """Adds a message to the processed cache."""
        msg_id_str = str(message_id)
        with self._cache_lock:
            self.cache["processed_messages"][msg_id_str] = {
                "filename": note_filename,
                "reply_to": reply_to_id
            }
            self.processed_ids.add(msg_id_str)  # Keep the set updated

            # Update lookup cache
            if self._lookup_cache.get("last_id") is None or int(msg_id_str) > self._lookup_cache["last_id"]:
                self._lookup_cache["last_id"] = int(msg_id_str)

            if reply_to_id:
                # Store the reply relationship for later linking
                reply_to_str = str(reply_to_id)
                if reply_to_str not in self.cache["replies"]:
                    self.cache["replies"][reply_to_str] = []
                if msg_id_str not in self.cache["replies"][reply_to_str]:  # Avoid duplicates
                    self.cache["replies"][reply_to_str].append(msg_id_str)
                    # Update reply count in lookup cache
                    if "reply_counts" in self._lookup_cache:
                        self._lookup_cache["reply_counts"][reply_to_str] = len(self.cache["replies"][reply_to_str])

    async def get_note_filename_async(self, message_id: int) -> Optional[str]:
        """Asynchronous method to retrieve note filename."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.get_note_filename,
            message_id
        )

    def get_note_filename(self, message_id: int) -> Optional[str]:
        """Retrieves the note filename for a given message ID."""
        with self._cache_lock:
            return self.cache["processed_messages"].get(str(message_id), {}).get("filename")

    async def get_reply_children_async(self, parent_message_id: int) -> list[str]:
        """Asynchronous method to get reply children."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.get_reply_children,
            parent_message_id
        )

    def get_reply_children(self, parent_message_id: int) -> list[str]:
        """Gets the IDs of messages replying to the given parent ID."""
        parent_id_str = str(parent_message_id)
        with self._cache_lock:
            return self.cache["replies"].get(parent_id_str, [])

    async def get_all_processed_messages_async(self) -> Dict[str, Any]:
        """Asynchronous method to get all processed messages."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.get_all_processed_messages
        )

    def get_all_processed_messages(self) -> Dict[str, Any]:
        """Gets all processed messages."""
        with self._cache_lock:
            return dict(self.cache.get("processed_messages", {}))  # Return a copy to prevent race conditions

    def get_last_processed_message_id(self) -> Optional[int]:
        """Finds the highest message ID in the cache."""
        # Use cached value if available for performance
        if "last_id" in self._lookup_cache:
            return self._lookup_cache["last_id"]

        with self._cache_lock:
            if not self.cache["processed_messages"]:
                return None
            try:
                last_id = max(int(k) for k in self.cache["processed_messages"].keys())
                self._lookup_cache["last_id"] = last_id
                return last_id
            except ValueError:
                logger.error("Found non-integer keys in processed_messages cache.")
                return None
