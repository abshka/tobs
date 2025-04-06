"""
Модуль для обработки текстовых сообщений из Telegram.
"""

import os
import asyncio
import logging
from typing import List, Optional, Generator
from datetime import datetime

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
        """Обрабатывает группу сообщений и сохраняет в папку года."""
        try:
            # Определяем дату поста (самая ранняя среди сообщений группы)
            # Фильтруем сообщения без даты
            valid_dates_gen: Generator[datetime, None, None] = (m.date for m in messages if m.date)
            try:
                post_date = min(valid_dates_gen)
            except ValueError:
                # Если нет сообщений с датой в группе, пропускаем
                logger.warning(
                    f"Группа {group_key} не содержит сообщений с валидной датой. Пропуск."
                )
                if pbar:
                    pbar.update(1)
                return

            year_str = str(post_date.year)
            date_str = post_date.strftime("%Y-%m-%d")

            # Определяем путь к папке года внутри obsidian_path
            year_dir_path = os.path.join(self.config.obsidian_path, year_str)
            # Создаем папку года, если она не существует
            # Используем синхронный метод, т.к. создание папки - быстрая операция
            # и происходит до основной асинхронной работы с файлами/медиа.
            os.makedirs(year_dir_path, exist_ok=True)

            # Находим первый непустой текст (используем message, как более типичный атрибут)
            post_text = next(
                (m.message.strip() for m in messages if m.message and m.message.strip()), ""
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
                # Устанавливаем текст по умолчанию, если его не было
                post_text = f"Медиа за {date_str}\n"

            # Формируем полный путь к файлу в папке года
            filepath = os.path.join(year_dir_path, filename)

            # Если файл уже существует, пропускаем
            # Используем os.path.exists, т.к. это быстрая проверка перед IO
            if os.path.exists(filepath):
                logger.debug(f"Файл {filepath} уже существует. Пропуск.")
                if pbar:
                    pbar.update(1)
                return

            # Подготовка задач для параллельной загрузки медиа
            media_tasks = [
                self.media_processor.download_media_item(m)
                for m in messages
                if m.media or hasattr(m, "video_note") # Проверяем на наличие медиа
            ]

            # Параллельная загрузка всех медиа
            media_markdown_results = await asyncio.gather(*media_tasks)

            # Запись в файл за один раз с использованием aiofiles
            media_content = "".join([md for md in media_markdown_results if md])
            full_content = post_text + "\n" + media_content if media_content else post_text

            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(full_content.strip() + "\n") # Убираем лишние пробелы в конце и добавляем одну новую строку

            if pbar:
                # Отображаем относительный путь для краткости
                relative_path = os.path.join(year_str, filename)
                pbar.set_postfix(файл=relative_path)
                pbar.update(1)
            logger.info(f"Создан файл: {filepath}")

        except Exception as e:
            logger.error(f"Ошибка обработки группы {group_key}: {e}", exc_info=True)
            if pbar:
                pbar.update(1) # Обновляем счетчик даже при ошибке, чтобы прогресс-бар дошел до конца
