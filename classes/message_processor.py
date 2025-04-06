"""
Модуль для обработки текстовых сообщений из Telegram.
"""

import os
import asyncio
import logging
from typing import List, Optional

import aiofiles
from telethon.tl.types import Message
from tqdm.asyncio import tqdm

from classes.config import Config
from classes.cache import Cache
from classes.media_processor import MediaProcessor

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Класс для обработки сообщений из Telegram."""

    def __init__(self, config: Config, cache: Cache, media_processor: MediaProcessor):
        self.config = config
        self.cache = cache
        self.media_processor = media_processor

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """Удаляет символы, недопустимые в именах файлов."""
        invalid_chars = '<>:"/\\|?*'
        sanitized = "".join(c for c in name if c not in invalid_chars)
        return sanitized.replace("\n", " ").strip()

    async def process_message_group(
            self, group_key: int, messages: List[Message], pbar: Optional[tqdm] = None
    ) -> None:
        """Обрабатывает группу сообщений."""
        try:
            # Определяем дату поста (самая ранняя среди сообщений группы)
            post_date = min(m.date for m in messages)
            date_str = post_date.strftime("%Y-%m-%d")

            # Находим первый непустой текст
            post_text = next(
                (m.text.strip() for m in messages if m.text and m.text.strip()), ""
            )
            # Формируем имя файла
            if post_text:
                first_line = post_text.splitlines()[0].strip()
                safe_first_line = self.sanitize_filename(first_line)[
                                  :50
                                  ]  # Увеличили лимит до 50 символов
                filename = f"{date_str}.{safe_first_line}.md"
            else:
                filename = f"{date_str}.media_only.md"
                post_text = f"Медиа за {date_str}\n"

            filepath = os.path.join(self.config.obsidian_path, filename)

            # Если файл уже существует, пропускаем
            if os.path.exists(filepath):
                if pbar:
                    pbar.update(1)
                return

            # Подготовка задач для параллельной загрузки медиа
            media_tasks = [
                self.media_processor.download_media_item(m)
                for m in messages
                if m.media or hasattr(m, "video_note")
            ]

            # Параллельная загрузка всех медиа
            media_markdown = await asyncio.gather(*media_tasks)

            # Запись в файл за один раз с использованием aiofiles
            media_content = "".join([md for md in media_markdown if md])
            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(post_text + media_content)

            if pbar:
                pbar.set_postfix(файл=os.path.basename(filename))
                pbar.update(1)
        except Exception as e:
            logger.error(f"Ошибка обработки группы {group_key}: {e}")
            if pbar:
                pbar.update(1)
