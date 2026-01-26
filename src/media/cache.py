"""
Media caching system.

Provides caching functionality for processed media files
to avoid redundant processing of the same content.
"""

import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from telethon.tl.types import Message


class MediaCache:
    """Управляет кэшированием обработанных медиафайлов."""

    def __init__(self, cache_manager: Optional[Any] = None):
        self.cache_manager = cache_manager

    async def check_cache(self, message: Message, output_path: Path) -> Optional[Path]:
        """Проверка кэша обработанных файлов."""
        if not self.cache_manager:
            return None

        try:
            cache_key = f"media_{message.id}"
            cached_info = await self.cache_manager.get(cache_key)

            if cached_info and isinstance(cached_info, dict):
                cached_path = Path(cached_info.get("path", ""))
                if cached_path.exists() and cached_path.stat().st_size > 0:
                    # Копирование из кэша
                    if cached_path != output_path:
                        await self._copy_file_async(cached_path, output_path)
                    return output_path

            return None

        except Exception as e:
            logger.debug(f"Cache check failed: {e}")
            return None

    async def save_to_cache(self, message: Message, result_path: Path):
        """Сохранение результата в кэш."""
        if not self.cache_manager:
            return

        try:
            cache_key = f"media_{message.id}"
            cache_data = {
                "path": str(result_path),
                "size": result_path.stat().st_size,
                "timestamp": time.time(),
            }

            await self.cache_manager.set(cache_key, cache_data)

        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

    async def _copy_file_async(self, src_path: Path, dst_path: Path):
        """
        Асинхронное копирование файла с zero-copy оптимизацией.
        
        Uses os.sendfile() on supported platforms for kernel-level copying,
        falls back to aiofiles on unsupported platforms or for small files.
        """
        from src.media.zero_copy import ZeroCopyConfig, get_zero_copy_transfer

        try:
            # Use zero-copy transfer with config from cache_manager if available
            config = ZeroCopyConfig(
                enabled=True,
                min_size_mb=10,
                verify_copy=True,
                chunk_size_mb=64
            )
            
            transfer = get_zero_copy_transfer(config)
            success = await transfer.copy_file(src_path, dst_path, verify=True)
            
            if not success:
                raise RuntimeError(f"Zero-copy transfer failed: {src_path} -> {dst_path}")
            
            logger.debug(f"File copied from cache: {src_path} -> {dst_path}")

        except Exception as e:
            logger.error(f"Failed to copy file from cache: {e}")
            raise
