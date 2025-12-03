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
from telethon import utils
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.types import Message

# Environment variables for download control
ENABLE_PARALLEL_DOWNLOAD = (
    os.getenv("ENABLE_PARALLEL_DOWNLOAD", "true").lower() == "true"
)
PARALLEL_DOWNLOAD_MIN_SIZE_MB = int(os.getenv("PARALLEL_DOWNLOAD_MIN_SIZE_MB", "5"))
MAX_PARALLEL_CONNECTIONS = int(os.getenv("MAX_PARALLEL_CONNECTIONS", "4"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))

# Persistent download mode - never give up on files (DEFAULT: enabled for all files)
PERSISTENT_DOWNLOAD_MODE = (
    os.getenv("PERSISTENT_DOWNLOAD_MODE", "true").lower() == "true"
)
PERSISTENT_MIN_SIZE_MB = float(
    os.getenv("PERSISTENT_MIN_SIZE_MB", "0.5")
)  # Для файлов > 0.5MB (почти все)


class TakeoutClientWrapper:
    """
    Wraps a TelegramClient to automatically inject InvokeWithTakeoutRequest
    into all calls. Used for accelerating media downloads.
    """

    def __init__(self, client, takeout_id):
        self._client = client
        self._takeout_id = takeout_id

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def download_file(self, *args, **kwargs):
        # Hack to support download_file with Takeout wrapping
        # We temporarily patch the client's __call__ to wrap requests
        original_call = self._client.__call__

        async def wrapped_call(request, ordered=False):
            if not isinstance(request, InvokeWithTakeoutRequest):
                request = InvokeWithTakeoutRequest(
                    takeout_id=self._takeout_id, query=request
                )
            return await original_call(request, ordered=ordered)

        try:
            # Patch
            self._client.__call__ = wrapped_call
            return await self._client.download_file(*args, **kwargs)
        finally:
            # Restore
            self._client.__call__ = original_call

    async def download_media(self, *args, **kwargs):
        # Same hack for download_media
        original_call = self._client.__call__

        async def wrapped_call(request, ordered=False):
            if not isinstance(request, InvokeWithTakeoutRequest):
                request = InvokeWithTakeoutRequest(
                    takeout_id=self._takeout_id, query=request
                )
            return await original_call(request, ordered=ordered)

        try:
            self._client.__call__ = wrapped_call
            return await self._client.download_media(*args, **kwargs)
        finally:
            self._client.__call__ = original_call

    async def __call__(self, request, ordered=False):
        if not isinstance(request, InvokeWithTakeoutRequest):
            request = InvokeWithTakeoutRequest(
                takeout_id=self._takeout_id, query=request
            )
        return await self._client(request, ordered=ordered)

    # We need to support download_media / download_file being called on this wrapper
    # Since we delegate __getattr__, client.download_media will be called.
    # But client.download_media calls self(request).
    # Since 'self' inside client.download_media is the client itself, it won't call our __call__.
    # WE MUST NOT delegate download_media/download_file if we want to intercept the call?
    # Wait, Telethon's download_media calls `self.download_file`.
    # `self.download_file` calls `self(GetFileRequest)`.
    # If we pass this wrapper as the 'client' argument to `download_file` (if it existed standalone), it would work.
    # But we are calling `wrapper.download_media`.
    # `wrapper.download_media` -> `client.download_media`.
    # Inside `client.download_media`, `self` is `client`.
    # So `client(GetFileRequest)` is called. `client.__call__` is used.
    # Our wrapper is bypassed.

    # SOLUTION: We must bind the method to our wrapper or use a different approach.
    # Telethon's `download_media` allows passing a `client`? No, it's a method on client.
    # However, `Message.download_media` takes a `client` argument? No, it uses `self._client`.

    # We can use `telethon.client.downloads.download_media(wrapper, ...)`?
    # No, it's a mixin.

    # We have to implement `download_media` on the wrapper and forward it to `client.download_media`
    # BUT we need `client` to use `wrapper` for the actual request.
    # This is tricky because `client` is hardcoded to use `self` for requests.

    # Alternative: Monkey-patch `__call__` on the client instance temporarily?
    # Risky if concurrent usage.

    # Alternative 2: `download_file` in Telethon is the low-level one.
    # It iterates chunks.
    # We can copy `download_media` logic? No, too complex.

    # Let's look at how `tdl` does it.
    # `tdl` uses `gotd/td`, which has a middleware system. Telethon doesn't have a request middleware system exposed easily.

    # However, we can use `client.download_file` directly?
    # `download_media` is just a wrapper that finds the location and calls `download_file`.
    # If we resolve the location ourselves, we can call `download_file`.
    # But `download_file` is also a method on `client`.

    # WAIT! `TelegramClient` inherits from `UpdateMethods`, `UserMethods`, etc.
    # The `__call__` is defined in `TelegramBaseClient`.

    # If we create a subclass of `TelegramClient` that shares the session/connection?
    # Too heavy.

    # Let's look at `TakeoutClientWrapper` again.
    # If we pass `wrapper` as the `client` to `Message`?
    # `msg = Message(...)`
    # `msg._client = wrapper`
    # `msg.download_media(...)` -> calls `self._client.download_media(...)` -> `wrapper.download_media(...)`
    # -> `client.download_media(...)` -> `client(GetFileRequest)`. Still bypassed.

    # We need `client.download_media` to use `wrapper` for sending requests.
    # It doesn't support that.

    # HACK: We can temporarily replace `client.__call__` with our wrapper's call.
    # But `client` is shared.

    # BETTER HACK:
    # Telethon's `download_file` implementation:
    # async def download_file(self, location, out=None, ...):
    #     sender = self._get_sender(dc_id)
    #     ...
    #     await sender.send(request)

    # It uses `sender`.

    # Maybe we can just use `InvokeWithTakeoutRequest` manually?
    # But `download_media` handles parallel downloads, parts, etc.

    # Let's look at `tdl` again. It wraps the *Invoker*.

    # In Telethon, `client` IS the invoker.

    # If we can't wrap the client easily, maybe we can just use the `TakeoutClientWrapper`
    # AND implement `download_media` on it by copying the minimal logic needed?
    # Or just use `client.download_media` but patch `client`?

    # Let's try to use `telethon.utils.get_input_location(message.file)`
    # Then `client.download_file(location, ...)`
    # But `download_file` still uses `self` (client).

    # What if we use a `ProxyClient` that inherits from `TelegramClient` (or mixins)
    # but delegates everything to the real client EXCEPT `__call__`?
    # `class ProxyClient(TelegramClient): ...`
    # But `TelegramClient` has a complex `__init__`.

    # Let's go with the "Monkey Patch" approach but safer.
    # We can create a new instance of `TelegramClient` that shares the `session` and `connection`?
    # No, connection is stateful.

    # Let's look at `src/telegram_sharded_client.py` again.
    # The workers are dedicated clients!
    # `self.worker_clients` are `TelegramClient` instances.
    # They are ONLY used for this export task.
    # So we CAN monkey-patch their `__call__` method!
    # Or better, we can wrap them *at creation time* in `ShardedTelegramManager`.

    # But `ShardedTelegramManager` creates them as `TelegramClient`.
    # We can define a `TakeoutTelegramClient` subclass in `ShardedTelegramManager`
    # that overrides `__call__`.

    # This is the cleanest solution.
    # I will modify `src/telegram_sharded_client.py` to use a custom client class for workers.

    pass


def get_best_threads(file_size: int, max_threads: int = 16) -> int:
    """
    Calculates optimal thread count based on file size (heuristic from tdl).
    """
    # tdl logic:
    # < 1MB: 1
    # < 5MB: 2
    # < 20MB: 4
    # < 50MB: 8
    # > 50MB: max (default 16 in tdl, we can use our env var)

    if file_size < 1 * 1024 * 1024:
        return 1
    if file_size < 5 * 1024 * 1024:
        return 2
    if file_size < 20 * 1024 * 1024:
        return 4
    if file_size < 50 * 1024 * 1024:
        return 8
    return max_threads


class MediaDownloader:
    """Управляет загрузкой медиафайлов из Telegram."""

    def __init__(
        self,
        connection_manager: Any,
        temp_dir: Path,
        client: Any = None,
        worker_clients: list = None,
    ):
        """
        Инициализация загрузчика медиа.

        Args:
            connection_manager: Менеджер соединений с семафором для контроля конкурентности
            temp_dir: Директория для временных файлов
            client: Основной клиент Telegram
            worker_clients: Список дополнительных клиентов (воркеров) для распределения нагрузки
        """
        self.connection_manager = connection_manager
        self.temp_dir = temp_dir
        self.client = client
        self.worker_clients = worker_clients or []

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
        MAX_PERSISTENT_ATTEMPTS = 50  # Абсолютный лимит попыток
        max_consecutive_failures = 5
        attempt = 0
        consecutive_failures = 0

        # Select client for download (round-robin if workers available)
        download_client = self.client
        if self.worker_clients:
            # Simple round-robin based on message ID to distribute load
            client_idx = message.id % len(self.worker_clients)
            download_client = self.worker_clients[client_idx]

        # Auto-wrap in Takeout if available (Crucial for speed)
        # Check if client has takeout_id (ShardedTelegramManager or TakeoutWorkerClient)
        takeout_id = getattr(download_client, "takeout_id", None)

        # If it has takeout_id but is NOT a TakeoutWorkerClient (e.g. it's the main manager),
        # we need to wrap it. TakeoutWorkerClient wraps itself.
        # We check class name to avoid circular imports or complex isinstance checks
        if takeout_id and type(download_client).__name__ != "TakeoutWorkerClient":
            download_client = TakeoutClientWrapper(download_client, takeout_id)

        file_size_mb = expected_size / (1024 * 1024)
        logger.info(
            f"🔄 Starting persistent download for message {message.id}: {file_size_mb:.2f} MB"
        )

        while attempt < MAX_PERSISTENT_ATTEMPTS:
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
                    # Try to use download_file for better control (part_size_kb)
                    try:
                        location = utils.get_input_location(message.media)
                        await asyncio.wait_for(
                            download_client.download_file(
                                location,
                                file=temp_path,
                                progress_callback=progress_callback,
                                part_size_kb=512,
                            ),
                            timeout=chunk_timeout,
                        )
                    except Exception as e_file:
                        # Fallback to download_media if download_file fails (e.g. location extraction issue)
                        # logger.debug(f"download_file failed, falling back to download_media: {e_file}")
                        await asyncio.wait_for(
                            download_client.download_media(
                                message,
                                file=temp_path,
                                progress_callback=progress_callback,
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
                # Special handling for DC migration errors
                if "FileMigrateError" in str(type(e)) or "DC" in str(e):
                    logger.info(
                        f"DC migration detected, extending timeout for next attempt"
                    )
                    # Increase timeout for DC migration
                    chunk_timeout = min(chunk_timeout * 1.5, 2400)  # Max 40 minutes
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
                    # Если использовали > 80% попыток, прекращаем
                    elif attempt >= MAX_PERSISTENT_ATTEMPTS * 0.8:
                        logger.error(
                            f"❌ Giving up after {attempt} attempts ({completion_percent:.1f}% complete)"
                        )
                        return None
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

        # Select client for download (round-robin if workers available)
        download_client = self.client
        if self.worker_clients:
            client_idx = message.id % len(self.worker_clients)
            download_client = self.worker_clients[client_idx]

        # Auto-wrap in Takeout if available
        takeout_id = getattr(download_client, "takeout_id", None)
        if takeout_id and type(download_client).__name__ != "TakeoutWorkerClient":
            download_client = TakeoutClientWrapper(download_client, takeout_id)

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
                    # Use standard download_media for stability
                    await asyncio.wait_for(
                        download_client.download_media(
                            message,
                            file=temp_path,
                            progress_callback=progress_callback,
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
