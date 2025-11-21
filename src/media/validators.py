"""
Media file validators.

Provides validation functionality for video, audio, and image files
to ensure integrity after download and processing.
"""

import asyncio
from pathlib import Path

from loguru import logger
from PIL import Image


class MediaValidator:
    """Проверяет целостность медиафайлов."""

    def __init__(self, io_executor):
        self.io_executor = io_executor

    async def validate_file_integrity(self, file_path: Path) -> bool:
        """Проверка целостности скачанного файла."""
        try:
            if not file_path.exists():
                return False

            # Проверяем размер файла
            file_size = file_path.stat().st_size
            if file_size == 0:
                logger.debug(f"File is empty: {file_path}")
                return False

            # Добавляем небольшую задержку чтобы файл успел полностью записаться
            await asyncio.sleep(0.1)

            # Определяем тип файла по расширению
            suffix = file_path.suffix.lower()

            # Для всех файлов делаем базовую проверку
            if file_size < 100:
                logger.debug(
                    f"File too small (likely corrupted): {file_path}, size: {file_size}"
                )
                return False

            # Упрощенная валидация - проверяем только критические файлы
            if suffix in [".mp4", ".avi", ".mov", ".mkv", ".webm"]:
                return await self._validate_video_file_soft(file_path)
            elif suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                return await self._validate_image_file_soft(file_path)
            elif suffix in [".mp3", ".ogg", ".wav", ".m4a", ".flac"]:
                return await self._validate_audio_file_soft(file_path)
            else:
                # Для неизвестных типов просто проверяем размер
                return file_size > 100

        except Exception as e:
            logger.debug(f"File integrity check failed for {file_path}: {e}")
            return False

    async def _validate_video_file_soft(self, file_path: Path) -> bool:
        """Упрощенная проверка видео файла - проверяем только заголовок."""
        try:
            # Читаем первые несколько байт для проверки сигнатуры
            with open(file_path, "rb") as f:
                header = f.read(32)

            # Проверяем сигнатуры популярных видео форматов
            video_signatures = [
                b"\x00\x00\x00\x18ftypmp4",  # MP4
                b"\x00\x00\x00\x14ftypqt",  # MOV
                b"\x1a\x45\xdf\xa3",  # MKV/WebM
                b"RIFF",  # AVI (начинается с RIFF)
            ]

            # Если найдена валидная сигнатура, файл корректный
            for signature in video_signatures:
                if signature in header:
                    logger.debug(f"Video file signature validated: {file_path}")
                    return True

            # Fallback: ffprobe с таймаутом
            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "csv=p=0",
                        str(file_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=5.0,
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0 and stdout:
                    logger.debug(f"Video file validated with ffprobe: {file_path}")
                    return True

                logger.debug(
                    f"Video validation failed: {stderr.decode()[:100] if stderr else 'unknown error'}"
                )
                return False

            except asyncio.TimeoutError:
                logger.debug(f"Video validation timed out: {file_path}")
                # Таймаут - считаем файл валидным если он достаточно большой
                return file_path.stat().st_size > 10000

        except Exception as e:
            logger.debug(f"Video validation error: {e}")
            return False

    async def _validate_audio_file_soft(self, file_path: Path) -> bool:
        """Упрощенная проверка аудио файла."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(12)

            # Проверяем сигнатуры аудио форматов
            audio_signatures = [
                b"ID3",  # MP3 с ID3 тегами
                b"\xff\xfb",  # MP3
                b"\xff\xf3",  # MP3
                b"OggS",  # OGG
                b"RIFF",  # WAV
                b"fLaC",  # FLAC
            ]

            for signature in audio_signatures:
                if header.startswith(signature):
                    logger.debug(f"Audio file signature validated: {file_path}")
                    return True

            # Если размер больше 10KB, считаем корректным
            return file_path.stat().st_size > 10000

        except Exception as e:
            logger.debug(f"Audio validation error: {e}")
            return False

    async def _validate_image_file_soft(self, file_path: Path) -> bool:
        """Упрощенная проверка изображения."""
        try:
            loop = asyncio.get_event_loop()

            def validate_image():
                try:
                    with Image.open(file_path) as img:
                        # Пытаемся загрузить изображение
                        img.verify()
                        return True
                except Exception:
                    return False

            is_valid = await loop.run_in_executor(self.io_executor, validate_image)
            if is_valid:
                logger.debug(f"Image file validation passed: {file_path}")
            else:
                logger.debug(f"Image file validation failed: {file_path}")

            return bool(is_valid)

        except Exception as e:
            logger.debug(f"Error validating image file {file_path}: {e}")
            return False
