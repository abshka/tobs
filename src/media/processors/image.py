"""
Image processor for media files.

Handles image file processing including resizing, format conversion,
EXIF rotation, and quality optimization using PIL/Pillow.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import aiofiles
from loguru import logger
from PIL import Image, ImageOps

from ..models import MediaMetadata, ProcessingSettings, ProcessingTask
from .base import BaseProcessor

# Environment variables for processing control (fallback values)
DEFAULT_PROCESS_IMAGES = os.getenv("PROCESS_IMAGES", "true").lower() == "true"


class ImageProcessor(BaseProcessor):
    """Процессор для изображений с оптимизацией и изменением размера."""

    def __init__(
        self,
        thread_pool: Any,  # UnifiedThreadPool instance
        config: Optional[Any] = None,
        settings: Optional[ProcessingSettings] = None,
    ):
        """
        Инициализация процессора изображений.

        Args:
            thread_pool: Unified thread pool для всех операций
            config: Объект конфигурации
            settings: Настройки обработки по умолчанию
        """
        super().__init__(thread_pool, settings)  # 🧵 TIER B - B-1
        self.config = config

        # Статистика
        self._image_processed_count = 0
        self._image_copied_count = 0

    async def process(self, task: ProcessingTask, worker_name: str) -> bool:
        """
        Обработка изображения с fallback на копирование.

        Args:
            task: Задача обработки с input/output путями
            worker_name: Имя воркера для логирования

        Returns:
            True если обработка успешна, False иначе
        """
        try:
            settings = task.processing_settings or self.settings
            metadata = task.metadata

            # Определение нужности обработки
            needs_proc = self.needs_processing_with_metadata(metadata, settings)

            if not needs_proc:
                logger.debug(
                    f"Image doesn't need processing, copying: {task.input_path}"
                )
                return await self._copy_file(task)

            # Обработка изображения в отдельном потоке
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.cpu_executor,
                self._process_image_sync,
                task,
                settings,
            )

            if success and task.output_path.exists():
                logger.debug(
                    f"✅ Image processing completed: {task.input_path} -> {task.output_path}"
                )
                self._image_processed_count += 1
                return True
            else:
                logger.debug(f"Using fallback copy for image {task.input_path}")
                return await self._copy_file(task)

        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            return await self._copy_file(task)

    def needs_processing(self, file_path: Path, settings: ProcessingSettings) -> bool:
        """
        Определяет, нужна ли обработка изображения (базовая проверка).

        Args:
            file_path: Путь к изображению
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        try:
            if not file_path.exists():
                return False

            # Базовая проверка: оптимизируем файлы больше 5MB
            file_size = file_path.stat().st_size
            if file_size > 5 * 1024 * 1024:
                return True

            return False
        except Exception:
            return False

    def needs_processing_with_metadata(
        self, metadata: Optional[MediaMetadata], settings: ProcessingSettings
    ) -> bool:
        """
        Определение необходимости обработки изображения с учетом метаданных.

        Args:
            metadata: Метаданные изображения
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        # Проверяем настройки из config или переменную окружения
        process_images = getattr(self.config, "process_images", DEFAULT_PROCESS_IMAGES)
        if not process_images:
            logger.debug(
                "Image processing disabled via config or PROCESS_IMAGES environment variable"
            )
            return False

        if not metadata:
            return False  # Если нет метаданных, не рискуем

        # Не обрабатываем слишком маленькие файлы
        if metadata.file_size < 1024:  # меньше 1KB
            return False

        # Проверка размера - только если значительно превышает (1.5x threshold)
        if (
            metadata.width
            and metadata.height
            and (
                metadata.width > settings.image_max_size[0] * 1.5
                or metadata.height > settings.image_max_size[1] * 1.5
            )
        ):
            logger.debug(
                f"Image size {metadata.width}x{metadata.height} exceeds "
                f"max {settings.image_max_size[0]}x{settings.image_max_size[1]}"
            )
            return True

        # Проверка размера файла - оптимизируем только файлы больше 5MB
        if metadata.file_size > 5 * 1024 * 1024:
            logger.debug(
                f"Image file size {metadata.file_size / 1024 / 1024:.1f}MB exceeds 5MB threshold"
            )
            return True

        return False

    def _process_image_sync(
        self,
        task: ProcessingTask,
        settings: ProcessingSettings,
    ) -> bool:
        """
        Синхронная обработка изображения через PIL (для executor).

        Args:
            task: Задача обработки
            settings: Настройки обработки

        Returns:
            True если обработка успешна
        """
        try:
            # Валидация входного файла
            if not task.input_path.exists():
                logger.error(f"Input image file does not exist: {task.input_path}")
                return False

            file_size = task.input_path.stat().st_size
            if file_size == 0:
                logger.error(f"Input image file is empty: {task.input_path}")
                return False

            logger.debug(
                f"Processing image file: {task.input_path} (size: {file_size} bytes)"
            )

            # Открытие и обработка изображения
            with Image.open(task.input_path) as img:
                # Поворот по EXIF данным (автоповорот)
                img = ImageOps.exif_transpose(img)

                # Изменение размера если нужно
                if (
                    img.width > settings.image_max_size[0]
                    or img.height > settings.image_max_size[1]
                ):
                    logger.debug(
                        f"Resizing image from {img.width}x{img.height} "
                        f"to max {settings.image_max_size[0]}x{settings.image_max_size[1]}"
                    )
                    img.thumbnail(settings.image_max_size, Image.Resampling.LANCZOS)

                # Конвертация в RGB если нужно (для JPEG)
                if img.mode in ("RGBA", "LA", "P"):
                    logger.debug(f"Converting image from {img.mode} to RGB")
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    rgb_img.paste(
                        img,
                        mask=img.split()[-1] if img.mode == "RGBA" else None,
                    )
                    img = rgb_img

                # Создание директории для вывода
                task.output_path.parent.mkdir(parents=True, exist_ok=True)

                # Сохранение с оптимизацией
                save_kwargs: dict[str, Any] = {
                    "format": "JPEG",
                    "quality": settings.image_quality,
                    "progressive": True,
                    "optimize": True,
                }

                img.save(str(task.output_path), **save_kwargs)

            # Проверка результата
            if not task.output_path.exists():
                logger.error(
                    f"PIL completed but output file not found: {task.output_path}"
                )
                return False

            output_size = task.output_path.stat().st_size
            if output_size == 0:
                logger.error(f"PIL produced empty file: {task.output_path}")
                return False

            # Проверка на подозрительно маленький размер
            source_size = task.input_path.stat().st_size
            if output_size < source_size * 0.01:  # Меньше 1% от исходного
                logger.warning(
                    f"PIL output suspiciously small: {output_size} bytes from {source_size} bytes"
                )
                return False

            logger.debug(
                f"Image processed successfully: {source_size} → {output_size} bytes "
                f"({(output_size / source_size) * 100:.1f}%)"
            )
            return True

        except Exception as e:
            logger.error(f"PIL processing failed for {task.input_path}: {str(e)}")
            return False

    async def _copy_file(self, task: ProcessingTask) -> bool:
        """
        Копирование файла с zero-copy оптимизацией как fallback при ошибках обработки.

        Args:
            task: Задача обработки

        Returns:
            True если копирование успешно
        """
        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer

        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                # Проверка исходного файла
                if not task.input_path.exists():
                    logger.error(f"Source file not found: {task.input_path}")
                    return False

                source_size = task.input_path.stat().st_size
                if source_size == 0:
                    logger.error(f"Source file is empty: {task.input_path}")
                    return False

                # Создание директории
                task.output_path.parent.mkdir(parents=True, exist_ok=True)

                # Удаляем существующий файл если есть
                if task.output_path.exists():
                    task.output_path.unlink()

                # Zero-copy transfer
                config = ZeroCopyConfig(
                    enabled=True,
                    min_size_mb=10,
                    verify_copy=True,
                    chunk_size_mb=64
                )
                
                transfer = get_zero_copy_transfer(config)
                success = await transfer.copy_file(
                    task.input_path,
                    task.output_path,
                    verify=True
                )
                
                if success:
                    self._image_copied_count += 1
                    return True
                else:
                    logger.error(f"Zero-copy failed on attempt {attempt + 1}")
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(1)
                    continue

            except Exception as e:
                logger.error(f"File copy failed on attempt {attempt + 1}: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                continue

        logger.error(f"File copy failed after {max_attempts} attempts")
        return False

    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику обработки изображений.

        Returns:
            Словарь со статистикой
        """
        total_processed = self._image_processed_count + self._image_copied_count
        processing_percentage = (
            (self._image_processed_count / total_processed) * 100
            if total_processed > 0
            else 0
        )

        return {
            "total_processed": total_processed,
            "optimized": self._image_processed_count,
            "copied": self._image_copied_count,
            "optimization_percentage": processing_percentage,
        }

    def log_statistics(self) -> None:
        """Логировать статистику обработки изображений."""
        stats = self.get_statistics()
        logger.info(
            f"Image processing stats: {stats['total_processed']} total, "
            f"{stats['optimized']} optimized, {stats['copied']} copied, "
            f"Optimization rate: {stats['optimization_percentage']:.1f}%"
        )
