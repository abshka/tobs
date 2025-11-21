"""
Media downloader module.

Handles downloading media files from Telegram with progress tracking,
resume support, and multiple download strategies.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from telethon.tl.types import Message

# Environment variables for download control
ENABLE_PARALLEL_DOWNLOAD = (
    os.getenv("ENABLE_PARALLEL_DOWNLOAD", "true").lower() == "true"
)
PARALLEL_DOWNLOAD_MIN_SIZE_MB = int(os.getenv("PARALLEL_DOWNLOAD_MIN_SIZE_MB", "5"))
MAX_PARALLEL_CONNECTIONS = int(os.getenv("MAX_PARALLEL_CONNECTIONS", "8"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

# Persistent download mode - never give up on files (DEFAULT: enabled for all files)
PERSISTENT_DOWNLOAD_MODE = (
    os.getenv("PERSISTENT_DOWNLOAD_MODE", "true").lower() == "true"
)
PERSISTENT_MIN_SIZE_MB = float(
    os.getenv("PERSISTENT_MIN_SIZE_MB", "0.5")
)  # Для файлов > 0.5MB (почти все)


class MediaDownloader:
    """Управляет загрузкой медиафайлов из Telegram."""

    def __init__(self, connection_manager: Any, temp_dir: Path):
        """
        Инициализация загрузчика медиа.

        Args:
            connection_manager: Менеджер соединений с семафором для контроля конкурентности
            temp_dir: Директория для временных файлов
        """
        self.connection_manager = connection_manager
        self.temp_dir = temp_dir

        # Статистика загрузок
        self._persistent_download_attempts = 0
        self._persistent_download_successes = 0
        self._standard_download_attempts = 0
        self._standard_download_successes = 0

        # Настройки из environment
        self._persistent_enabled = PERSISTENT_DOWNLOAD_MODE
        self._persistent_min_size_mb = PERSISTENT_MIN_SIZE_MB

    async def download_media(
        self,
        message: Message,
        progress_queue: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Главный метод загрузки медиафайла из сообщения.

        Автоматически выбирает стратегию загрузки на основе размера файла
        и настроек окружения.

        Args:
            message: Telegram сообщение с медиа
            progress_queue: Очередь для отправки прогресса загрузки
            task_id: ID задачи для отслеживания прогресса

        Returns:
            Path к загруженному файлу или None при неудаче
        """
        if not hasattr(message, "file") or not message.file:
            logger.warning("Message has no file attribute or file is None")
            return None

        expected_size = getattr(message.file, "size", 0)
        if expected_size == 0:
            logger.warning(f"Message {message.id} has zero file size")
            return None

        file_size_mb = expected_size / (1024 * 1024)
        logger.info(
            f"Starting download for message {message.id}: {file_size_mb:.2f} MB"
        )

        # Используем persistent download для всех файлов (guaranteed completion)
        if self._persistent_enabled:
            return await self._persistent_download(
                message, expected_size, progress_queue, task_id
            )
        else:
            return await self._standard_download(
                message, expected_size, progress_queue, task_id
            )

    async def _persistent_download(
        self,
        message: Message,
        expected_size: int,
        progress_queue: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Упорная загрузка файла до полного завершения.

        Никогда не сдается - повторяет попытки до тех пор, пока файл
        не будет загружен полностью. Идеально для гарантированной загрузки.

        Args:
            message: Telegram сообщение с медиа
            expected_size: Ожидаемый размер файла в байтах
            progress_queue: Очередь для отправки прогресса
            task_id: ID задачи для прогресса

        Returns:
            Path к загруженному файлу или None после критических неудач
        """
        self._persistent_download_attempts += 1

        temp_path = self.temp_dir / f"persistent_{message.id}.tmp"
        max_consecutive_failures = 5
        attempt = 0
        consecutive_failures = 0

        file_size_mb = expected_size / (1024 * 1024)
        logger.info(
            f"🔄 Starting persistent download for message {message.id}: {file_size_mb:.2f} MB"
        )

        while True:
            attempt += 1

            # Проверяем текущий размер файла
            current_size = 0
            if temp_path.exists():
                current_size = temp_path.stat().st_size

                # Проверяем, завершена ли загрузка
                if current_size >= expected_size:
                    logger.info(
                        f"✅ Persistent download completed for message {message.id}: "
                        f"{current_size / 1024 / 1024:.2f} MB"
                    )
                    self._persistent_download_successes += 1
                    return temp_path

                # Если файл слишком большой, перезапускаем
                if current_size > expected_size * 1.1:
                    logger.warning(
                        f"Downloaded file too large ({current_size} > {expected_size * 1.1}), "
                        f"restarting download"
                    )
                    temp_path.unlink(missing_ok=True)
                    current_size = 0

            # Адаптивный таймаут на основе оставшегося размера
            remaining_mb = (expected_size - current_size) / (1024 * 1024)
            chunk_timeout = max(300, min(1200, remaining_mb * 60))

            if attempt > 1:
                completion_percent = (
                    (current_size / expected_size) * 100 if expected_size > 0 else 0
                )
                logger.info(
                    f"Persistent download attempt {attempt} for message {message.id}: "
                    f"{current_size / 1024 / 1024:.2f}/{file_size_mb:.2f} MB "
                    f"({completion_percent:.1f}%), timeout: {chunk_timeout}s"
                )

            # Progress callback для Rich progress bar
            async def progress_callback(downloaded: int, total: int) -> None:
                if progress_queue and task_id:
                    advance = downloaded - getattr(
                        progress_callback,
                        "last_reported",
                        0,  # type: ignore[attr-defined]
                    )
                    if advance > 0:
                        await progress_queue.put(
                            {
                                "type": "update",
                                "task_id": task_id,
                                "data": {"advance": advance},
                            }
                        )
                        progress_callback.last_reported = downloaded  # type: ignore[attr-defined]

            try:
                # Используем семафор connection manager для контроля конкурентности
                async with self.connection_manager.download_semaphore:
                    await asyncio.wait_for(
                        message.download_media(
                            file=temp_path, progress_callback=progress_callback
                        ),
                        timeout=chunk_timeout,
                    )

                # Проверяем прогресс после попытки
                if temp_path.exists():
                    new_size = temp_path.stat().st_size
                    if new_size > current_size:
                        # Прогресс есть, сбрасываем счетчик неудач
                        consecutive_failures = 0
                        logger.debug(
                            f"Progress made: {new_size - current_size} bytes downloaded"
                        )
                    else:
                        # Нет прогресса
                        consecutive_failures += 1
                        logger.warning(
                            f"No progress in attempt {attempt}, consecutive failures: {consecutive_failures}"
                        )
                else:
                    consecutive_failures += 1
                    logger.warning(
                        f"Temp file not found after download attempt, consecutive failures: {consecutive_failures}"
                    )

            except asyncio.TimeoutError:
                logger.warning(
                    f"Persistent download attempt {attempt} timed out after {chunk_timeout}s"
                )
                consecutive_failures += 1
            except Exception as e:
                logger.warning(
                    f"Persistent download attempt {attempt} failed with error: {type(e).__name__}: {e}"
                )
                consecutive_failures += 1

            # При множественных неудачах подряд - принимаем решение
            if consecutive_failures >= max_consecutive_failures:
                if temp_path.exists():
                    final_size = temp_path.stat().st_size
                    completion_percent = (
                        (final_size / expected_size) * 100 if expected_size > 0 else 0
                    )

                    # Если загружено > 90%, считаем успехом
                    if final_size > expected_size * 0.9:
                        logger.warning(
                            f"⚠️ Accepting partial download ({completion_percent:.1f}%) "
                            f"after {max_consecutive_failures} consecutive failures"
                        )
                        self._persistent_download_successes += 1
                        return temp_path
                    else:
                        # Слишком мало данных, перезапускаем с чистого листа
                        logger.warning(
                            f"Insufficient data ({completion_percent:.1f}%), "
                            f"restarting from scratch"
                        )
                        temp_path.unlink(missing_ok=True)

                # Сбрасываем счетчик и продолжаем
                consecutive_failures = 0
                continue

            # Короткая пауза между попытками
            delay = (
                2
                if consecutive_failures == 0
                else min(5 + consecutive_failures * 2, 30)
            )
            await asyncio.sleep(delay)

    async def _standard_download(
        self,
        message: Message,
        expected_size: int,
        progress_queue: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Улучшенная стандартная загрузка с ограниченным количеством попыток.

        В отличие от persistent download, эта стратегия сдается после
        определенного количества неудачных попыток.

        Args:
            message: Telegram сообщение с медиа
            expected_size: Ожидаемый размер файла в байтах
            progress_queue: Очередь для отправки прогресса
            task_id: ID задачи для прогресса

        Returns:
            Path к загруженному файлу или None после исчерпания попыток
        """
        self._standard_download_attempts += 1

        file_size_mb = expected_size / (1024 * 1024)
        max_retries = 15  # Максимум попыток

        # Адаптивный таймаут в зависимости от размера файла
        base_timeout = min(1200, max(300, file_size_mb * 60))

        temp_path = self.temp_dir / f"download_{message.id}_{int(time.time())}"

        logger.info(
            f"📥 Standard download starting: {file_size_mb:.1f}MB file "
            f"(message {message.id}), timeout: {base_timeout}s"
        )

        for attempt in range(max_retries):
            try:
                current_size = temp_path.stat().st_size if temp_path.exists() else 0

                # Логирование прогресса
                if current_size > 0:
                    completion_percent = (
                        (current_size / expected_size) * 100 if expected_size > 0 else 0
                    )
                    logger.info(
                        f"Resuming download attempt {attempt + 1}/{max_retries}: "
                        f"{current_size / 1024 / 1024:.1f}MB ({completion_percent:.1f}%)"
                    )
                else:
                    logger.info(
                        f"Starting download attempt {attempt + 1}/{max_retries}: "
                        f"{file_size_mb:.1f}MB"
                    )

                start_time = time.time()
                last_progress_time = start_time

                # Progress callback с логированием каждые 30 секунд
                async def progress_callback(downloaded: int, total: int) -> None:
                    nonlocal last_progress_time

                    if progress_queue and task_id:
                        advance = downloaded - getattr(
                            progress_callback,
                            "last_reported",
                            0,  # type: ignore[attr-defined]
                        )
                        if advance > 0:
                            await progress_queue.put(
                                {
                                    "type": "update",
                                    "task_id": task_id,
                                    "data": {"advance": advance},
                                }
                            )
                            progress_callback.last_reported = downloaded  # type: ignore[attr-defined]

                    # Логируем прогресс каждые 30 секунд
                    current_time = time.time()
                    if current_time - last_progress_time > 30:
                        if downloaded > current_size:
                            speed_kbps = (
                                (downloaded - current_size)
                                / (current_time - start_time)
                                / 1024
                            )
                            progress_percent = (
                                (downloaded / expected_size) * 100
                                if expected_size > 0
                                else 0
                            )
                            logger.info(
                                f"Progress {message.id}: {progress_percent:.1f}% "
                                f"({downloaded / 1024 / 1024:.1f}/{expected_size / 1024 / 1024:.1f}MB) "
                                f"Speed: {speed_kbps:.1f} KB/s"
                            )
                        last_progress_time = current_time

                # Загрузка с семафором
                async with self.connection_manager.download_semaphore:
                    await asyncio.wait_for(
                        message.download_media(
                            file=temp_path, progress_callback=progress_callback
                        ),
                        timeout=base_timeout,
                    )

                # Проверяем успешность загрузки
                if temp_path.exists():
                    final_size = temp_path.stat().st_size
                    # Принимаем файл если загружено >= 95% от ожидаемого размера
                    if final_size >= expected_size * 0.95:
                        elapsed_time = time.time() - start_time
                        speed_kbps = (
                            (final_size - current_size) / elapsed_time / 1024
                            if elapsed_time > 0
                            else 0
                        )
                        logger.info(
                            f"✅ Standard download completed for message {message.id}: "
                            f"{final_size / 1024 / 1024:.1f}MB in {elapsed_time:.1f}s "
                            f"({speed_kbps:.1f} KB/s)"
                        )
                        self._standard_download_successes += 1
                        return temp_path
                    else:
                        completion_percent = (
                            (final_size / expected_size) * 100
                            if expected_size > 0
                            else 0
                        )
                        logger.warning(
                            f"Downloaded file incomplete: {final_size}/{expected_size} bytes "
                            f"({completion_percent:.1f}%)"
                        )

            except asyncio.TimeoutError as e:
                logger.warning(
                    f"Standard download attempt {attempt + 1} timed out after {base_timeout}s: {e}"
                )
                # Для таймаутов не удаляем файл - можем продолжить загрузку
                if attempt < max_retries - 1:
                    delay = min(30 + attempt * 10, 120)
                    logger.info(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.warning(
                    f"Standard download attempt {attempt + 1} failed with error: "
                    f"{type(e).__name__}: {e}"
                )
                # Для других ошибок также сохраняем файл для возможного продолжения
                if attempt < max_retries - 1:
                    delay = min(10 + attempt * 5, 60)
                    logger.info(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)

        # Все попытки исчерпаны
        if temp_path.exists():
            partial_size = temp_path.stat().st_size
            completion_percent = (
                (partial_size / expected_size) * 100 if expected_size > 0 else 0
            )
            logger.error(
                f"❌ Standard download failed after {max_retries} attempts for message {message.id}. "
                f"Partial file: {partial_size / 1024 / 1024:.1f}MB ({completion_percent:.1f}%)"
            )
        else:
            logger.error(
                f"❌ Standard download failed after {max_retries} attempts for message {message.id}. "
                f"No partial file."
            )

        return None

    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику загрузок.

        Returns:
            Словарь со статистикой успешных и неудачных загрузок
        """
        persistent_success_rate = (
            (self._persistent_download_successes / self._persistent_download_attempts)
            * 100
            if self._persistent_download_attempts > 0
            else 0
        )

        standard_success_rate = (
            (self._standard_download_successes / self._standard_download_attempts) * 100
            if self._standard_download_attempts > 0
            else 0
        )

        return {
            "persistent_downloads": {
                "enabled": self._persistent_enabled,
                "attempts": self._persistent_download_attempts,
                "successes": self._persistent_download_successes,
                "success_rate_percent": persistent_success_rate,
                "min_size_mb": self._persistent_min_size_mb,
            },
            "standard_downloads": {
                "attempts": self._standard_download_attempts,
                "successes": self._standard_download_successes,
                "success_rate_percent": standard_success_rate,
            },
        }

    def log_statistics(self) -> None:
        """Логировать статистику загрузок."""
        stats = self.get_statistics()

        if self._persistent_download_attempts > 0:
            logger.info(
                f"Persistent downloads: {self._persistent_download_successes}/"
                f"{self._persistent_download_attempts} successful "
                f"({stats['persistent_downloads']['success_rate_percent']:.1f}%)"
            )

        if self._standard_download_attempts > 0:
            logger.info(
                f"Standard downloads: {self._standard_download_successes}/"
                f"{self._standard_download_attempts} successful "
                f"({stats['standard_downloads']['success_rate_percent']:.1f}%)"
            )
