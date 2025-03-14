#!/usr/bin/env python3
"""
Telegram Channel Media Downloader
Скрипт для экспорта сообщений и медиа из Telegram-каналов в Obsidian.
"""

import os
import sys
import logging
import asyncio
import time
import argparse
import hashlib
import json
from typing import Dict, List, Set, Optional, Any, Callable
from dataclasses import dataclass
from contextlib import asynccontextmanager

import aiofiles
from telethon import TelegramClient
from telethon.tl.types import Message, DocumentAttributeVideo
from tqdm.asyncio import tqdm
from dotenv import load_dotenv


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("telegram_export.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Конфигурация скрипта."""

    api_id: str
    api_hash: str
    channel: int
    obsidian_path: str
    max_video_size_mb: int = 50
    max_concurrent_downloads: int = 5
    cache_ttl: int = 86400
    skip_processed: bool = True
    batch_size: int = 50
    max_retries: int = 3
    retry_delay: int = 5

    @classmethod
    def from_env(cls) -> "Config":
        """Загрузка конфигурации из переменных окружения."""
        load_dotenv()

        channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
        if channel_id and channel_id.startswith("-100"):
            channel = int(channel_id)
        else:
            channel = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))

        return cls(
            api_id=os.getenv("TELEGRAM_API_ID", ""),
            api_hash=os.getenv("TELEGRAM_API_HASH", ""),
            channel=channel,
            obsidian_path=os.getenv("OBSIDIAN_PATH", ""),
            max_video_size_mb=int(os.getenv("MAX_VIDEO_SIZE_MB", "50")),
            max_concurrent_downloads=int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "5")),
            cache_ttl=int(os.getenv("CACHE_TTL", "86400")),
            skip_processed=os.getenv("SKIP_PROCESSED", "True").lower() == "true",
            batch_size=int(os.getenv("BATCH_SIZE", "50")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_delay=int(os.getenv("RETRY_DELAY", "5")),
        )


class Cache:
    """Управление кэшем обработанных ID сообщений."""

    def __init__(self, cache_file: str, ttl: int):
        self.cache_file = cache_file
        self.ttl = ttl
        self.processed_ids: Set[int] = set()
        self.media_hash_map: Dict[str, str] = {}  # message_id -> media_filename
        self._loaded = False

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

            logger.info(f"Загружено {len(self.processed_ids)} ID сообщений из кэша")
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша: {e}")

        self._loaded = True

    async def save(self) -> None:
        """Асинхронное сохранение кэша в файл."""
        try:
            data = {
                "messages": [(msg_id, time.time()) for msg_id in self.processed_ids],
                "media_hashes": self.media_hash_map,
            }

            async with aiofiles.open(self.cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2))

            logger.info(f"Кэш сохранен: {len(self.processed_ids)} ID сообщений")
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")

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


class MediaProcessor:
    """Класс для обработки медиа-файлов из Telegram."""

    def __init__(self, config: Config, cache: Cache):
        self.config = config
        self.cache = cache
        self.semaphore = asyncio.Semaphore(config.max_concurrent_downloads)
        self.paths = {
            "images": os.path.join(config.obsidian_path, "images"),
            "videos": os.path.join(config.obsidian_path, "videos"),
            "voices": os.path.join(config.obsidian_path, "voices"),
            "documents": os.path.join(config.obsidian_path, "documents"),
            "round_videos": os.path.join(
                config.obsidian_path, "round_videos"
            ),  # Директория для круговых видео
        }

        # Создаём необходимые директории
        for path in self.paths.values():
            os.makedirs(path, exist_ok=True)

    async def retry_operation(self, operation: Callable, *args, **kwargs) -> Any:
        """Выполняет операцию с несколькими попытками."""
        retries = self.config.max_retries
        delay = self.config.retry_delay

        for attempt in range(retries):
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                if attempt < retries - 1:
                    wait_time = delay * (2**attempt)  # Экспоненциальная задержка
                    logger.warning(
                        f"Попытка {attempt + 1}/{retries} не удалась: {e}. "
                        f"Повторная попытка через {wait_time} сек."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise

    @asynccontextmanager
    async def download_semaphore(self):
        """Контекстный менеджер для семафора загрузки."""
        async with self.semaphore:
            yield

    def _is_round_video(self, message: Message) -> bool:
        """Проверяет, является ли сообщение круговым видео."""
        # Проверка для video_note (специальный тип сообщений в Telegram)
        if hasattr(message, "video_note") and message.video_note:
            return True

        # Проверка для документов с атрибутом round_message
        if message.document and hasattr(message.document, "attributes"):
            for attr in message.document.attributes:
                if isinstance(attr, DocumentAttributeVideo) and getattr(
                    attr, "round_message", False
                ):
                    return True
        return False

    async def download_media_item(self, message: Message) -> Optional[str]:
        """Загружает медиа и возвращает markdown-ссылку."""
        # Проверяем кэш, возможно мы уже загружали это медиа
        cached_path = self.cache.get_media_path(message.id)
        if cached_path and os.path.exists(cached_path):
            logger.debug(f"Медиа для сообщения {message.id} найдено в кэше")
            rel_path = os.path.relpath(cached_path, self.config.obsidian_path)
            return self._create_markdown_link(message, rel_path)

        async with self.download_semaphore():
            try:
                # Определяем целевую директорию в зависимости от типа медиа
                if message.photo:
                    target_dir = self.paths["images"]
                elif self._is_round_video(message):
                    target_dir = self.paths["round_videos"]
                elif message.video or (
                    message.document and message.document.mime_type == "video/mp4"
                ):
                    target_dir = self.paths["videos"]
                elif message.voice or (
                    message.document and message.document.mime_type == "audio/ogg"
                ):
                    target_dir = self.paths["voices"]
                elif message.document:
                    target_dir = self.paths["documents"]
                else:
                    return None

                # Создаём уникальное имя файла на основе ID и даты сообщения
                media_hash = hashlib.md5(
                    f"{message.id}_{message.date}".encode()
                ).hexdigest()[:8]

                # Загружаем медиа с повторными попытками
                media_path = await self.retry_operation(
                    message.download_media,
                    file=os.path.join(target_dir, f"{media_hash}_"),
                )

                if not media_path:
                    return None

                # Для видео проверяем размер
                if (
                    message.video
                    or self._is_round_video(message)
                    or (message.document and message.document.mime_type == "video/mp4")
                ):
                    video_size_mb = os.path.getsize(media_path) / (1024 * 1024)
                    if video_size_mb > self.config.max_video_size_mb:
                        logger.info(
                            f"Видео {media_path} имеет размер {video_size_mb:.2f} МБ и превышает "
                            f"порог {self.config.max_video_size_mb} МБ"
                        )
                        os.remove(media_path)
                        return None

                # Кэшируем путь к медиа
                self.cache.add_media_hash(message.id, media_path)

                # Получаем относительный путь для markdown
                rel_path = os.path.relpath(media_path, self.config.obsidian_path)

                return self._create_markdown_link(message, rel_path)

            except Exception as e:
                logger.error(f"Ошибка загрузки медиа для сообщения {message.id}: {e}")
                return None

    def _create_markdown_link(self, message: Message, rel_path: str) -> str:
        """Создаёт markdown-ссылку в зависимости от типа медиа."""
        if message.photo:
            return f"\n\n![]({rel_path})"
        elif self._is_round_video(message):
            return f"\n\n[Видеосообщение]({rel_path})"
        elif message.video or (
            message.document and message.document.mime_type == "video/mp4"
        ):
            return f"\n\n[Видео]({rel_path})"
        elif message.voice or (
            message.document and message.document.mime_type == "audio/ogg"
        ):
            return f"\n\n[Голосовое сообщение]({rel_path})"
        elif message.document:
            filename = message.file.name or "документ"
            return f"\n\n[{filename}]({rel_path})"
        return ""


class MessageProcessor:
    """Класс для обработки сообщений из Telegram."""

    def __init__(self, config: Config, cache: Cache, media_processor: MediaProcessor):
        self.config = config
        self.cache = cache
        self.media_processor = media_processor

    def sanitize_filename(self, name: str) -> str:
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


class TelegramExporter:
    """Основной класс для экспорта данных из Telegram."""

    def __init__(self, config: Config):
        self.config = config
        self.cache_file = os.path.join(
            os.path.dirname(config.obsidian_path), ".telegram_cache.json"
        )
        self.cache = Cache(self.cache_file, config.cache_ttl)
        self.client = TelegramClient("session_name", config.api_id, config.api_hash)
        self.media_processor = MediaProcessor(config, self.cache)
        self.message_processor = MessageProcessor(
            config, self.cache, self.media_processor
        )

    async def run(self, limit: Optional[int] = None):
        """Запуск основного процесса экспорта."""
        await self.cache.load()

        try:
            # Проверка доступа к каналу
            try:
                channel_entity = await self.client.get_entity(self.config.channel)
                logger.info(
                    f"Успешное подключение к каналу: {getattr(channel_entity, 'title', self.config.channel)}"
                )
            except Exception as e:
                logger.error(f"Не удалось получить доступ к каналу: {e}")
                if str(e).find("Cannot find any entity") != -1:
                    logger.info(
                        "Попробуйте добавить префикс '-100' к ID канала, если это приватный канал"
                    )
                return

            # Счетчики для статистики
            stats = {"processed": 0, "skipped": 0, "groups": 0, "files_created": 0}

            # Группировка сообщений
            logger.info("Получение сообщений из канала...")
            groups = {}

            start_time = time.time()
            with tqdm(desc="Загрузка сообщений") as msg_pbar:
                # Ограничение загрузки по количеству сообщений (если указано)
                iter_messages_kwargs = {"limit": limit} if limit else {}

                async for message in self.client.iter_messages(
                    self.config.channel, **iter_messages_kwargs
                ):
                    msg_pbar.update(1)

                    # Пропускаем уже обработанные сообщения
                    if self.config.skip_processed and self.cache.is_processed(
                        message.id
                    ):
                        stats["skipped"] += 1
                        continue

                    self.cache.add_message(message.id)
                    stats["processed"] += 1

                    try:
                        # Группировка сообщений по grouped_id или по одиночным сообщениям
                        if message.grouped_id:
                            groups.setdefault(message.grouped_id, []).append(message)
                        else:
                            groups[message.id] = [message]
                    except Exception as e:
                        logger.error(
                            f"Ошибка группировки сообщения {getattr(message, 'id', 'unknown')}: {e}"
                        )

                    # Обновление прогресс-бара
                    if stats["processed"] % 50 == 0:
                        elapsed = time.time() - start_time
                        rate = stats["processed"] / elapsed if elapsed > 0 else 0
                        msg_pbar.set_postfix(
                            загружено=stats["processed"],
                            групп=len(groups),
                            пропущено=stats["skipped"],
                            скорость=f"{rate:.1f} сообщ/сек",
                        )

            # Итоговая статистика
            logger.info(
                f"Всего загружено {stats['processed']} сообщений (пропущено {stats['skipped']})"
            )
            stats["groups"] = len(groups)
            logger.info(f"Найдено {stats['groups']} постов для обработки")

            # Обработка групп с прогресс-баром
            with tqdm(total=len(groups), desc="Обработка постов") as pbar:
                # Разбиваем обработку на пакеты для лучшего контроля памяти
                group_items = list(groups.items())
                for i in range(0, len(group_items), self.config.batch_size):
                    batch = group_items[i : i + self.config.batch_size]
                    batch_tasks = [
                        self.message_processor.process_message_group(key, msgs, pbar)
                        for key, msgs in batch
                    ]
                    # Запускаем batch_tasks параллельно и ждем их завершения
                    await asyncio.gather(*batch_tasks)

                    # Периодически сохраняем кэш для возможности восстановления после сбоя
                    if i % (self.config.batch_size * 2) == 0 and i > 0:
                        await self.cache.save()

                    # Обновляем статистику для файлов
                    stats["files_created"] = len(batch)

            # Окончательное сохранение кэша
            await self.cache.save()

            # Итоговая статистика
            end_time = time.time()
            duration = end_time - start_time
            logger.info(f"Экспорт завершен за {duration:.1f} секунд")
            logger.info(
                f"Обработано: {stats['processed']} сообщений, "
                f"создано постов: {stats['files_created']}, "
                f"пропущено: {stats['skipped']}"
            )

        except Exception as e:
            logger.error(f"Общая ошибка: {e}")
            # Сохраняем кэш даже при ошибке
            await self.cache.save()


async def main():
    """Точка входа для асинхронного запуска."""
    # Аргументы командной строки
    parser = argparse.ArgumentParser(description="Telegram Channel Media Downloader")
    parser.add_argument("--debug", action="store_true", help="Включить режим отладки")
    parser.add_argument("--skip-cache", action="store_true", help="Игнорировать кэш")
    parser.add_argument("--limit", type=int, help="Ограничить количество сообщений")
    args = parser.parse_args()

    # Настройка логирования в зависимости от режима
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("telethon").setLevel(logging.INFO)

    # Загрузка конфигурации
    config = Config.from_env()

    # Изменяем настройки в соответствии с аргументами командной строки
    if args.skip_cache:
        config.skip_processed = False
        logger.info("Кэш игнорируется, будут обработаны все сообщения")

    # Создаем и запускаем экспортер
    exporter = TelegramExporter(config)

    async with exporter.client:
        await exporter.run(limit=args.limit)


if __name__ == "__main__":
    try:
        # Используем новый event loop для асинхронного запуска
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Операция прервана пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("Работа скрипта завершена")
