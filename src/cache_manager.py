import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, Optional, Set
from src.utils import logger
import aiofiles
import threading

class CacheManager:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: Dict[str, Any] = {"processed_messages": {}, "replies": {}}
        self.processed_ids: Set[str] = set()  # Changed to Set[str] to match actual usage
        self._cache_lock = threading.RLock()  # Add lock for thread safety
        self._executor = ThreadPoolExecutor(max_workers=4)  # Thread pool for I/O operations

    async def load_cache(self):
        """Loads cache data from the JSON file."""
        if not self.cache_path.exists():
            logger.warning(f"Cache file not found at {self.cache_path}. Starting fresh.")
            return

        loop = asyncio.get_event_loop()
        try:
            # Run file I/O in a thread pool for better performance
            async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                content = await f.read()
                if content:  # Check if file is not empty
                    # Parse JSON in a thread pool
                    self.cache = await loop.run_in_executor(self._executor, json.loads, content)

                    # Ensure keys exist
                    self.cache.setdefault("processed_messages", {})
                    self.cache.setdefault("replies", {})

                    # Populate processed_ids set for quick lookups
                    with self._cache_lock:
                        self.processed_ids = set(self.cache["processed_messages"].keys())

                    logger.info(f"Loaded cache from {self.cache_path}. {len(self.processed_ids)} messages processed previously.")
                else:
                    logger.warning(f"Cache file {self.cache_path} is empty. Starting fresh.")

        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from cache file {self.cache_path}. Cache might be corrupted. Starting fresh.")
            self.cache = {"processed_messages": {}, "replies": {}}
            self.processed_ids = set()
        except Exception as e:
            logger.error(f"Failed to load cache file {self.cache_path}: {e}")
            # Decide if we should proceed with an empty cache or raise error
            # For robustness, let's proceed with an empty cache
            self.cache = {"processed_messages": {}, "replies": {}}
            self.processed_ids = set()

    async def save_cache(self):
        """Saves the current cache state to the JSON file."""
        try:
            loop = asyncio.get_event_loop()

            # Serialize JSON in a thread pool
            with self._cache_lock:
                cache_copy = dict(self.cache)  # Make a copy to avoid race conditions

            json_data = await loop.run_in_executor(
                self._executor,
                lambda: json.dumps(cache_copy, indent=4, ensure_ascii=False)
            )

            # Write to file asynchronously
            async with aiofiles.open(self.cache_path, mode='w', encoding='utf-8') as f:
                await f.write(json_data)

            logger.info(f"Cache saved to {self.cache_path}")
        except Exception as e:
            logger.error(f"Failed to save cache to {self.cache_path}: {e}")

    def is_processed(self, message_id: int) -> bool:
        """Checks if a message ID has already been processed."""
        msg_id_str = str(message_id)
        with self._cache_lock:
            return msg_id_str in self.cache["processed_messages"]

    def add_processed_message(self, message_id: int, note_filename: str, reply_to_id: Optional[int]):
        """Adds a message to the processed cache."""
        msg_id_str = str(message_id)
        with self._cache_lock:
            self.cache["processed_messages"][msg_id_str] = {
                "filename": note_filename,
                "reply_to": reply_to_id
            }
            self.processed_ids.add(msg_id_str)  # Keep the set updated

            if reply_to_id:
                # Store the reply relationship for later linking
                # Key: Parent ID, Value: List of Child IDs
                reply_to_str = str(reply_to_id)
                if reply_to_str not in self.cache["replies"]:
                    self.cache["replies"][reply_to_str] = []
                if msg_id_str not in self.cache["replies"][reply_to_str]:  # Avoid duplicates
                    self.cache["replies"][reply_to_str].append(msg_id_str)

    def get_note_filename(self, message_id: int) -> Optional[str]:
        """Retrieves the note filename for a given message ID."""
        with self._cache_lock:
            return self.cache["processed_messages"].get(str(message_id), {}).get("filename")

    def get_reply_children(self, parent_message_id: int) -> list[str]:
        """Gets the IDs of messages replying to the given parent ID."""
        with self._cache_lock:
            return self.cache["replies"].get(str(parent_message_id), [])

    def get_all_processed_messages(self) -> Dict[str, Any]:
        with self._cache_lock:
            return dict(self.cache.get("processed_messages", {}))  # Return a copy to prevent race conditions

    def get_last_processed_message_id(self) -> Optional[int]:
        """Finds the highest message ID in the cache."""
        with self._cache_lock:
            if not self.cache["processed_messages"]:
                return None
            try:
                return max(int(k) for k in self.cache["processed_messages"].keys())
            except ValueError:
                logger.error("Found non-integer keys in processed_messages cache.")
                return None
