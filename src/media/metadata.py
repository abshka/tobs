"""
Metadata extraction for media files.

Extracts metadata from video, audio, and image files using FFmpeg and PIL.
Provides caching for improved performance.
"""

import asyncio
import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Dict

import aiofiles
import aiofiles.os
import ffmpeg
from loguru import logger
from PIL import Image

from .models import MediaMetadata


class MetadataExtractor:
    """Извлекает метаданные из медиафайлов."""

    def __init__(self, io_executor):
        self.io_executor = io_executor
        self._metadata_cache: Dict[str, MediaMetadata] = {}
        self._file_checksums: Dict[Path, str] = {}

    async def get_metadata(self, file_path: Path, media_type: str) -> MediaMetadata:
        """Получение метаданных медиа файла."""
        try:
            # Проверка кэша метаданных
            file_hash = await self._get_file_hash(file_path)
            if file_hash in self._metadata_cache:
                return self._metadata_cache[file_hash]

            # Базовая информация
            stat = await aiofiles.os.stat(file_path)
            mime_type, _ = mimetypes.guess_type(str(file_path))

            metadata = MediaMetadata(
                file_size=stat.st_size,
                mime_type=mime_type or "application/octet-stream",
                checksum=file_hash,
            )

            # Дополнительные метаданные в зависимости от типа
            if media_type in ["video", "audio"]:
                video_metadata = await self._get_ffmpeg_metadata(file_path)
                metadata.duration = video_metadata.get("duration")
                metadata.width = video_metadata.get("width")
                metadata.height = video_metadata.get("height")
                metadata.bitrate = video_metadata.get("bitrate")
                metadata.codec = video_metadata.get("codec")
                metadata.fps = video_metadata.get("fps")
                metadata.channels = video_metadata.get("channels")
                metadata.sample_rate = video_metadata.get("sample_rate")

            elif media_type in ["photo", "image"]:
                image_metadata = await self._get_image_metadata(file_path)
                metadata.width = image_metadata.get("width")
                metadata.height = image_metadata.get("height")
                metadata.format = image_metadata.get("format")

            # Сохранение в кэш
            self._metadata_cache[file_hash] = metadata

            return metadata

        except Exception as e:
            logger.error(f"Failed to get metadata for {file_path}: {e}")
            return MediaMetadata(file_size=0, mime_type="application/octet-stream")

    async def _get_ffmpeg_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Получение метаданных через FFmpeg."""
        try:
            loop = asyncio.get_event_loop()

            def probe_file():
                try:
                    probe = ffmpeg.probe(str(file_path))
                    video_stream = next(
                        (s for s in probe["streams"] if s["codec_type"] == "video"),
                        None,
                    )
                    audio_stream = next(
                        (s for s in probe["streams"] if s["codec_type"] == "audio"),
                        None,
                    )

                    metadata = {}

                    if video_stream:
                        metadata.update(
                            {
                                "width": int(video_stream.get("width", 0)),
                                "height": int(video_stream.get("height", 0)),
                                "duration": float(video_stream.get("duration", 0)),
                                "fps": eval(video_stream.get("r_frame_rate", "0/1")),
                                "codec": video_stream.get("codec_name"),
                                "bitrate": int(video_stream.get("bit_rate", 0)),
                            }
                        )

                    if audio_stream:
                        metadata.update(
                            {
                                "channels": int(audio_stream.get("channels", 0)),
                                "sample_rate": int(audio_stream.get("sample_rate", 0)),
                                "duration": float(
                                    audio_stream.get(
                                        "duration", metadata.get("duration", 0)
                                    )
                                ),
                            }
                        )

                    return metadata

                except Exception as e:
                    # Only log as warning if file size > 0, otherwise it's expected for empty files
                    if file_path.exists() and file_path.stat().st_size > 0:
                        logger.warning(f"FFmpeg probe failed for {file_path}: {e}")
                    return {}

            return await loop.run_in_executor(self.io_executor, probe_file)

        except Exception as e:
            logger.error(f"Failed to get FFmpeg metadata for {file_path}: {e}")
            return {}

    async def _get_image_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Получение метаданных изображения."""
        try:
            loop = asyncio.get_event_loop()

            def get_image_info():
                try:
                    with Image.open(file_path) as img:
                        return {
                            "width": img.width,
                            "height": img.height,
                            "format": img.format,
                            "mode": img.mode,
                        }
                except Exception as e:
                    logger.warning(f"PIL failed for {file_path}: {e}")
                    return {}

            return await loop.run_in_executor(self.io_executor, get_image_info)

        except Exception as e:
            logger.error(f"Failed to get image metadata for {file_path}: {e}")
            return {}

    async def _get_file_hash(self, file_path: Path) -> str:
        """Получение хэша файла."""
        if file_path in self._file_checksums:
            return self._file_checksums[file_path]

        try:
            hash_obj = hashlib.md5()
            async with aiofiles.open(file_path, "rb") as f:
                while chunk := await f.read(8192):
                    hash_obj.update(chunk)

            file_hash = hash_obj.hexdigest()
            self._file_checksums[file_path] = file_hash
            return file_hash

        except Exception as e:
            logger.error(f"Failed to calculate hash for {file_path}: {e}")
            return str(file_path)
