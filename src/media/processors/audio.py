"""
Audio processor for media files.

Handles audio file processing including transcoding and bitrate optimization.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import aiofiles
import ffmpeg
from loguru import logger

from ..models import MediaMetadata, ProcessingSettings, ProcessingTask
from .base import BaseProcessor

# Environment variables for processing control (fallback values)
DEFAULT_PROCESS_AUDIO = os.getenv("PROCESS_AUDIO", "true").lower() == "true"


class AudioProcessor(BaseProcessor):
    """Процессор для аудиофайлов с транскодированием и оптимизацией битрейта."""

    def __init__(
        self,
        io_executor: Any,
        cpu_executor: Any,
        validator: Any,
        config: Optional[Any] = None,
        settings: Optional[ProcessingSettings] = None,
    ):
        """
        Инициализация аудиопроцессора.

        Args:
            io_executor: Executor для IO операций
            cpu_executor: Executor для CPU-интенсивных операций (FFmpeg)
            validator: MediaValidator для проверки целостности файлов
            config: Объект конфигурации
            settings: Настройки обработки по умолчанию
        """
        super().__init__(io_executor, cpu_executor, settings)
        self.validator = validator
        self.config = config

        # Статистика
        self._audio_processed_count = 0
        self._audio_copied_count = 0

    async def process(self, task: ProcessingTask, worker_name: str) -> bool:
        """
        Обработка аудио файла с fallback на копирование.

        Args:
            task: Задача обработки с input/output путями
            worker_name: Имя воркера для логирования

        Returns:
            True если обработка успешна, False иначе
        """
        try:
            settings = task.processing_settings or self.settings
            metadata = task.metadata

            # Определяем нужность обработки для аудио
            needs_proc = self.needs_processing_with_metadata(metadata, settings)

            if not needs_proc:
                logger.debug(
                    f"Audio file doesn't need processing, copying: {task.input_path}"
                )
                return await self._copy_file(task)

            # Проверка целостности аудио файла
            if self.validator:
                is_valid = await self.validator.validate_file(task.input_path)
                if not is_valid:
                    logger.warning(
                        f"Audio file integrity check failed, using fallback copy: {task.input_path}"
                    )
                    return await self._copy_file(task)

            # Настройки аудио
            audio_args = {
                "acodec": settings.audio_codec,
                "b:a": f"{settings.max_audio_bitrate}k",
            }

            # Выполнение FFmpeg в отдельном потоке
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.cpu_executor,
                self._run_ffmpeg_audio,
                task,
                audio_args,
            )

            if success and task.output_path.exists():
                logger.debug(
                    f"✅ Audio processing completed: {task.input_path} -> {task.output_path}"
                )
                self._audio_processed_count += 1
                return True
            else:
                # Fallback: простое копирование при ошибке FFmpeg
                logger.debug(f"Using fallback copy for audio {task.input_path}")
                return await self._copy_file(task)

        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            return await self._copy_file(task)

    def needs_processing(self, file_path: Path, settings: ProcessingSettings) -> bool:
        """
        Определяет, нужна ли обработка аудио файла (базовая проверка).

        Args:
            file_path: Путь к аудиофайлу
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        try:
            if not file_path.exists():
                return False

            # Базовая проверка: попробуем оптимизировать файлы среднего размера
            file_size = file_path.stat().st_size
            # Оптимизируем файлы от 1MB до 100MB
            if 1 * 1024 * 1024 < file_size < 100 * 1024 * 1024:
                return True

            return False
        except Exception:
            return False

    def needs_processing_with_metadata(
        self, metadata: Optional[MediaMetadata], settings: ProcessingSettings
    ) -> bool:
        """
        Определение необходимости обработки аудио с учетом метаданных.

        Args:
            metadata: Метаданные аудиофайла
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        # Проверяем настройки из config или переменную окружения
        process_audio = getattr(self.config, "process_audio", DEFAULT_PROCESS_AUDIO)
        if not process_audio:
            logger.debug(
                "Audio processing disabled via config or PROCESS_AUDIO environment variable"
            )
            return False

        if not metadata:
            return True  # Попробуем оптимизировать если нет метаданных

        # Проверяем битрейт аудио
        if metadata.bitrate:
            # metadata.bitrate в bps, settings в kbps
            if metadata.bitrate > settings.max_audio_bitrate * 1000:
                logger.debug(
                    f"Audio bitrate {metadata.bitrate} exceeds max {settings.max_audio_bitrate * 1000}"
                )
                return True

        # Проверяем кодек
        if metadata.codec:
            # Предпочитаем AAC и MP3
            if metadata.codec.lower() not in ["aac", "mp3"]:
                logger.debug(
                    f"Audio codec {metadata.codec} is not optimal (prefer AAC/MP3)"
                )
                return True

        return False

    def _run_ffmpeg_audio(
        self,
        task: ProcessingTask,
        audio_args: dict,
    ) -> bool:
        """
        Выполнение FFmpeg команды для аудио (синхронный метод для executor).

        Args:
            task: Задача обработки
            audio_args: Аргументы для аудио кодирования

        Returns:
            True если FFmpeg выполнен успешно
        """
        try:
            # Валидация входного файла
            if not task.input_path.exists():
                logger.error(f"Input audio file does not exist: {task.input_path}")
                return False

            file_size = task.input_path.stat().st_size
            if file_size == 0:
                logger.error(f"Input audio file is empty: {task.input_path}")
                return False

            logger.debug(
                f"Processing audio file: {task.input_path} (size: {file_size} bytes)"
            )

            # Построение FFmpeg команды
            input_stream = ffmpeg.input(str(task.input_path))
            output = input_stream.output(str(task.output_path), **audio_args)

            # Логирование команды для отладки
            cmd_args = ffmpeg.compile(output)
            logger.debug(f"Executing FFmpeg audio command: {' '.join(cmd_args)}")

            # Выполнение FFmpeg
            ffmpeg.run(output, overwrite_output=True, quiet=True)

            # Проверяем результат FFmpeg для аудио
            if not task.output_path.exists():
                logger.error(
                    f"FFmpeg audio completed but output file not found: {task.output_path}"
                )
                return False

            output_size = task.output_path.stat().st_size
            if output_size == 0:
                logger.error(f"FFmpeg audio produced empty file: {task.output_path}")
                return False

            # Если файл слишком мал по сравнению с исходным, возможно ошибка
            source_size = task.input_path.stat().st_size
            if output_size < source_size * 0.01:  # Меньше 1% от исходного
                logger.warning(
                    f"FFmpeg audio output suspiciously small: {output_size} bytes from {source_size} bytes"
                )
                return False

            return True

        except ffmpeg.Error as e:
            # Логирование полной ошибки
            stderr_output = (
                e.stderr.decode("utf-8") if e.stderr else "No stderr available"
            )
            stdout_output = (
                e.stdout.decode("utf-8") if e.stdout else "No stdout available"
            )
            logger.error(f"FFmpeg audio processing failed for {task.input_path}")
            logger.error(f"FFmpeg stderr: {stderr_output}")
            logger.error(f"FFmpeg stdout: {stdout_output}")
            return False

        except Exception as e:
            logger.error(
                f"FFmpeg unexpected error for audio {task.input_path}: {str(e)}"
            )
            return False

    async def _copy_file(self, task: ProcessingTask) -> bool:
        """
        Копирование файла как fallback при ошибках обработки.

        Args:
            task: Задача обработки

        Returns:
            True если копирование успешно
        """
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

                # Копирование файла
                copied_bytes = 0
                async with aiofiles.open(task.input_path, "rb") as src:
                    async with aiofiles.open(task.output_path, "wb") as dst:
                        while chunk := await src.read(64 * 1024):  # 64KB chunks
                            await dst.write(chunk)
                            copied_bytes += len(chunk)
                        await dst.flush()

                # Проверка результата
                if not task.output_path.exists():
                    logger.error(f"Output file was not created: {task.output_path}")
                    continue

                output_size = task.output_path.stat().st_size

                if output_size != source_size:
                    logger.error(
                        f"File copy size mismatch! Source: {source_size} bytes, "
                        f"Output: {output_size} bytes (attempt {attempt + 1})"
                    )
                    continue

                self._audio_copied_count += 1
                return True

            except Exception as e:
                logger.error(f"File copy failed on attempt {attempt + 1}: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                continue

        logger.error(f"File copy failed after {max_attempts} attempts")
        return False

    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику обработки аудио.

        Returns:
            Словарь со статистикой
        """
        total_processed = self._audio_processed_count + self._audio_copied_count
        processing_percentage = (
            (self._audio_processed_count / total_processed) * 100
            if total_processed > 0
            else 0
        )

        return {
            "total_processed": total_processed,
            "transcoded": self._audio_processed_count,
            "copied": self._audio_copied_count,
            "transcoding_percentage": processing_percentage,
        }

    def log_statistics(self) -> None:
        """Логировать статистику обработки аудио."""
        stats = self.get_statistics()
        logger.info(
            f"Audio processing stats: {stats['total_processed']} total, "
            f"{stats['transcoded']} transcoded, {stats['copied']} copied, "
            f"Transcoding rate: {stats['transcoding_percentage']:.1f}%"
        )
