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
    rate_limit_pause: float = 0.5  # Пауза между загрузками в секундах
    flood_wait_multiplier: float = 1.5  # Множитель времени ожидания при flood wait

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


class MediaProcessor:
    """Класс для обработки медиа-файлов из Telegram."""

    def __init__(self, config: Config, cache: Cache, optimize_images: bool = False):
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

        self.last_download_time = 0
        self.rate_limit_delay = config.rate_limit_pause
        self.download_stats = {
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "total_size_mb": 0,
        }
        self.optimize_images = optimize_images

        # Создаём необходимые директории
        for path in self.paths.values():
            os.makedirs(path, exist_ok=True)

    async def retry_operation(self, operation: Callable, *args, **kwargs) -> Any:
        """Выполняет операцию с несколькими попытками."""
        retries = self.config.max_retries
        delay = self.config.retry_delay

        for attempt in range(retries):
            try:
                # Соблюдаем паузу между запросами для избежания FloodWait
                current_time = time.time()
                time_since_last = current_time - self.last_download_time
                if time_since_last < self.rate_limit_delay:
                    await asyncio.sleep(self.rate_limit_delay - time_since_last)

                self.last_download_time = time.time()
                return await operation(*args, **kwargs)
            except Exception as e:
                if "flood wait" in str(e).lower():
                    # Специальная обработка для FloodWait от Telegram
                    import re

                    wait_time = 5  # По умолчанию 5 секунд
                    match = re.search(r"(\d+) seconds", str(e))
                    if match:
                        wait_time = (
                            int(match.group(1)) * self.config.flood_wait_multiplier
                        )

                    logger.warning(
                        f"Получен FloodWait от Telegram. Ожидание {wait_time:.1f} секунд."
                    )
                    await asyncio.sleep(wait_time)
                    continue

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

    def _get_file_size_mb(self, message: Message) -> float:
        """Получает размер файла в мегабайтах до его загрузки."""
        try:
            if message.video:
                return message.video.size / (1024 * 1024)
            elif hasattr(message, "video_note") and message.video_note:
                return message.video_note.size / (1024 * 1024)
            elif message.document:
                return message.document.size / (1024 * 1024)
            elif message.photo:
                # Для фото размер обычно небольшой, берем максимальное значение
                return 5  # Предполагаем, что фото весит не более 5 МБ
            else:
                return 0
        except AttributeError:
            # Если не удалось получить размер, возвращаем 0
            logger.warning(
                f"Не удалось определить размер медиа в сообщении {message.id}"
            )
            return 0

    async def _optimize_image(self, image_path: str) -> None:
        """Оптимизирует изображение для уменьшения размера."""
        try:
            from PIL import Image
            import os

            # Проверяем размер файла перед оптимизацией
            original_size = os.path.getsize(image_path)

            # Открываем и пересохраняем с оптимизацией
            with Image.open(image_path) as img:
                # Сохраняем EXIF и другие метаданные
                exif = img.info.get("exif", None)

                # Определяем максимальное разрешение (например, 1920x1080)
                max_width = 1920
                max_height = 1080

                # Изменяем размер только если изображение больше максимального
                if img.width > max_width or img.height > max_height:
                    ratio = min(max_width / img.width, max_height / img.height)
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    img = img.resize(new_size, Image.LANCZOS)

                # Сохраняем с оптимизацией
                kwargs = {
                    "optimize": True,
                    "quality": 85,
                }  # 85% качества обычно достаточно
                if exif:
                    kwargs["exif"] = exif

                img.save(image_path, **kwargs)

            # Проверяем результат
            new_size = os.path.getsize(image_path)
            saved_percent = (1 - new_size / original_size) * 100
            if saved_percent > 5:  # Если сэкономили больше 5%
                logger.debug(
                    f"Оптимизация изображения {image_path}: {saved_percent:.1f}% экономии"
                )

        except ImportError:
            logger.warning("Pillow не установлен, оптимизация изображений пропущена")
        except Exception as e:
            logger.warning(f"Ошибка оптимизации изображения {image_path}: {e}")

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
                    logger.info(f"Обнаружено круговое видео в сообщении {message.id}")
                elif message.video or (
                    message.document and message.document.mime_type == "video/mp4"
                ):
                    target_dir = self.paths["videos"]
                    # Проверяем размер видео перед загрузкой
                    file_size_mb = self._get_file_size_mb(message)
                    if file_size_mb > self.config.max_video_size_mb:
                        logger.info(
                            f"Видео в сообщении {message.id} имеет размер {file_size_mb:.2f} МБ и превышает "
                            f"порог {self.config.max_video_size_mb} МБ. Загрузка пропущена."
                        )
                        return f"\n\n> [!note] Видео пропущено\n> Размер видео ({file_size_mb:.2f} МБ) превышает допустимый порог ({self.config.max_video_size_mb} МБ)"
                elif message.voice or (
                    message.document and message.document.mime_type == "audio/ogg"
                ):
                    target_dir = self.paths["voices"]
                elif message.document:
                    target_dir = self.paths["documents"]
                    # Для документов тоже проверяем размер
                    file_size_mb = self._get_file_size_mb(message)
                    if file_size_mb > self.config.max_video_size_mb:
                        logger.info(
                            f"Документ в сообщении {message.id} имеет размер {file_size_mb:.2f} МБ и превышает "
                            f"порог {self.config.max_video_size_mb} МБ. Загрузка пропущена."
                        )
                        filename = message.file.name or "документ"
                        return f"\n\n> [!note] Файл {filename} пропущен\n> Размер файла ({file_size_mb:.2f} МБ) превышает допустимый порог ({self.config.max_video_size_mb} МБ)"
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

                if media_path:
                    # Обновляем статистику загрузок
                    actual_size_mb = os.path.getsize(media_path) / (1024 * 1024)
                    self.download_stats["success"] += 1
                    self.download_stats["total_size_mb"] += actual_size_mb

                    if self.download_stats["success"] % 10 == 0:
                        logger.info(
                            f"Прогресс загрузки: {self.download_stats['success']} успешно, "
                            f"{self.download_stats['failed']} ошибок, "
                            f"{self.download_stats['skipped']} пропущено, "
                            f"общий размер: {self.download_stats['total_size_mb']:.1f} МБ"
                        )

                    # Оптимизируем изображения если включен соответствующий режим
                    if self.optimize_images and message.photo:
                        await self._optimize_image(media_path)

                # Проверка фактического размера после загрузки (может быть полезно в случаях, когда размер определен неверно)
                actual_size_mb = os.path.getsize(media_path) / (1024 * 1024)
                if (
                    actual_size_mb > self.config.max_video_size_mb * 1.1
                ):  # 10% погрешность
                    logger.info(
                        f"Файл {media_path} имеет фактический размер {actual_size_mb:.2f} МБ, что превышает "
                        f"порог {self.config.max_video_size_mb} МБ. Предварительно оценённый размер: {self._get_file_size_mb(message):.2f} МБ"
                    )
                    os.remove(media_path)
                    return f"\n\n> [!warning] Файл удалён после загрузки\n> Фактический размер ({actual_size_mb:.2f} МБ) превышает допустимый порог ({self.config.max_video_size_mb} МБ)"

                # Сохраняем путь в кэш
                self.cache.add_media_hash(message.id, media_path)

                # Возвращаем относительный путь для markdown
                rel_path = os.path.relpath(media_path, self.config.obsidian_path)
                return self._create_markdown_link(message, rel_path)

            except Exception as e:
                logger.error(f"Ошибка загрузки медиа для сообщения {message.id}: {e}")
                self.download_stats["failed"] += 1
                return None

    def _create_markdown_link(self, message: Message, rel_path: str) -> str:
        """Создаёт markdown-ссылку в зависимости от типа медиа."""
        if message.photo:
            return f"\n\n![]({rel_path})"
        elif self._is_round_video(message):
            return f"\n\n![]({rel_path})"
        elif message.video or (
            message.document and message.document.mime_type == "video/mp4"
        ):
            return f"\n\n![]({rel_path})"
        elif message.voice or (
            message.document and message.document.mime_type == "audio/ogg"
        ):
            return f"\n\n![]({rel_path})"
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
        self.memory_usage_warning_issued = False

    async def _check_resources(self):
        """Проверяет доступные системные ресурсы перед запуском."""
        # Проверка дискового пространства
        try:
            import shutil

            if os.path.exists(self.config.obsidian_path):
                free_space_bytes = shutil.disk_usage(self.config.obsidian_path).free
                free_space_mb = free_space_bytes / (1024 * 1024)

                if free_space_mb < 200:  # Проверка, что есть хотя бы 200 МБ места
                    logger.warning(
                        f"Внимание! Мало свободного места: {free_space_mb:.1f} МБ"
                    )
                    return False

            # Проверка доступности памяти
            try:
                import psutil

                memory_available_mb = psutil.virtual_memory().available / (1024 * 1024)
                if memory_available_mb < 100:  # Меньше 100 МБ
                    logger.warning(
                        f"Внимание! Мало доступной памяти: {memory_available_mb:.1f} МБ"
                    )
                    return False
            except ImportError:
                logger.debug("Модуль psutil не установлен, пропуск проверки памяти")

            return True
        except Exception as e:
            logger.warning(f"Не удалось проверить системные ресурсы: {e}")
            return True  # В случае ошибки продолжаем выполнение

    async def _monitor_memory_usage(self):
        """Периодически проверяет использование памяти."""
        try:
            import psutil
            import gc

            while True:
                await asyncio.sleep(30)  # Проверка каждые 30 секунд

                memory_usage_percent = psutil.virtual_memory().percent
                if memory_usage_percent > 80 and not self.memory_usage_warning_issued:
                    logger.warning(
                        f"Высокое использование памяти: {memory_usage_percent}%"
                    )
                    self.memory_usage_warning_issued = True

                    # Принудительный сбор мусора
                    collected = gc.collect()
                    logger.debug(f"Выполнен сбор мусора, собрано {collected} объектов")

                if memory_usage_percent < 70 and self.memory_usage_warning_issued:
                    self.memory_usage_warning_issued = False

        except ImportError:
            logger.debug("Модуль psutil не установлен, мониторинг памяти отключен")
        except Exception as e:
            logger.error(f"Ошибка мониторинга памяти: {e}")

    async def _confirm_continue(self, prompt: str) -> bool:
        """Запрашивает подтверждение пользователя для продолжения."""
        user_input = input(f"{prompt} (y/n): ").strip().lower()
        return user_input == "y"

    async def run(self, limit: Optional[int] = None, resume_from: Optional[int] = None):
        """Запуск основного процесса экспорта."""
        await self.cache.load()

        # Запуск мониторинга ресурсов в фоновом режиме
        resource_check = await self._check_resources()
        if not resource_check:
            if not await self._confirm_continue(
                "Обнаружена нехватка системных ресурсов. Продолжить?"
            ):
                logger.info("Операция отменена пользователем из-за нехватки ресурсов")
                return

        # Запуск мониторинга памяти в фоновом режиме
        memory_task = asyncio.create_task(self._monitor_memory_usage())

        try:
            logger.info("Получение сообщений из канала...")
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
            elapsed = time.time() - start_time
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            logger.info(
                f"Всего загружено {stats['processed']} сообщений (пропущено {stats['skipped']})"
            )
            stats["groups"] = len(groups)
            logger.info(f"Найдено {stats['groups']} постов для обработки")

            # Работаем с группами для экономии памяти
            with tqdm(total=len(groups), desc="Обработка постов") as pbar:
                group_items = list(groups.items())
                for i in range(0, len(group_items), self.config.batch_size):
                    batch = group_items[i : i + self.config.batch_size]
                    batch_tasks = [
                        self.message_processor.process_message_group(key, msgs, pbar)
                        for key, msgs in batch
                    ]
                    # Запускаем batch_tasks параллельно и ждем их завершения
                    await asyncio.gather(*batch_tasks)

                    # Очистка обработанных групп для экономии памяти
                    for key, _ in batch:
                        if key in groups:
                            del groups[key]

                    # Принудительный сбор мусора каждые несколько пакетов
                    if i % (self.config.batch_size * 5) == 0 and i > 0:
                        import gc

                        gc.collect()

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

        finally:
            # Отменяем задачу мониторинга памяти
            if "memory_task" in locals() and not memory_task.done():
                memory_task.cancel()
                try:
                    await memory_task
                except asyncio.CancelledError:
                    pass


class InteractiveMenu:
    """Класс для интерактивного меню настройки скрипта."""

    def __init__(self, config: Config):
        self.config = config

    async def run(self) -> Config:
        """Запускает интерактивное меню и возвращает обновленную конфигурацию."""
        print("\n===== Telegram-Obsidian Exporter - Интерактивное меню =====\n")

        # Проверка и настройка API Telegram
        if not self.config.api_id or not self.config.api_hash:
            print("Необходимо указать API_ID и API_HASH для работы с Telegram API.")
            self.config.api_id = input("Введите ваш API_ID: ").strip()
            self.config.api_hash = input("Введите ваш API_HASH: ").strip()

        # Настройка канала
        print("\n--- Настройка источника данных ---")
        current_channel = (
            self.config.channel if self.config.channel != 0 else "не указан"
        )
        print(f"Текущий канал: {current_channel}")
        change_channel = (
            input("Хотите изменить ID канала? (y/n): ").strip().lower() == "y"
        )

        if change_channel:
            channel_input = input(
                "Введите ID канала (добавьте префикс -100 для приватных каналов): "
            ).strip()
            try:
                self.config.channel = int(channel_input)
            except ValueError:
                print("Ошибка: ID канала должен быть числом.")
                return self.config

        # Настройка пути к Obsidian
        print("\n--- Настройка пути к Obsidian ---")
        current_path = (
            self.config.obsidian_path if self.config.obsidian_path else "не указан"
        )
        print(f"Текущий путь: {current_path}")
        change_path = (
            input("Хотите изменить путь к Obsidian? (y/n): ").strip().lower() == "y"
        )

        if change_path:
            path_input = input("Введите путь к директории Obsidian: ").strip()
            if os.path.exists(path_input):
                self.config.obsidian_path = path_input
            else:
                create_dir = (
                    input(f"Директория {path_input} не существует. Создать? (y/n): ")
                    .strip()
                    .lower()
                    == "y"
                )
                if create_dir:
                    try:
                        os.makedirs(path_input, exist_ok=True)
                        self.config.obsidian_path = path_input
                    except Exception as e:
                        print(f"Ошибка при создании директории: {e}")
                        return self.config
                else:
                    print("Путь к Obsidian не изменен.")

        # Настройка параметров загрузки
        print("\n--- Настройка параметров загрузки ---")
        try:
            max_size_input = input(
                f"Максимальный размер видео в МБ (текущий: {self.config.max_video_size_mb}): "
            ).strip()
            if max_size_input:
                self.config.max_video_size_mb = int(max_size_input)

            concurrent_downloads = input(
                f"Количество одновременных загрузок (текущее: {self.config.max_concurrent_downloads}): "
            ).strip()
            if concurrent_downloads:
                self.config.max_concurrent_downloads = int(concurrent_downloads)
        except ValueError:
            print("Ошибка: параметры должны быть целыми числами.")

        # Настройка обработки
        print("\n--- Настройка обработки ---")
        skip_processed = (
            input(
                f"Пропускать обработанные сообщения? (y/n, текущее: {'y' if self.config.skip_processed else 'n'}): "
            )
            .strip()
            .lower()
        )
        if skip_processed in ["y", "n"]:
            self.config.skip_processed = skip_processed == "y"

        print("\nНастройка завершена. Запуск экспорта...")
        return self.config


async def main():
    """Точка входа для асинхронного запуска."""
    # Аргументы командной строки
    parser = argparse.ArgumentParser(description="Telegram Channel Media Downloader")
    parser.add_argument("--debug", action="store_true", help="Включить режим отладки")
    parser.add_argument("--skip-cache", action="store_true", help="Игнорировать кэш")
    parser.add_argument("--limit", type=int, help="Ограничить количество сообщений")
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Запустить в интерактивном режиме",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Возобновить с последней сохраненной позиции",
    )
    parser.add_argument(
        "--optimize-images",
        action="store_true",
        help="Оптимизировать загруженные изображения для уменьшения размера",
    )
    args = parser.parse_args()

    # Загрузка конфигурации
    config = Config.from_env()

    # Настройка логирования в зависимости от режима
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("telethon").setLevel(logging.INFO)

    if args.skip_cache:
        config.skip_processed = False
        logger.info("Кэш игнорируется, будут обработаны все сообщения")

    # Запуск интерактивного меню, если указан соответствующий флаг
    if args.interactive:
        menu = InteractiveMenu(config)
        config = await menu.run()

    # Создаем и запускаем экспортер
    exporter = TelegramExporter(config)

    if args.optimize_images:
        exporter.media_processor = MediaProcessor(
            config, exporter.cache, optimize_images=True
        )

    async with exporter.client:
        # Проверяем опцию возобновления
        if args.resume and exporter.cache.get_resume_point():
            print(
                f"Возобновление с последней сохраненной позиции: сообщение #{exporter.cache.get_resume_point()}"
            )
            await exporter.run(
                limit=args.limit, resume_from=exporter.cache.get_resume_point()
            )
        else:
            await exporter.run(limit=args.limit)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Операция прервана пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("Работа скрипта завершена")
