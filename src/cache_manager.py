import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import aiofiles
import ujson

from src.utils import logger


class CacheManager:
    """
    Handles async loading, saving, and updating of the export cache.
    """
    def __init__(self, cache_path: Path):
        """
        Initialize the CacheManager.

        Args:
            cache_path (Path): The path to the cache file.
        """
        self.cache_path = cache_path.resolve()
        self.cache: Dict[str, Any] = {"version": 2, "entities": {}}
        self._lock = asyncio.Lock()
        self._save_task: Optional[asyncio.Task] = None
        self._dirty = False

    async def load_cache(self):
        """
        Asynchronously load the cache from the cache file.

        Args:
            None

        Returns:
            None
        """
        async with self._lock:
            if not self.cache_path.exists():
                return
            try:
                async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                if not content:
                    return

                try:
                    loaded_data = ujson.loads(content)
                except Exception as e:
                    logger.error(f"Cache file has invalid format or could not be parsed: {e}. Starting fresh.")
                    return

                if not isinstance(loaded_data, dict) or "version" not in loaded_data:
                    logger.error("Cache file has invalid format. Starting fresh.")
                    return
                self.cache = loaded_data
                self.cache.setdefault("entities", {})

                for entity_id, data in self.cache["entities"].items():
                    data.setdefault("processed_messages", {})
                    data.setdefault("last_id", None)
                    data.setdefault("title", "Unknown")
                    data.setdefault("type", "unknown")

                logger.info(f"Cache loaded: {len(self.cache['entities'])} entities.")

            except (json.JSONDecodeError, TypeError):
                logger.error("Cache file is corrupt or has invalid format. Starting fresh.")
                self.cache = {"version": 2, "entities": {}}
            except Exception as e:
                logger.error(f"Failed to load cache file: {e}", exc_info=True)
            finally:
                self._dirty = False

    def _get_default_entity_cache(self) -> Dict[str, Any]:
        """
        Get the default structure for an entity cache.

        Args:
            None

        Returns:
            Dict[str, Any]: The default entity cache structure.
        """
        return {"processed_messages": {}, "last_id": None, "title": "Unknown", "type": "unknown"}

    async def save_cache(self):
        """
        Asynchronously save the cache to the cache file if there are changes.

        Args:
            None

        Returns:
            None
        """
        if not self._dirty:
            return
        async with self._lock:
            if not self._dirty:
                return
            try:
                cache_json = ujson.dumps(self.cache, indent=2, ensure_ascii=False)
                temp_path = self.cache_path.with_suffix('.tmp')
                async with aiofiles.open(temp_path, mode='w', encoding='utf-8') as f:
                    await f.write(cache_json)
                os.replace(temp_path, self.cache_path)
                self._dirty = False
            except Exception:
                pass

    async def schedule_background_save(self):
        """
        Schedule a background save of the cache if there are changes.

        Args:
            None

        Returns:
            None
        """
        if not self._dirty or (self._save_task and not self._save_task.done()):
            return
        await asyncio.sleep(0.1)
        if not self._dirty:
            return
        self._save_task = asyncio.create_task(self.save_cache())

    async def _with_entity_data(self, entity_id: Union[str, int], operation: Callable, modify: bool = False):
        """
        Perform an operation on the entity data in a thread-safe manner.

        Args:
            entity_id (Union[str, int]): The entity identifier.
            operation (Callable): The operation to perform on the entity data.
            modify (bool): Whether the operation modifies the data.

        Returns:
            Any: The result of the operation.
        """
        entity_id_str = str(entity_id)
        async with self._lock:
            entity_data = self.cache["entities"].setdefault(entity_id_str, self._get_default_entity_cache())
            result = operation(entity_data)
            if modify:
                self._dirty = True
            return result

    async def is_processed(self, message_id: int, entity_id: Union[str, int]) -> bool:
        """
        Check if a message has already been processed for a given entity.

        Args:
            message_id (int): The message ID.
            entity_id (Union[str, int]): The entity identifier.

        Returns:
            bool: True if processed, False otherwise.
        """
        msg_id_str = str(message_id)
        def check(data):
            return msg_id_str in data.get("processed_messages", {})
        return await self._with_entity_data(entity_id, check) or False

    async def add_processed_message_async(
        self, message_id: int, note_filename: str, reply_to_id: Optional[int],
        entity_id: Union[str, int], title: str, telegram_url: Optional[str]
    ):
        """
        Add a processed message to the cache for a given entity.

        Args:
            message_id (int): The message ID.
            note_filename (str): The filename of the note.
            reply_to_id (Optional[int]): The ID of the message this is replying to.
            entity_id (Union[str, int]): The entity identifier.
            title (str): The title of the message.
            telegram_url (Optional[str]): The Telegram URL of the message.

        Returns:
            None
        """
        msg_id_str = str(message_id)
        def update(data):
            data["processed_messages"][msg_id_str] = {
                "filename": note_filename,
                "reply_to": reply_to_id,
                "title": title,
                "telegram_url": telegram_url,
                "media_files": []
            }
            current_last_id = data.get("last_id")
            if current_last_id is None or message_id > current_last_id:
                data["last_id"] = message_id
        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    async def update_entity_info_async(self, entity_id: Union[str, int], title: str, entity_type: str):
        """
        Update the title and type of an entity in the cache.

        Args:
            entity_id (Union[str, int]): The entity identifier.
            title (str): The title of the entity.
            entity_type (str): The type of the entity.

        Returns:
            None
        """
        def update(data):
            data["title"] = title
            data["type"] = entity_type
        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    async def add_media_file_to_message(self, entity_id: Union[str, int], message_id: int, media_filename: str, media_size: int):
        """
        Add a media file to a processed message for a given entity.

        Args:
            entity_id (Union[str, int]): The entity identifier.
            message_id (int): The message ID.
            media_filename (str): The name of the media file.
            media_size (int): The size of the media file in bytes.

        Returns:
            None
        """
        msg_id_str = str(message_id)
        def update(data):
            entry = data["processed_messages"].get(msg_id_str)
            if entry is not None:
                media_files = entry.setdefault("media_files", [])
                if not any(f["name"] == media_filename and f["size"] == media_size for f in media_files):
                    media_files.append({"name": media_filename, "size": media_size})
        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    async def all_media_files_present(self, entity_id: Union[str, int], message_id: int, media_dir: Path) -> bool:
        """
        Check if all media files for a processed message are present in the specified directory.

        Args:
            entity_id (Union[str, int]): The entity identifier.
            message_id (int): The message ID.
            media_dir (Path): The directory to check for media files.

        Returns:
            bool: True if all media files are present and have the correct size, False otherwise.
        """
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
        """
        Get all processed messages for a given entity.

        Args:
            entity_id (Union[str, int]): The entity identifier.

        Returns:
            Dict[str, Any]: A dictionary of all processed messages for the entity.
        """
        def get_messages(data):
            return dict(data.get("processed_messages", {}))
        return await self._with_entity_data(entity_id, get_messages) or {}

    def get_last_processed_message_id(self, entity_id: Union[str, int]) -> Optional[int]:
        """
        Get the last processed message ID for a given entity.

        Args:
            entity_id (Union[str, int]): The entity identifier.

        Returns:
            Optional[int]: The last processed message ID, or None if not found.
        """
        entity_data = self.cache.get("entities", {}).get(str(entity_id))
        return entity_data.get("last_id") if entity_data else None
