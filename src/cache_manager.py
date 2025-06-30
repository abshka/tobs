"""
CacheManager: Handles async loading,  log, and updating of the export cache.
"""

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
    """
    Handles async loading, saving, and updating of the export cache.
    """
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path.resolve()
        self.cache: Dict[str, Any] = {"version": 2, "entities": {}}
        self._lock = asyncio.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="CacheThread")
        self._save_task: Optional[asyncio.Task] = None
        self._dirty = False
        logger.info(f"Cache Manager initialized. Cache file: {self.cache_path}")

    async def load_cache(self):
        """
        Asynchronously loads cache data from a JSON file.
        """
        async with self._lock:
            if not self.cache_path.exists():
                logger.warning(f"Cache file not found at {self.cache_path}. Starting fresh.")
                return
            try:
                async with aiofiles.open(self.cache_path, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                if not content:
                    logger.warning(f"Cache file {self.cache_path} is empty.")
                    return

                loop = asyncio.get_running_loop()
                loaded_data = await loop.run_in_executor(self._pool, json.loads, content)

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
        Returns the default structure for a new entity in the cache.
        """
        return {"processed_messages": {}, "last_id": None, "title": "Unknown", "type": "unknown"}

    async def save_cache(self):
        """
        Saves the current cache state to a JSON file if it has changed.
        """
        if not self._dirty:
            return
        async with self._lock:
            if not self._dirty:
                return
            try:
                # logger.info(f"Saving cache to {self.cache_path}...")
                loop = asyncio.get_running_loop()
                cache_json = await loop.run_in_executor(
                    self._pool, partial(json.dumps, self.cache, indent=2, ensure_ascii=False)
                )
                temp_path = self.cache_path.with_suffix('.tmp')
                async with aiofiles.open(temp_path, mode='w', encoding='utf-8') as f:
                    await f.write(cache_json)
                await loop.run_in_executor(self._pool, os.replace, temp_path, self.cache_path)
                # logger.info("Cache saved successfully.")
                self._dirty = False
            except Exception as e:
                logger.error(f"Failed to save cache: {e}", exc_info=True)

    async def schedule_background_save(self):
        """
        Schedules a cache save operation if needed and not already running.
        """
        if not self._dirty or (self._save_task and not self._save_task.done()):
            return
        await asyncio.sleep(0.1)
        if not self._dirty:
            return
        self._save_task = asyncio.create_task(self.save_cache())

    async def _with_entity_data(self, entity_id: Union[str, int], operation: Callable, modify: bool = False):
        """
        Performs an operation on entity data with locking.
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
        Checks if a message ID has been processed for the given entity.
        """
        msg_id_str = str(message_id)
        def check(data):
            return msg_id_str in data.get("processed_messages", {})
        return await self._with_entity_data(entity_id, check) or False

    async def add_processed_message_async(
        self, message_id: int, note_filename: str, reply_to_id: Optional[int],
        entity_id: Union[str, int], title: str, telegram_url: Optional[str]
    ):
        """Добавляет обработанное сообщение в кэш для указанной сущности."""
        msg_id_str = str(message_id)
        def update(data):
            data["processed_messages"][msg_id_str] = {
                "filename": note_filename,
                "reply_to": reply_to_id,
                "title": title,
                "telegram_url": telegram_url,
                # Новый ключ: список медиафайлов (имя и размер)
                "media_files": []
            }
            current_last_id = data.get("last_id")
            if current_last_id is None or message_id > current_last_id:
                data["last_id"] = message_id
        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    async def update_entity_info_async(self, entity_id: Union[str, int], title: str, entity_type: str):
        """Обновляет информацию о сущности (заголовок, тип) в кэше."""
        def update(data):
            data["title"] = title
            data["type"] = entity_type
        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    async def add_media_file_to_message(self, entity_id: Union[str, int], message_id: int, media_filename: str, media_size: int):
        """
        Добавляет информацию о скачанном медиафайле к сообщению в кэше.
        """
        msg_id_str = str(message_id)
        def update(data):
            entry = data["processed_messages"].get(msg_id_str)
            if entry is not None:
                media_files = entry.setdefault("media_files", [])
                # Не добавлять дубликаты
                if not any(f["name"] == media_filename and f["size"] == media_size for f in media_files):
                    media_files.append({"name": media_filename, "size": media_size})
        await self._with_entity_data(entity_id, update, modify=True)
        await self.schedule_background_save()

    async def all_media_files_present(self, entity_id: Union[str, int], message_id: int, media_dir: Path) -> bool:
        """
        Проверяет, что все медиафайлы для сообщения реально существуют и соответствуют размеру.
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
        """Получает все обработанные сообщения для указанной сущности."""
        def get_messages(data):
            return dict(data.get("processed_messages", {}))
        return await self._with_entity_data(entity_id, get_messages) or {}

    def get_last_processed_message_id(self, entity_id: Union[str, int]) -> Optional[int]:
        """Находит самый большой ID обработанного сообщения для сущности."""
        entity_data = self.cache.get("entities", {}).get(str(entity_id))
        return entity_data.get("last_id") if entity_data else None
