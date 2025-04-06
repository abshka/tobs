"""
Модуль для обработки медиа-файлов из Telegram.
"""

import os
import time
import asyncio
import logging
import hashlib
from typing import Any, Callable, Optional
from contextlib import asynccontextmanager

from telethon.tl.types import Message, DocumentAttributeVideo

from classes.config import Config
from classes.cache import Cache

logger = logging.getLogger(__name__)

class MediaProcessor:
    """Класс для обработки медиа-файлов из Telegram."""

    def __init__(self, config: Config, cache: Cache, optimize_images: bool = True, optimize_videos: bool = True):
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
            "total_size_mb": 0.0,
        }
        self.optimize_images = optimize_images
        self.optimize_videos = optimize_videos

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
                    wait_time = delay * (2 ** attempt)  # Экспоненциальная задержка
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

    @staticmethod
    def _is_round_video(message: Message) -> bool:
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

    @staticmethod
    def _get_file_size_mb(message: Message) -> float:
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

    @staticmethod
    async def _optimize_image(image_path: str) -> None:
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
                    img = img.resize(new_size, Image.Resampling.LANCZOS)

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

    @staticmethod
    async def _optimize_video(video_path: str) -> str:
        """Оптимизирует видео для уменьшения размера."""
        try:
            import subprocess
            import os

            # Проверяем размер файла перед оптимизацией
            original_size = os.path.getsize(video_path)

            # Определяем путь для сжатого файла
            compressed_path = video_path.replace(".mp4", "_compressed.mp4")

            # Используем ffmpeg для сжатия видео
            command = [
                "ffmpeg",
                "-i", video_path,
                "-vcodec", "libx264",
                "-crf", "28",
                "-preset", "slow",
                compressed_path
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.warning(f"Ошибка сжатия видео {video_path}: {stderr.decode()}")
                return video_path

            # Проверяем результат
            new_size = os.path.getsize(compressed_path)
            saved_percent = (1 - new_size / original_size) * 100
            if saved_percent > 5:  # Если сэкономили больше 5%
                logger.debug(
                    f"Сжатие видео {video_path}: {saved_percent:.1f}% экономии"
                )

            # Удаляем оригинальный файл
            os.remove(video_path)

            return compressed_path

        except ImportError:
            logger.warning("ffmpeg не установлен, сжатие видео пропущено")
            return video_path
        except Exception as e:
            logger.warning(f"Ошибка сжатия видео {video_path}: {e}")
            return video_path

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

                    # Оптимизируем видео если это видео
                    if message.video or (message.document and message.document.mime_type == "video/mp4"):
                        media_path = await self._optimize_video(media_path)

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
        return f"\n\n![]({rel_path})"
