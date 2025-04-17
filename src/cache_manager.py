import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import aiofiles

from src.utils import logger


class CacheManager:
    def __init__(self, cache_path: Path):
        """Initializes the CacheManager for centralized cache file handling."""
        self.cache_path = cache_path.resolve()
        self.cache = {"version": 1, "entities": {}}

        # Single lock for all operations (sync uses this via run_in_executor)
        self._lock = asyncio.Lock()

        # Single thread pool for I/O operations
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="CacheThread")

        # Background save management
        self._save_task = None
        self._dirty = False

        logger.info(f"Cache Manager initialized. Cache file: {self.cache_path}")

    async def load_cache(self):
        """Loads cache data from the JSON file asynchronously."""
        async with self._lock:
            if not self.cache_path.exists():
                logger.warning(f"Cache file not found at {self.cache_path}. Starting with empty cache.")
                return

            try:
                logger.info(f"Loading cache from {self.cache_path}...")

                async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                    content = await f.read()

                if not content:
                    logger.warning(f"Cache file {self.cache_path} is empty. Starting fresh.")
                    return

                loop = asyncio.get_running_loop()
                loaded_data = await loop.run_in_executor(self._pool, json.loads, content)

                if not isinstance(loaded_data, dict) or "version" not in loaded_data:
                    logger.error("Cache file has invalid format. Starting fresh.")
                    return

                # Update cache with loaded data
                self.cache = loaded_data
                self.cache.setdefault("entities", {})

                # Ensure all entity entries have required fields
                for entity_id, data in self.cache["entities"].items():
                    if not isinstance(data, dict):
                        logger.warning(f"Invalid data structure for entity {entity_id}. Resetting.")
                        self.cache["entities"][entity_id] = self._get_default_entity_cache()
                    else:
                        data.setdefault("processed_messages", {})
                        data.setdefault("last_id", None)
                        data.setdefault("title", "Unknown")
                        data.setdefault("type", "unknown")
                        data.setdefault("processed_count", len(data.get("processed_messages", {})))

                total_entities = len(self.cache["entities"])
                total_messages = sum(len(e.get("processed_messages", {}))
                                     for e in self.cache["entities"].values())

                logger.info(f"Cache loaded: {total_entities} entities, {total_messages} total messages.")

            except json.JSONDecodeError:
                logger.error("Error decoding JSON from cache file. Starting fresh.")
                self.cache = {"version": 1, "entities": {}}
            except Exception as e:
                logger.error(f"Failed to load cache file: {e}")
                self.cache = {"version": 1, "entities": {}}
            finally:
                self._dirty = False

    def _get_default_entity_cache(self) -> Dict[str, Any]:
        """Returns the default structure for a new entity in the cache."""
        return {
            "processed_messages": {},
            "replies_pointing_here": {},
            "last_id": None,
            "title": "Unknown",
            "type": "unknown",
            "processed_count": 0
        }

    async def save_cache(self):
        """Saves the current cache state to the JSON file if marked as dirty."""
        if not self._dirty:
            return

        async with self._lock:
            if not self._dirty:
                return

            try:
                logger.info(f"Saving cache to {self.cache_path}...")

                # Make a copy to avoid modification during serialization
                loop = asyncio.get_running_loop()
                cache_json = await loop.run_in_executor(
                    self._pool,
                    partial(json.dumps, self.cache, indent=2, ensure_ascii=False)
                )

                # Ensure directory exists
                await loop.run_in_executor(
                    self._pool,
                    lambda: self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                )

                # Write to temp file first, then replace
                temp_path = self.cache_path.with_suffix('.tmp')
                async with aiofiles.open(temp_path, mode='w', encoding='utf-8') as f:
                    await f.write(cache_json)

                # Atomic replace
                await loop.run_in_executor(self._pool, os.replace, temp_path, self.cache_path)

                logger.info("Cache saved successfully.")
                self._dirty = False

            except Exception as e:
                logger.error(f"Failed to save cache: {e}")

    async def schedule_background_save(self):
        """Schedules a cache save operation if needed and not already running."""
        if not self._dirty or (self._save_task and not self._save_task.done()):
            return

        await asyncio.sleep(0.5)  # Short delay to allow batching

        if not self._dirty:
            return

        # Create and track the save task
        self._save_task = asyncio.create_task(self.save_cache())

        def on_complete(task):
            try:
                task.result()  # Get result to propagate exceptions
            except Exception as e:
                logger.warning(f"Background save task failed: {e}")
            finally:
                if self._save_task is task:
                    self._save_task = None

        self._save_task.add_done_callback(on_complete)

    # Helper method to run an entity data access operation with the lock
    async def _with_entity_data(self, entity_id: Union[str, int],
                                operation: Callable[[Dict[str, Any]], Any],
                                modify: bool = False) -> Any:
        """Executes an operation on entity data with proper locking."""
        entity_id_str = str(entity_id)

        async with self._lock:
            # Get entity data, creating if needed
            if entity_id_str not in self.cache["entities"]:
                if modify:  # Only create if we're going to modify
                    self.cache["entities"][entity_id_str] = self._get_default_entity_cache()

            entity_data = self.cache["entities"].get(entity_id_str)
            if not entity_data and not modify:
                return None  # No data and not modifying = return None

            # Run the operation
            result = operation(entity_data) if entity_data else None

            # Mark dirty if modified
            if modify:
                self._dirty = True

            return result

    # === Message Processing Methods ===

    async def is_processed_async(self, message_id: int, entity_id: Union[str, int]) -> bool:
        """Check if a message ID has been processed for a specific entity."""
        msg_id_str = str(message_id)

        def check(data):
            return msg_id_str in data.get("processed_messages", {})

        return await self._with_entity_data(entity_id, check) or False

    def is_processed(self, message_id: int, entity_id: Union[str, int]) -> bool:
        """Synchronous version - runs in an executor via direct call."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.is_processed_async(message_id, entity_id))
        finally:
            loop.close()

    async def add_processed_message_async(
        self,
        message_id: int,
        note_filename: str,
        reply_to_id: Optional[int],
        entity_id: Union[str, int]
    ):
        """Adds a processed message to the cache for a specific entity."""
        msg_id_str = str(message_id)

        def update(data):
            # Add message data
            if msg_id_str not in data["processed_messages"]:
                data["processed_messages"][msg_id_str] = {
                    "filename": note_filename,
                    "reply_to": reply_to_id
                }
                data["processed_count"] = data.get("processed_count", 0) + 1

            # Update last ID if higher
            current_last_id = data.get("last_id")
            if current_last_id is None or message_id > current_last_id:
                data["last_id"] = message_id

            # Track reply relationships
            if reply_to_id:
                reply_to_str = str(reply_to_id)
                data.setdefault("replies_pointing_here", {})
                data["replies_pointing_here"].setdefault(reply_to_str, [])
                if msg_id_str not in data["replies_pointing_here"][reply_to_str]:
                    data["replies_pointing_here"][reply_to_str].append(msg_id_str)

        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    def add_processed_message(
        self,
        message_id: int,
        note_filename: str,
        reply_to_id: Optional[int],
        entity_id: Union[str, int]
    ):
        """Synchronous version - runs in an executor via direct call."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                self.add_processed_message_async(message_id, note_filename, reply_to_id, entity_id)
            )
        finally:
            loop.close()

    # === Entity Info Methods ===

    async def update_entity_info_async(self, entity_id: Union[str, int], title: str, entity_type: str):
        """Update entity information (title, type) in the cache."""
        def update(data):
            if data.get("title") != title or data.get("type") != entity_type:
                data["title"] = title
                data["type"] = entity_type

        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    def update_entity_info(self, entity_id: Union[str, int], title: str, entity_type: str):
        """Synchronous version - runs in an executor via direct call."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                self.update_entity_info_async(entity_id, title, entity_type)
            )
        finally:
            loop.close()

    # === Data Retrieval Methods ===

    async def get_note_filename_async(self, message_id: int, entity_id: Union[str, int]) -> Optional[str]:
        """Retrieve note filename for a specific entity."""
        msg_id_str = str(message_id)

        def get_filename(data):
            return data.get("processed_messages", {}).get(msg_id_str, {}).get("filename")

        return await self._with_entity_data(entity_id, get_filename)

    def get_note_filename(self, message_id: int, entity_id: Union[str, int]) -> Optional[str]:
        """Synchronous version - runs in an executor via direct call."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.get_note_filename_async(message_id, entity_id)
            )
        finally:
            loop.close()

    async def get_all_processed_messages_async(self, entity_id: Union[str, int]) -> Dict[str, Any]:
        """Get all processed messages for a specific entity."""
        def get_messages(data):
            return dict(data.get("processed_messages", {}))

        result = await self._with_entity_data(entity_id, get_messages)
        return result or {}

    def get_all_processed_messages(self, entity_id: Union[str, int]) -> Dict[str, Any]:
        """Synchronous version - runs in an executor via direct call."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.get_all_processed_messages_async(entity_id)
            )
        finally:
            loop.close()

    def get_last_processed_message_id(self, entity_id: Union[str, int]) -> Optional[int]:
        """Finds the highest message ID processed for a specific entity."""
        entity_id_str = str(entity_id)

        # Direct dictionary access for this simple getter
        entity_data = self.cache.get("entities", {}).get(entity_id_str)
        if entity_data:
            return entity_data.get("last_id")
        return None

    async def get_entity_stats_async(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all entities asynchronously."""
        async with self._lock:
            stats = {}
            for entity_id, data in self.cache.get("entities", {}).items():
                stats[entity_id] = {
                    "title": data.get("title", "Unknown"),
                    "type": data.get("type", "unknown"),
                    "processed_count": data.get("processed_count", 0),
                    "last_id": data.get("last_id")
                }
            return stats

    def get_entity_stats(self) -> Dict[str, Dict[str, Any]]:
        """Synchronous version - runs in an executor via direct call."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.get_entity_stats_async())
        finally:
            loop.close()
