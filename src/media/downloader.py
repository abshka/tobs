"""
Media downloader module.

Handles downloading media files from Telegram with progress tracking,
resume support, and multiple download strategies.
"""

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from telethon import utils
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.types import Message

# B-6: Hash-based deduplication
from src.media.hash_dedup import HashBasedDeduplicator


class TelegramServerError(Exception):
    """Raised when Telegram servers are having issues (not client-side problem)."""



# Patterns indicating Telegram server-side issues
TELEGRAM_SERVER_ERROR_PATTERNS = [
    r"Telegram is having internal issues",
    r"TimeoutError.*GetFileRequest",
    r"TimeoutError.*Timeout while fetching",
]

# Pattern for Telethon's internal retry exhaustion - this is FINAL, no point retrying
TELETHON_EXHAUSTED_PATTERN = r"Request was unsuccessful (\d+) time"


def is_telegram_server_error(error: Exception) -> bool:
    """Check if error indicates Telegram server-side issues."""
    error_str = str(error)
    for pattern in TELEGRAM_SERVER_ERROR_PATTERNS:
        if re.search(pattern, error_str, re.IGNORECASE):
            return True
    return False


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

    # NOTE: The TakeoutClientWrapper above handles wrapping requests with InvokeWithTakeoutRequest.
    # For complex download scenarios, consider using the wrapper's download_media/download_file methods.


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
        worker_clients: Optional[list] = None,
        cache_manager: Optional[Any] = None,
        config: Optional[Any] = None,
    ):
        """
        Инициализация загрузчика медиа.

        Args:
            connection_manager: Менеджер соединений с семафором для контроля конкурентности
            temp_dir: Директория для временных файлов
            client: Основной клиент Telegram
            worker_clients: Список дополнительных клиентов (воркеров) для распределения нагрузки
            cache_manager: Менеджер кэша для дедупликации загрузок
            config: Конфигурация приложения
        """
        self.connection_manager = connection_manager
        self.temp_dir = temp_dir
        self.client = client
        self.worker_clients = worker_clients or []
        self.cache_manager = cache_manager
        self.config = config
        
        # In-memory cache for current session deduplication
        self._downloaded_cache: Dict[str, Path] = {}

        # Статистика загрузок
        self._persistent_download_attempts = 0
        self._persistent_download_successes = 0
        self._standard_download_attempts = 0
        self._standard_download_successes = 0

        # Настройки из environment
        self._persistent_enabled = PERSISTENT_DOWNLOAD_MODE
        self._persistent_min_size_mb = PERSISTENT_MIN_SIZE_MB
        
        # B-6: Hash-based deduplication
        if config and hasattr(config, 'performance') and config.performance.hash_based_deduplication:
            # Determine cache directory
            if hasattr(config, 'cache_path'):
                cache_dir = Path(config.cache_path)
            else:
                cache_dir = temp_dir.parent / 'cache'
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            self._hash_dedup = HashBasedDeduplicator(
                cache_path=cache_dir / "media_hash_cache.msgpack",
                max_cache_size=config.performance.hash_cache_max_size,
                enable_api_hashing=True
            )
            logger.info("🔐 Hash-based deduplication ENABLED")
        else:
            self._hash_dedup = None
            logger.info("ID-based deduplication only (hash dedup disabled)")

    def _get_file_key(self, message: Message) -> Optional[str]:
        """
        Generate a unique key for the media file to prevent duplicate downloads.
        """
        if not hasattr(message, "media") or not message.media:
            return None
            
        media = message.media
        try:
            if hasattr(media, "document") and media.document:
                # Document ID + Access Hash is unique
                return f"doc_{media.document.id}_{media.document.access_hash}"
            elif hasattr(media, "photo") and media.photo:
                # Photo ID + Access Hash is unique
                return f"photo_{media.photo.id}_{media.photo.access_hash}"
        except Exception:
            pass
            
        return None

    def _get_part_size(self, file_size: int) -> int:
        """Determine optimal part size for downloading."""
        # Use configured value if set (and not 0/auto)
        if self.config and hasattr(self.config, "performance"):
            configured_kb = getattr(self.config.performance, "part_size_kb", 0)
            if configured_kb > 0:
                return configured_kb
        
        # Auto-tuning
        if file_size < 10 * 1024 * 1024:  # < 10MB
            return 128
        elif file_size < 100 * 1024 * 1024:  # < 100MB
            return 256
        else:  # > 100MB
            return 512

    async def download_media(
        self,
        message: Message,
        progress_queue: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Главный метод загрузки медиафайла из сообщения.

        Использует трёх-уровневую дедупликацию:
        1. TIER 1: Hash-based (content matching) - highest precision
        2. TIER 2: ID-based (existing) - fast fallback
        3. TIER 3: Download - if both caches miss

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

        # Store file_hash for later cache updates
        file_hash: Optional[str] = None
        
        # === TIER 1: Hash-Based Deduplication (B-6) ===
        if self._hash_dedup:
            try:
                # Get file hash from Telegram API
                file_hash = await self._hash_dedup.get_file_hash(
                    client=self.client,
                    media=message.media,
                    timeout=self.config.performance.hash_api_timeout
                )
                
                if file_hash:
                    # Check hash cache
                    cached_path = await self._hash_dedup.check_cache(file_hash)
                    if cached_path:
                        # CACHE HIT: Reuse existing file
                        logger.info(
                            f"✅ Hash dedup HIT: msg {message.id} -> "
                            f"{cached_path.name}"
                        )
                        return cached_path
                    else:
                        logger.debug(
                            f"Hash dedup MISS: {file_hash[:16]}... (will check ID cache)"
                        )
            except Exception as e:
                logger.warning(f"Hash dedup failed: {e}, falling back to ID-based")
        
        # === TIER 2: ID-Based Deduplication (Existing) ===
        file_key = self._get_file_key(message)
        if file_key:
            # 1. Check in-memory cache
            if file_key in self._downloaded_cache:
                cached_path = self._downloaded_cache[file_key]
                if cached_path.exists() and cached_path.stat().st_size > 0:
                    logger.debug(f"♻️ ID dedup hit (memory): {file_key}")
                    # Update hash cache if we have the hash
                    if self._hash_dedup and file_hash:
                        self._hash_dedup.add_to_cache(file_hash, cached_path)
                    return cached_path
            
            # 2. Check persistent cache manager
            if self.cache_manager and hasattr(self.cache_manager, "get_file_path"):
                cached_path_str = await self.cache_manager.get_file_path(file_key)
                if cached_path_str:
                    cached_path = Path(cached_path_str)
                    if cached_path.exists() and cached_path.stat().st_size > 0:
                        logger.debug(f"♻️ ID dedup hit (persistent): {file_key}")
                        # Update memory cache
                        self._downloaded_cache[file_key] = cached_path
                        # Update hash cache if we have the hash
                        if self._hash_dedup and file_hash:
                            self._hash_dedup.add_to_cache(file_hash, cached_path)
                        return cached_path

        # === TIER 3: Download ===
        expected_size = getattr(message.file, "size", 0)
        if expected_size == 0:
            logger.warning(f"Message {message.id} has zero file size")
            return None

        file_size_mb = expected_size / (1024 * 1024)
        logger.info(
            f"Downloading message {message.id}: {file_size_mb:.2f} MB"
        )

        result_path = None
        # Используем persistent download для всех файлов (guaranteed completion)
        if self._persistent_enabled:
            result_path = await self._persistent_download(
                message, expected_size, progress_queue, task_id
            )
        else:
            result_path = await self._standard_download(
                message, expected_size, progress_queue, task_id
            )
            
        # === Update ALL Caches on Success ===
        if result_path and result_path.exists():
            # Update ID cache (existing)
            if file_key:
                self._downloaded_cache[file_key] = result_path
                if self.cache_manager and hasattr(self.cache_manager, "store_file_path"):
                    await self.cache_manager.store_file_path(file_key, str(result_path))
            
            # Update hash cache (B-6 new)
            if self._hash_dedup and file_hash:
                self._hash_dedup.add_to_cache(file_hash, result_path)
                logger.debug(f"Updated hash cache: {file_hash[:16]}... -> {result_path.name}")
                
        return result_path

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

        Raises:
            TelegramServerError: If Telegram servers are having persistent issues
        """
        self._persistent_download_attempts += 1

        temp_path = self.temp_dir / f"persistent_{message.id}.tmp"
        # Reduced from 50 to 10 - download_queue already handles retries at task level
        # with its own retry logic. No need for double retry loops.
        MAX_PERSISTENT_ATTEMPTS = 10
        max_consecutive_failures = 3  # Reduced from 5
        max_telegram_server_errors = 3  # Fast-fail on server issues (reduced from 5)
        attempt = 0
        consecutive_failures = 0
        telegram_server_error_count = 0

        # Select client for download (round-robin if workers available)
        download_client = self.client
        if self.worker_clients:
            # Simple round-robin based on message ID to distribute load
            client_idx = message.id % len(self.worker_clients)
            download_client = self.worker_clients[client_idx]

        # Auto-wrap in Takeout if available (Crucial for speed)
        # Check if client has takeout_id (e.g. TakeoutSessionWrapper)
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
                                part_size_kb=self._get_part_size(expected_size),
                            ),
                            timeout=chunk_timeout,
                        )
                    except Exception:
                        # Fallback to download_media if download_file fails (e.g. location extraction issue)
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
                error_str = str(e)
                logger.warning(
                    f"Persistent download attempt {attempt} failed with error: {type(e).__name__}: {e}"
                )

                # Check for Telethon's internal retry exhaustion FIRST
                # "Request was unsuccessful 26 time(s)" means Telethon already tried 26 times internally
                # No point in retrying - this is a final failure from Telethon
                exhausted_match = re.search(TELETHON_EXHAUSTED_PATTERN, error_str)
                if exhausted_match:
                    internal_retries = int(exhausted_match.group(1))
                    logger.error(
                        f"❌ Telethon exhausted {internal_retries} internal retries for message {message.id}. "
                        f"This is a final failure - not retrying. "
                        f"Downloaded: {temp_path.stat().st_size if temp_path.exists() else 0} bytes"
                    )
                    # Return partial file if exists and has data, otherwise None
                    if temp_path.exists() and temp_path.stat().st_size > 0:
                        final_size = temp_path.stat().st_size
                        if expected_size > 0:
                            completion = (final_size / expected_size) * 100
                            logger.warning(
                                f"⚠️ Returning partial file ({completion:.1f}% complete)"
                            )
                        return temp_path
                    return None

                # Check for Telegram server-side errors - fast fail on these
                if is_telegram_server_error(e):
                    telegram_server_error_count += 1
                    logger.warning(
                        f"Telegram server error detected ({telegram_server_error_count}/{max_telegram_server_errors})"
                    )

                    if telegram_server_error_count >= max_telegram_server_errors:
                        logger.error(
                            f"❌ Telegram servers are having issues. "
                            f"Giving up on message {message.id} after {telegram_server_error_count} server errors. "
                            f"Downloaded: {temp_path.stat().st_size if temp_path.exists() else 0} bytes"
                        )
                        raise TelegramServerError(
                            f"Telegram servers unavailable after {telegram_server_error_count} attempts: {error_str}"
                        )

                    # Longer backoff for server errors
                    backoff_time = min(30 * telegram_server_error_count, 120)
                    logger.info(
                        f"Backing off for {backoff_time}s due to Telegram server issues"
                    )
                    await asyncio.sleep(backoff_time)
                    consecutive_failures += 1
                    continue

                # Special handling for DC migration errors
                if "FileMigrateError" in str(type(e)) or "DC" in error_str:
                    logger.info(
                        "DC migration detected, extending timeout for next attempt"
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

        # Все попытки исчерпаны
        logger.error(
            f"❌ Persistent download failed after {MAX_PERSISTENT_ATTEMPTS} attempts for message {message.id}"
        )
        return None

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
        # Reduced from 15 to 5 - download_queue handles task-level retries
        max_retries = 5

        # Адаптивный таймаут в зависимости от размера файла
        base_timeout = min(600, max(180, file_size_mb * 30))  # Reduced timeouts

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
                    try:
                        location = utils.get_input_location(message.media)
                        await asyncio.wait_for(
                            download_client.download_file(
                                location,
                                file=temp_path,
                                progress_callback=progress_callback,
                                part_size_kb=self._get_part_size(expected_size),
                            ),
                            timeout=base_timeout,
                        )
                    except Exception:
                        # Fallback to download_media if download_file fails (e.g. location extraction issue)
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
        
        # B-6: Include hash dedup stats
        hash_dedup_stats = self.get_hash_dedup_stats() if self._hash_dedup else {}

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
            "hash_deduplication": hash_dedup_stats,  # B-6: Hash-based dedup stats
        }
    
    def get_hash_dedup_stats(self) -> Dict[str, int]:
        """
        Get hash-based deduplication statistics.
        
        Returns:
            Dictionary with hash dedup stats or empty dict if disabled
        """
        if self._hash_dedup:
            return self._hash_dedup.get_stats()
        return {}

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
