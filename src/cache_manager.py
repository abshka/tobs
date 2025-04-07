import json
from pathlib import Path
from typing import Dict, Any, Optional, Set
from src.utils import logger
import aiofiles

class CacheManager:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: Dict[str, Any] = {"processed_messages": {}, "replies": {}}
        self.processed_ids: Set[int] = set()

    async def load_cache(self):
        """Loads cache data from the JSON file."""
        if not self.cache_path.exists():
            logger.warning(f"Cache file not found at {self.cache_path}. Starting fresh.")
            return

        try:
            async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                content = await f.read()
                if content: # Check if file is not empty
                    self.cache = json.loads(content)
                    # Ensure keys exist
                    self.cache.setdefault("processed_messages", {})
                    self.cache.setdefault("replies", {})
                    # Populate processed_ids set for quick lookups
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
            # Convert set back to list for JSON serialization if needed, though we store dicts
            async with aiofiles.open(self.cache_path, mode='w', encoding='utf-8') as f:
                await f.write(json.dumps(self.cache, indent=4, ensure_ascii=False))
            logger.info(f"Cache saved to {self.cache_path}")
        except Exception as e:
            logger.error(f"Failed to save cache to {self.cache_path}: {e}")

    def is_processed(self, message_id: int) -> bool:
        """Checks if a message ID has already been processed."""
        return str(message_id) in self.cache["processed_messages"]

    def add_processed_message(self, message_id: int, note_filename: str, reply_to_id: Optional[int]):
        """Adds a message to the processed cache."""
        msg_id_str = str(message_id)
        self.cache["processed_messages"][msg_id_str] = {
            "filename": note_filename,
            "reply_to": reply_to_id
        }
        self.processed_ids.add(msg_id_str) # Keep the set updated
        if reply_to_id:
            # Store the reply relationship for later linking
            # Key: Parent ID, Value: List of Child IDs
            reply_to_str = str(reply_to_id)
            if reply_to_str not in self.cache["replies"]:
                self.cache["replies"][reply_to_str] = []
            if msg_id_str not in self.cache["replies"][reply_to_str]: # Avoid duplicates
                 self.cache["replies"][reply_to_str].append(msg_id_str)


    def get_note_filename(self, message_id: int) -> Optional[str]:
        """Retrieves the note filename for a given message ID."""
        return self.cache["processed_messages"].get(str(message_id), {}).get("filename")

    def get_reply_children(self, parent_message_id: int) -> list[str]:
        """Gets the IDs of messages replying to the given parent ID."""
        return self.cache["replies"].get(str(parent_message_id), [])

    def get_all_processed_messages(self) -> Dict[str, Any]:
        return self.cache.get("processed_messages", {})

    def get_last_processed_message_id(self) -> Optional[int]:
        """Finds the highest message ID in the cache."""
        if not self.cache["processed_messages"]:
            return None
        try:
            return max(int(k) for k in self.cache["processed_messages"].keys())
        except ValueError:
            logger.error("Found non-integer keys in processed_messages cache.")
            return None
