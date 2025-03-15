"""
Модуль для работы с кэшем обработанных сообщений.
"""

import os
import json
import time
import logging
from typing import Set, Dict, Any, Optional

import aiofiles

logger = logging.getLogger(__name__)


class Cache:
    """Управление кэшем обработанных ID сообщений."""

    def __init__(self, cache_file: str, ttl: int):
        self.cache_file = cache_file
        self.ttl = ttl
        self.processed_ids: Set[int] = set()
        self.media_hash_map: Dict[str, str] = {}  # message_id -> media_filename
        self._loaded = False
        self.last_position: Optional[int] = None
        self.resume_data: Dict[str, Any] = {}

    async def load(self) -> None:
        """Асинхронная загрузка кэша из файла."""
        if self._loaded:
            return

        if not os.path.exists(self.cache_file):
            self._loaded = True
            return

        try:
            async with aiofiles.open(self.cache_file, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

                # Загрузка сообщений с учётом времени жизни
                current_time = time.time()
                for entry in data.get("messages", []):
                    msg_id, timestamp = entry
                    if current_time - float(timestamp) < self.ttl:
                        self.processed_ids.add(int(msg_id))

                # Загрузка карты хешей медиа
                self.media_hash_map = data.get("media_hashes", {})

                # Загрузка данных для возобновления
                self.resume_data = data.get("resume_data", {})
                self.last_position = self.resume_data.get("last_position")

            logger.info(f"Загружено {len(self.processed_ids)} ID сообщений из кэша")
            if self.last_position:
                logger.info(
                    f"Найдена точка возобновления: сообщение #{self.last_position}"
                )
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша: {e}")

        self._loaded = True

    async def save(self) -> None:
        """Асинхронное сохранение кэша в файл."""
        try:
            data = {
                "messages": [(msg_id, time.time()) for msg_id in self.processed_ids],
                "media_hashes": self.media_hash_map,
                "resume_data": self.resume_data,
            }

            async with aiofiles.open(self.cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2))

            logger.info(f"Кэш сохранен: {len(self.processed_ids)} ID сообщений")
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")

    def save_resume_point(self, message_id: int) -> None:
        """Сохраняет текущую позицию для возможности возобновления."""
        self.last_position = message_id
        self.resume_data["last_position"] = message_id
        self.resume_data["timestamp"] = time.time()

    def get_resume_point(self) -> Optional[int]:
        """Возвращает ID сообщения для возобновления загрузки."""
        return self.last_position

    def add_message(self, msg_id: int) -> None:
        """Добавление ID сообщения в кэш."""
        self.processed_ids.add(msg_id)

    def is_processed(self, msg_id: int) -> bool:
        """Проверка, обработано ли сообщение."""
        return msg_id in self.processed_ids

    def add_media_hash(self, msg_id: int, media_path: str) -> None:
        """Добавление соответствия ID сообщения и пути к медиа."""
        self.media_hash_map[str(msg_id)] = media_path

    def get_media_path(self, msg_id: int) -> Optional[str]:
        """Получение пути к медиа по ID сообщения."""
        return self.media_hash_map.get(str(msg_id))
