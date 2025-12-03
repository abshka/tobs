"""
Media processor manager.

Main orchestrator that coordinates all media processing operations.
Uses composition of modular components instead of inheritance.
"""

import asyncio
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiofiles
import aiofiles.os
from loguru import logger
from telethon.tl.types import Message

from src.utils import sanitize_filename

from .cache import MediaCache
from .download_queue import MediaDownloadQueue
from .downloader import MediaDownloader
from .hardware import HardwareAccelerationDetector
from .metadata import MetadataExtractor
from .models import ProcessingSettings, ProcessingTask
from .processors.audio import AudioProcessor
from .processors.image import ImageProcessor
from .processors.video import VideoProcessor
from .validators import MediaValidator


class MediaProcessor:
    """
    Главный оркестратор медиа-процессинга.

    Использует композицию модульных компонентов для обработки медиа.
    Обеспечивает обратную совместимость с существующим API.
    """

    def __init__(
        self,
        config,
        client,
        cache_manager=None,
        connection_manager=None,
        max_workers: int = 4,
        temp_dir: Optional[Path] = None,
        enable_smart_caching: bool = True,
        worker_clients: list = None,
    ):
        """
        Инициализация медиа-процессора.

        Args:
            config: Конфигурация приложения
            client: Telegram клиент
            cache_manager: Менеджер кэша (опционально)
            connection_manager: Менеджер соединений (опционально)
            max_workers: Количество воркеров для обработки
            temp_dir: Временная директория
            enable_smart_caching: Включить умное кэширование
            worker_clients: Список дополнительных клиентов (воркеров)
        """
        self.config = config
        self.client = client
        self.worker_clients = worker_clients or []
        self.cache_manager = cache_manager
        self.connection_manager = connection_manager
        self.max_workers = max_workers
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "tobs_media"
        self.enable_smart_caching = enable_smart_caching

        # Создание временной директории
        self.temp_dir.mkdir(exist_ok=True)

        # Пулы потоков для разных типов операций
        self.io_executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="media_io"
        )
        self.cpu_executor = ThreadPoolExecutor(
            max_workers=max_workers // 2 or 1, thread_name_prefix="media_cpu"
        )
        self.ffmpeg_executor = ThreadPoolExecutor(
            max_workers=max_workers // 2 or 1, thread_name_prefix="ffmpeg"
        )

        # Настройки обработки по умолчанию
        self.default_processing = ProcessingSettings()

        # Инициализация модульных компонентов
        self._hw_detector = HardwareAccelerationDetector(config)
        self._metadata_extractor = MetadataExtractor(self.io_executor)
        self._validator = MediaValidator(self.io_executor)
        self._cache = MediaCache(cache_manager)

        # Downloader будет инициализирован после start()
        self._downloader: Optional[MediaDownloader] = None

        # Процессоры будут инициализированы после start()
        self._video_processor: Optional[VideoProcessor] = None
        self._audio_processor: Optional[AudioProcessor] = None
        self._image_processor: Optional[ImageProcessor] = None

        # Транскрайбер (опционально, если включена транскрипция)
        self._transcriber = None

        # Очередь обработки
        self._processing_queue: asyncio.Queue = asyncio.Queue()

        # 🚀 Deferred Processing Queue
        self._pending_tasks: List[ProcessingTask] = []

        # 🚀 Background Download Queue (async media downloads)
        self._download_queue: Optional[MediaDownloadQueue] = None

        # Статистика
        self._processed_files = 0
        self._cache_hits = 0
        self._total_processing_time = 0.0
        self._bytes_processed = 0

        # ⚠️ Обработка ошибок (Phase 2 Task 2.3)
        self._failed_tasks = 0  # Счётчик неудачных задач
        self._worker_errors: Dict[str, List[str]] = {}  # Ошибки по воркеру
        self._failed_items_log: List[
            Dict[str, Any]
        ] = []  # Лог failed items для диагностики

        # Состояние
        self._shutdown_event = asyncio.Event()
        self._worker_tasks: List[asyncio.Task] = []
        self._hw_acceleration_ready = False

        logger.info("MediaProcessor initialized with modular architecture")

    async def start(self):
        """Инициализация процессора и запуск воркеров."""
        try:
            # Определение аппаратного ускорения
            try:
                available_encoders = (
                    await self._hw_detector.detect_hardware_acceleration()
                )
                self._hw_acceleration_ready = available_encoders.get("vaapi", False)

                if self._hw_acceleration_ready:
                    logger.info("VA-API hardware acceleration is ready")
                else:
                    logger.info("VA-API not available, using software encoding only")
            except Exception as e:
                logger.warning(f"Hardware acceleration detection failed: {e}")
                self._hw_acceleration_ready = False

            # Инициализация downloader
            self._downloader = MediaDownloader(
                connection_manager=self.connection_manager,
                temp_dir=self.temp_dir,
                client=self.client,
                worker_clients=self.worker_clients,
            )

            # 🚀 Инициализация Background Download Queue (если включено)
            if getattr(self.config, "async_media_download", True):
                workers = getattr(self.config, "async_download_workers", 3)
                self._download_queue = MediaDownloadQueue(
                    downloader=self._downloader,
                    max_workers=workers,
                    max_queue_size=1000,
                )
                await self._download_queue.start()
                logger.info(f"🚀 Background download queue started with {workers} workers")

            # Инициализация процессоров
            self._video_processor = VideoProcessor(
                io_executor=self.io_executor,
                cpu_executor=self.cpu_executor,
                hw_detector=self._hw_detector,
                metadata_extractor=self._metadata_extractor,
                config=self.config,
                settings=self.default_processing,
            )

            self._audio_processor = AudioProcessor(
                io_executor=self.io_executor,
                cpu_executor=self.cpu_executor,
                validator=self._validator,
                config=self.config,
                settings=self.default_processing,
            )

            self._image_processor = ImageProcessor(
                io_executor=self.io_executor,
                cpu_executor=self.cpu_executor,
                config=self.config,
                settings=self.default_processing,
            )

            # Инициализация транскрайбера (если включено) - v5.0.0
            transcription_config = getattr(self.config, "transcription", None)
            if transcription_config and transcription_config.enabled:
                try:
                    from .processors import WhisperTranscriber

                    logger.info(
                        f"Initializing Whisper transcriber: "
                        f"device={transcription_config.device}, "
                        f"compute_type={transcription_config.compute_type}"
                    )

                    # Create transcriber
                    self._transcriber = WhisperTranscriber(
                        device=transcription_config.device,
                        compute_type=transcription_config.compute_type,
                        batch_size=transcription_config.batch_size,
                        duration_threshold=transcription_config.duration_threshold,
                        use_batched=transcription_config.use_batched,
                        enable_cache=transcription_config.cache_enabled,
                    )

                    # Lazy loading: model will be loaded on first transcription request
                    logger.info("Transcriber configured (model will load on first use)")

                except Exception as e:
                    logger.error(f"Failed to initialize transcriber: {e}")
                    logger.warning("Transcription will be disabled for this session")
                    self._transcriber = None

            # Инициализация процессоров (если требуется)
            try:
                await self._video_processor.initialize()
            except Exception as e:
                logger.warning(f"Video processor initialization failed: {e}")
                logger.info("Video processing will use fallback mode")

            # Запуск фоновых воркеров
            for i in range(self.max_workers):
                task = asyncio.create_task(self._processing_worker(f"worker_{i}"))
                self._worker_tasks.append(task)

            logger.info(f"Media processor started with {self.max_workers} workers")

        except Exception as e:
            logger.error(f"Failed to start media processor: {e}")
            self._hw_acceleration_ready = False
            raise

    async def get_media_metadata(self, message: Message) -> Optional[Dict[str, Any]]:
        """
        Получение метаданных медиа из сообщения без загрузки файла.

        Args:
            message: Telegram сообщение

        Returns:
            Словарь с метаданными или None
        """
        if not message.file:
            return None

        try:
            metadata = {
                "size": message.file.size,
                "mime_type": message.file.mime_type,
                "name": message.file.name,
                "attributes": getattr(message.file, "attributes", None),
            }
            return metadata
        except Exception as e:
            logger.error(f"Error getting media metadata for message {message.id}: {e}")
            return None

    async def download_and_process_media(
        self,
        message: Message,
        entity_id: Union[str, int],
        entity_media_path: Path,
        progress_queue=None,
        task_id=None,
        processing_settings: Optional[ProcessingSettings] = None,
    ) -> List[Path]:
        """
        Загрузка и оптимизация медиа из сообщения.

        Args:
            message: Telegram сообщение
            entity_id: ID сущности (канал/чат)
            entity_media_path: Путь для сохранения медиа
            progress_queue: Очередь для прогресса
            task_id: ID задачи
            processing_settings: Настройки обработки

        Returns:
            Список путей к обработанным файлам (или placeholder-путей при async mode)
        """
        if not self.config.any_media_download_enabled:
            return []

        try:
            # Получение списка медиа
            media_items = await self._extract_media_from_message(message)
            if not media_items:
                return []

            # Настройки обработки
            proc_settings = processing_settings or self.default_processing

            # 🚀 Async Download Mode: queue downloads instead of waiting
            if self._download_queue:
                return await self._queue_media_downloads(
                    media_items, entity_id, entity_media_path
                )

            # Synchronous mode (fallback): process each media file
            tasks = []
            for media_type, msg in media_items:
                task = self._process_single_media(
                    msg,
                    entity_id,
                    media_type,
                    entity_media_path,
                    proc_settings,
                    progress_queue,
                    task_id,
                )
                tasks.append(task)

            # Выполнение с контролем параллелизма
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Фильтрация успешных результатов
            successful_paths = []
            for result in results:
                if isinstance(result, Path) and result.exists():
                    successful_paths.append(result)
                elif isinstance(result, Exception):
                    logger.warning(f"Media processing failed: {result}")

            return successful_paths

        except Exception as e:
            logger.error(
                f"Failed to download and process media for message {message.id}: {e}"
            )
            return []

    async def _queue_media_downloads(
        self,
        media_items: List[Tuple[str, Message]],
        entity_id: Union[str, int],
        entity_media_path: Path,
    ) -> List[Path]:
        """
        Queue media downloads to background workers.
        
        Returns placeholder paths immediately - actual files will be downloaded
        by background workers.
        """
        result_paths = []
        
        for media_type, msg in media_items:
            try:
                # Check if file already exists
                if not hasattr(msg, "file") or not msg.file:
                    continue
                    
                # Generate filename
                filename = await self._generate_filename(msg, media_type)
                if not filename:
                    continue
                
                # Output path
                type_subdir = entity_media_path / media_type
                await aiofiles.os.makedirs(type_subdir, exist_ok=True)
                output_path = type_subdir / filename
                
                # Skip if file already exists with correct size
                if output_path.exists():
                    expected_size = getattr(msg.file, "size", 0)
                    try:
                        actual_size = output_path.stat().st_size
                        if expected_size > 0 and actual_size >= expected_size * 0.95:
                            logger.debug(f"File already exists: {output_path}")
                            result_paths.append(output_path)
                            continue
                    except Exception:
                        pass
                
                # Check cache
                if self.enable_smart_caching:
                    cached_path = await self._cache.check_cache(msg.id, output_path)
                    if cached_path:
                        self._cache_hits += 1
                        result_paths.append(cached_path)
                        continue
                
                # Queue for background download
                await self._download_queue.enqueue(
                    message=msg,
                    entity_id=entity_id,
                    output_path=output_path,
                    media_type=media_type,
                )
                
                # Return the expected output path (file will appear later)
                result_paths.append(output_path)
                
            except Exception as e:
                logger.warning(f"Failed to queue media download: {e}")
        
        return result_paths

    async def _extract_media_from_message(
        self, message: Message
    ) -> List[Tuple[str, Message]]:
        """Извлечение медиа из сообщения."""
        media_items: List[Tuple[str, Message]] = []

        if not hasattr(message, "media") or not message.media:
            return media_items

        # Определение типа медиа
        media_type = self._determine_media_type(message)

        # Check extension filters first (tdl-style)
        filename = await self._generate_filename(message, media_type or "unknown")
        if filename and not self._is_extension_allowed(filename):
            logger.debug(f"Skipping {filename} due to extension filter")
            return media_items

        if media_type and self._should_download_media_type(media_type):
            media_items.append((media_type, message))
        else:
            logger.debug(f"Skipping media type {media_type} (disabled in config)")

        return media_items

    def _is_extension_allowed(self, filename: str) -> bool:
        """Check if file extension is allowed by include/exclude lists."""
        if not filename:
            return True

        # Extract extension without dot
        parts = filename.rsplit(".", 1)
        if len(parts) < 2:
            return True  # No extension, allow by default (or maybe filter?)

        ext = parts[1].lower()

        # Exclude takes precedence
        if self.config.exclude_extensions and ext in self.config.exclude_extensions:
            return False

        # Include acts as a whitelist if present
        if self.config.include_extensions and ext not in self.config.include_extensions:
            return False

        return True

    def _should_download_media_type(self, media_type: str) -> bool:
        """Check if the given media type should be downloaded based on config."""
        if media_type in ["photo", "image"]:
            return self.config.download_photos
        elif media_type == "video":
            return self.config.download_videos
        elif media_type == "audio":
            return self.config.download_audio
        else:
            # "document", "webpage", "unknown", stickers, etc.
            return self.config.download_other

    def _determine_media_type(self, message: Message) -> Optional[str]:
        """Определение типа медиа."""
        if not hasattr(message, "media") or not message.media:
            return None

        media = message.media
        media_type = type(media).__name__

        if media_type == "MessageMediaPhoto":
            return "photo"
        elif media_type == "MessageMediaDocument":
            if hasattr(media, "document") and media.document:
                mime_type = getattr(media.document, "mime_type", "")
                if mime_type.startswith("video/"):
                    return "video"
                elif mime_type.startswith("audio/"):
                    return "audio"
                elif mime_type.startswith("image/"):
                    return "image"
                else:
                    return "document"
        elif media_type == "MessageMediaWebPage":
            return "webpage"

        return "unknown"

    async def _process_single_media(
        self,
        message: Message,
        entity_id: Union[str, int],
        media_type: str,
        entity_media_path: Path,
        processing_settings: ProcessingSettings,
        progress_queue=None,
        task_id=None,
    ) -> Optional[Path]:
        """Обработка одного медиа файла."""
        try:
            # Check if message has file attribute
            if not hasattr(message, "file") or not message.file:
                logger.warning(
                    f"Message {message.id} has no file attribute or file is None"
                )
                return None

            # Генерация имени файла
            filename = await self._generate_filename(message, media_type)
            if not filename:
                return None

            # Путь для сохранения
            type_subdir = entity_media_path / media_type
            await aiofiles.os.makedirs(type_subdir, exist_ok=True)
            output_path = type_subdir / filename

            # Проверка существования файла
            if output_path.exists():
                # Check if we should have processed this file
                should_process = False
                if media_type == "video" and getattr(
                    self.config, "process_video", False
                ):
                    should_process = True
                elif media_type == "audio" and getattr(
                    self.config, "process_audio", True
                ):
                    should_process = True
                elif media_type in ["photo", "image"] and getattr(
                    self.config, "process_images", True
                ):
                    should_process = True

                # If not processing, verify size
                if not should_process:
                    expected_size = (
                        getattr(message.file, "size", 0)
                        if hasattr(message, "file")
                        else 0
                    )
                    stat = output_path.stat()
                    if expected_size > 0 and stat.st_size != expected_size:
                        logger.warning(
                            f"File exists but size mismatch: {stat.st_size} != {expected_size}. Re-downloading."
                        )
                        # Fall through to download
                    else:
                        logger.debug(
                            f"Media file already exists and size matches: {output_path}"
                        )
                        return output_path
                else:
                    # If processing is enabled, we assume existing file is correct if non-zero
                    if output_path.stat().st_size > 0:
                        logger.debug(
                            f"Processed media file already exists: {output_path}"
                        )
                        return output_path

            # Проверка кэша
            if self.enable_smart_caching:
                cached_path = await self._cache.check_cache(message.id, output_path)
                if cached_path:
                    self._cache_hits += 1
                    return cached_path

            # Загрузка файла через MediaDownloader
            if not self._downloader:
                logger.error("MediaDownloader not initialized")
                return None

            temp_path = await self._downloader.download_media(
                message=message,
                progress_queue=progress_queue,
                task_id=task_id,
            )

            if not temp_path or not temp_path.exists():
                logger.error("Download failed")
                return None

            # Проверяем размер загруженного файла
            downloaded_size = temp_path.stat().st_size
            expected_size = (
                getattr(message.file, "size", 0) if hasattr(message, "file") else 0
            )

            if expected_size > 0 and downloaded_size < expected_size * 0.95:
                logger.error(
                    f"Downloaded file is incomplete! Downloaded: {downloaded_size} bytes, "
                    f"Expected: {expected_size} bytes ({(downloaded_size / expected_size) * 100:.1f}%)"
                )
                return None

            logger.info(
                f"File downloaded successfully: {downloaded_size / 1024 / 1024:.1f}MB"
            )

            # Получение метаданных через MetadataExtractor
            # Only try to extract metadata if file is not empty
            if downloaded_size > 0:
                metadata = await self._metadata_extractor.get_metadata(
                    temp_path, media_type
                )
            else:
                metadata = None

            # Создание задачи обработки
            processing_task = ProcessingTask(
                input_path=temp_path,
                output_path=output_path,
                media_type=media_type,
                processing_settings=processing_settings,
                metadata=metadata,
            )

            # 🚀 Deferred Processing Logic
            if self.config.deferred_processing:
                # Move temp file to final location immediately (raw)
                # We will process it in-place later
                try:
                    # Ensure parent dir exists
                    await aiofiles.os.makedirs(output_path.parent, exist_ok=True)

                    # Move file
                    shutil.move(str(temp_path), str(output_path))

                    # Update task to point to the new location as input
                    processing_task.input_path = output_path

                    # Add to pending list
                    self._pending_tasks.append(processing_task)

                    logger.info(f"Deferred processing for {filename}. Saved raw file.")
                    return output_path
                except Exception as e:
                    logger.error(f"Failed to move deferred file: {e}")
                    return None

            # Добавление в очередь обработки
            await self._processing_queue.put(processing_task)

            # Ожидание результата
            result_path = await self._wait_for_processing_result(processing_task)

            # Обновление статистики
            if result_path and result_path.exists():
                result_size = result_path.stat().st_size

                if result_size == 0:
                    logger.error("Result file is empty! Processing may have failed.")
                    return None

                if expected_size > 0 and result_size < expected_size * 0.01:
                    logger.error(
                        f"Result file suspiciously small! Result: {result_size} bytes"
                    )
                    return None

                self._processed_files += 1
                self._bytes_processed += result_size

                # Сохранение в кэш
                if self.enable_smart_caching:
                    await self._cache.save_to_cache(message.id, result_path)

                logger.info(
                    f"Media processing completed: {result_size / 1024 / 1024:.1f}MB -> {result_path}"
                )

                # Очистка временного файла
                try:
                    if temp_path and temp_path.exists():
                        await aiofiles.os.unlink(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp file {temp_path}: {e}")

                return result_path
            else:
                logger.error("Processing failed or result file not found")
                return None

        except Exception as e:
            logger.error(f"Failed to process media for message {message.id}: {e}")
            return None

    async def _generate_filename(
        self, message: Message, media_type: str
    ) -> Optional[str]:
        """Генерация имени файла."""
        try:
            base_name = f"msg_{message.id}"

            extension_map = {
                "photo": ".jpg",
                "image": ".jpg",
                "video": ".mp4",
                "audio": ".mp3",
                "document": "",
            }

            extension = extension_map.get(media_type, "")

            # Try to get extension from mime_type
            if hasattr(message, "file") and hasattr(message.file, "mime_type"):
                import mimetypes

                ext = mimetypes.guess_extension(message.file.mime_type)
                if ext:
                    extension = ext

            # Попытка получить оригинальное имя файла
            if hasattr(message, "media") and hasattr(message.media, "document"):
                document = message.media.document
                if hasattr(document, "attributes"):
                    for attr in document.attributes:
                        if hasattr(attr, "file_name") and attr.file_name:
                            original_name = sanitize_filename(attr.file_name)
                            name, ext = os.path.splitext(original_name)
                            return f"{base_name}_{name}{ext}"

            return f"{base_name}{extension}"

        except Exception as e:
            logger.error(f"Failed to generate filename: {e}")
            return f"msg_{message.id}.bin"

    async def _wait_for_processing_result(self, task: ProcessingTask) -> Optional[Path]:
        """Ожидание результата обработки с проверкой завершения."""
        max_wait = 300  # 5 минут
        check_interval = 1
        last_size = 0
        stable_count = 0
        required_stable_checks = 3

        logger.debug(f"Waiting for processing result: {task.output_path}")

        for attempt in range(max_wait):
            if task.output_path.exists():
                current_size = task.output_path.stat().st_size

                if current_size == last_size and current_size > 0:
                    stable_count += 1
                    if stable_count >= required_stable_checks:
                        logger.debug(
                            f"Processing completed: {current_size / 1024 / 1024:.1f}MB"
                        )
                        return task.output_path
                else:
                    stable_count = 0
                    last_size = current_size

            await asyncio.sleep(check_interval)

        logger.warning(f"Processing timeout for {task.output_path}")
        return task.output_path if task.output_path.exists() else None

    def _handle_worker_error(
        self,
        worker_name: str,
        task: Optional[ProcessingTask],
        error: Exception,
        error_type: str = "task",
    ):
        """
        Централизованная обработка ошибок воркера (Phase 2 Task 2.3).

        Args:
            worker_name: Имя воркера
            task: Задача обработки (может быть None для ошибок очереди)
            error: Исключение
            error_type: "queue", "task", или "worker"
        """
        # Логирование с полным traceback
        if error_type == "queue":
            logger.warning(f"Worker {worker_name}: Queue timeout, continuing...")
        elif error_type == "task":
            logger.error(
                f"Worker {worker_name}: Task failed (File: {task.input_path.name if task else 'N/A'}), "
                f"Type: {task.media_type if task else 'N/A'}, "
                f"Attempt: {task.attempts if task else 'N/A'}, "
                f"Error: {error}"
            )
            # Log full traceback for task errors in DEBUG mode
            logger.opt(exception=True).debug(f"Task error details:")
            self._failed_tasks += 1

            # Логирование в failed items
            if task:
                self._failed_items_log.append(
                    {
                        "task_file": task.input_path.name,
                        "media_type": task.media_type,
                        "input_path": str(task.input_path),
                        "error": str(error),
                        "timestamp": time.time(),
                        "attempts": task.attempts,
                    }
                )
        else:  # worker error - these are unexpected and should show full trace
            logger.error(
                f"Worker {worker_name}: Unexpected error: {type(error).__name__}: {error}"
            )
            # ALWAYS log full traceback for worker errors (not just in DEBUG)
            logger.opt(exception=True).error(f"Worker error traceback:")

        # Обновление словаря ошибок по воркеру
        if worker_name not in self._worker_errors:
            self._worker_errors[worker_name] = []
        self._worker_errors[worker_name].append(f"{error_type}: {str(error)}")

        # 限制размер лога (не более 100 ошибок на воркер)
        if len(self._worker_errors[worker_name]) > 100:
            self._worker_errors[worker_name] = self._worker_errors[worker_name][-100:]

    async def _processing_worker(self, worker_name: str):
        """
        Фоновый воркер для обработки медиа (Phase 2 Task 2.3: Enhanced error handling).
        """
        logger.debug(f"Processing worker {worker_name} started")

        while not self._shutdown_event.is_set():
            task = None  # Для использования в finally блоке
            try:
                # Получение задачи из очереди с таймаутом
                try:
                    task = await asyncio.wait_for(
                        self._processing_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # Нормальное поведение - очередь пуста, продолжаем ждать
                    continue

                # Обработка задачи
                start_time = time.time()

                try:
                    success = await self._execute_processing_task(task, worker_name)

                    processing_time = time.time() - start_time
                    self._total_processing_time += processing_time

                    if success:
                        logger.debug(
                            f"Worker {worker_name} completed task {task.input_path.name} in {processing_time:.2f}s"
                        )
                    else:
                        # Задача завершилась, но результат неудачен - пытаемся retry
                        task.attempts += 1
                        if task.attempts < task.max_attempts:
                            logger.warning(
                                f"Worker {worker_name}: Task {task.input_path.name} retry "
                                f"{task.attempts}/{task.max_attempts}"
                            )
                            await self._processing_queue.put(task)
                        else:
                            # Max retries достигнут
                            logger.error(
                                f"Worker {worker_name}: Task {task.input_path.name} failed after "
                                f"{task.attempts} attempts, giving up"
                            )
                            self._handle_worker_error(
                                worker_name,
                                task,
                                Exception("Max retries exceeded"),
                                "task",
                            )

                except Exception as e:
                    # Исключение во время обработки
                    self._handle_worker_error(worker_name, task, e, "task")

                    task.attempts += 1
                    if task.attempts < task.max_attempts:
                        logger.info(
                            f"Requeueing task {task.input_path.name} for retry (attempt {task.attempts}/{task.max_attempts})"
                        )
                        await self._processing_queue.put(task)

                finally:
                    # Важно: всегда вызываем task_done, даже при ошибках
                    if task:
                        try:
                            self._processing_queue.task_done()
                        except ValueError:
                            # task_done() уже был вызван или очередь была очищена
                            pass

            except Exception as e:
                # Критическая ошибка в worker loop (не в обработке задачи)
                self._handle_worker_error(worker_name, task, e, "worker")
                await asyncio.sleep(1)

        logger.debug(f"Processing worker {worker_name} stopped")

    async def _execute_processing_task(
        self, task: ProcessingTask, worker_name: str
    ) -> bool:
        """Выполнение задачи обработки."""
        try:
            if not task.input_path.exists():
                logger.error(f"Input file not found: {task.input_path}")
                return False

            source_size = task.input_path.stat().st_size
            success = False

            # Выбор процессора по типу медиа
            if task.media_type == "video":
                if not self._video_processor:
                    logger.error("VideoProcessor not initialized")
                    return False
                success = await self._video_processor.process(task, worker_name)

            elif task.media_type == "audio":
                if not self._audio_processor:
                    logger.error("AudioProcessor not initialized")
                    return False
                success = await self._audio_processor.process(task, worker_name)

            elif task.media_type in ["photo", "image"]:
                if not self._image_processor:
                    logger.error("ImageProcessor not initialized")
                    return False
                success = await self._image_processor.process(task, worker_name)

            else:
                # Простое копирование для неизвестных типов
                logger.info(f"Unknown media type {task.media_type}, copying file")
                success = await self._copy_file(task)

            # Проверяем результат обработки
            if success and task.output_path.exists():
                result_size = task.output_path.stat().st_size

                if result_size < source_size * 0.01:
                    logger.warning(
                        "Processing produced suspicious result, using fallback copy"
                    )
                    success = await self._copy_file(task)
                elif result_size == 0:
                    logger.warning(
                        "Processing produced empty file, using fallback copy"
                    )
                    success = await self._copy_file(task)

            elif success:
                logger.warning("Processing claimed success but no output file found")
                success = await self._copy_file(task)

            return success

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            try:
                logger.info("Attempting fallback file copy...")
                return await self._copy_file(task)
            except Exception as e2:
                logger.error(f"Fallback copy also failed: {e2}")
                return False

    async def _copy_file(self, task: ProcessingTask) -> bool:
        """Простое копирование файла при невозможности обработки."""
        try:
            async with aiofiles.open(task.input_path, "rb") as src:
                async with aiofiles.open(task.output_path, "wb") as dst:
                    while chunk := await src.read(8192 * 1024):
                        await dst.write(chunk)

            if task.output_path.exists() and task.output_path.stat().st_size > 0:
                logger.info(f"File copied successfully: {task.output_path}")
                return True

            return False

        except Exception as e:
            logger.error(f"File copy failed: {e}")
            return False

    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики процессора."""
        avg_processing_time = self._total_processing_time / max(
            self._processed_files, 1
        )

        cache_hit_rate = (
            self._cache_hits / max(self._processed_files + self._cache_hits, 1) * 100
        )

        # Объединяем статистику от всех компонентов
        stats = {
            "processed_files": self._processed_files,
            "cache_hits": self._cache_hits,
            "cache_hit_rate_percent": cache_hit_rate,
            "avg_processing_time_seconds": avg_processing_time,
            "total_bytes_processed": self._bytes_processed,
            "hardware_acceleration": self._hw_detector.available_encoders,
            "queue_size": self._processing_queue.qsize(),
            "active_workers": len([t for t in self._worker_tasks if not t.done()]),
        }

        # Добавляем статистику от downloader
        if self._downloader:
            stats["downloader"] = self._downloader.get_statistics()

        # Добавляем статистику от процессоров
        if self._video_processor:
            stats["video_processor"] = self._video_processor.get_statistics()

        if self._audio_processor:
            stats["audio_processor"] = self._audio_processor.get_statistics()

        if self._image_processor:
            stats["image_processor"] = self._image_processor.get_statistics()

        return stats

    async def is_idle(self) -> bool:
        """
        Проверяет, завершена ли вся работа медиа-процессора.

        Returns:
            True если очередь пуста и нет активных задач обработки
        """
        # Check processing queue
        if self._processing_queue.qsize() > 0:
            return False

        # 🚀 Check download queue
        if self._download_queue:
            pending = self._download_queue.get_pending_count()
            in_progress = self._download_queue.get_in_progress_count()
            if pending > 0 or in_progress > 0:
                return False

        try:
            await asyncio.wait_for(self._processing_queue.join(), timeout=0.1)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_downloads(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for all background downloads to complete.
        
        Args:
            timeout: Maximum time to wait (None = wait forever)
            
        Returns:
            True if all downloads completed, False if timeout
        """
        if not self._download_queue:
            return True
            
        logger.info("⏳ Waiting for background downloads to complete...")
        result = await self._download_queue.wait_all(timeout=timeout)
        
        if result:
            self._download_queue.log_stats()
            logger.info("✅ All downloads completed")
        else:
            self._download_queue.log_stats()
            logger.warning("⚠️ Download wait timed out")
            
        return result

    async def wait_until_idle(
        self, timeout: float = 300.0, progress_callback=None
    ) -> bool:
        """
        Ожидает завершения всех задач медиа-процессора.

        Args:
            timeout: Максимальное время ожидания в секундах
            progress_callback: Функция для отображения прогресса

        Returns:
            True если все задачи завершены, False при таймауте
        """
        start_time = time.time()
        last_progress_time = start_time

        while time.time() - start_time < timeout:
            if await self.is_idle():
                return True

            current_time = time.time()
            if current_time - last_progress_time >= 15.0:
                elapsed = current_time - start_time
                queue_size = self._processing_queue.qsize()

                stats = await self.get_stats()
                active_workers = stats.get("active_workers", 0)

                if progress_callback:
                    progress_callback(queue_size, elapsed)
                else:
                    logger.info(
                        f"Media processor still working: queue={queue_size}, "
                        f"active_workers={active_workers}, waited={elapsed:.0f}s"
                    )

                last_progress_time = current_time

            await asyncio.sleep(10.0)

        return False

    async def process_pending_tasks(self):
        """
        Process all deferred media tasks.
        Should be called after the main export loop.
        """
        if not self._pending_tasks:
            logger.info("No pending media tasks to process.")
            return

        total = len(self._pending_tasks)
        logger.info(f"🚀 Starting deferred processing for {total} files...")

        for i, task in enumerate(self._pending_tasks):
            try:
                final_path = task.output_path
                if final_path.exists():
                    # Create a temp file for processing input
                    temp_input = final_path.with_suffix(final_path.suffix + ".tmp_proc")
                    shutil.move(str(final_path), str(temp_input))
                    task.input_path = temp_input

                    # Add to queue
                    await self._processing_queue.put(task)
                else:
                    logger.warning(f"Pending file missing: {final_path}")
            except Exception as e:
                logger.error(f"Failed to prepare deferred task: {e}")

        # Clear pending list
        self._pending_tasks.clear()

        # Wait for all to finish
        await self.wait_until_idle()
        logger.info("✅ Deferred processing complete.")

    async def transcribe_audio(self, file_path: Path) -> Optional[str]:
        """
        Транскрибировать аудиофайл (голосовое сообщение).

        Version: 5.0.0 - Simplified standalone implementation

        Args:
            file_path: Путь к аудиофайлу

        Returns:
            Текст транскрипции или None при ошибке
        """
        if not self._transcriber:
            logger.debug("Transcriber not available")
            return None

        try:
            # Get language from config
            language = getattr(self.config.transcription, "language", None)

            # Run transcription in executor to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.cpu_executor,
                lambda: self._transcriber.transcribe(file_path, language=language),
            )

            if result and result.text:
                logger.debug(
                    f"Transcription successful: {file_path.name} "
                    f"({len(result.text)} chars, language={result.language})"
                )
                return result.text
            else:
                logger.warning(
                    f"Transcription returned empty result for {file_path.name}"
                )
                return None

        except Exception as e:
            logger.error(f"Transcription failed for {file_path}: {e}")
            return None

    async def shutdown(self, timeout: float = 30.0):
        """
        Корректное завершение работы (Phase 2 Task 2.3: Enhanced shutdown with error logging).
        """
        logger.info("Shutting down media processor...")

        # 🚀 Остановка Background Download Queue
        if self._download_queue:
            logger.info("Waiting for background downloads to complete...")
            self._download_queue.log_stats()
            await self._download_queue.stop(wait_for_completion=True)
            logger.info("Background download queue stopped")

        # Логирование статистики ошибок перед завершением
        if self._failed_tasks > 0 or self._worker_errors:
            logger.warning(
                f"⚠️ Media processor shutdown with errors: "
                f"failed_tasks={self._failed_tasks}, workers_with_errors={len(self._worker_errors)}"
            )

            for worker_name, errors in self._worker_errors.items():
                logger.warning(f"  Worker '{worker_name}': {len(errors)} error(s)")
                if errors:
                    logger.debug(f"    Last error: {errors[-1]}")

        # Сигнал завершения
        self._shutdown_event.set()

        # Ожидание завершения воркеров
        if self._worker_tasks:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*self._worker_tasks, return_exceptions=True),
                    timeout=timeout,
                )
                # Проверка результатов для выявления ошибок
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Worker {i} failed with exception: {result}")
            except asyncio.TimeoutError:
                logger.warning(
                    "Some workers didn't complete within timeout, cancelling..."
                )
                for task in self._worker_tasks:
                    if not task.done():
                        task.cancel()
                        logger.warning(f"Cancelled task: {task.get_name()}")

        # Закрытие пулов потоков (Phase 2 Task 2.3: Proper executor shutdown)
        try:
            logger.debug("Shutting down thread executors...")
            self.io_executor.shutdown(wait=True)
            self.cpu_executor.shutdown(wait=True)
            self.ffmpeg_executor.shutdown(wait=True)
            logger.debug("Thread executors shut down successfully")
        except Exception as e:
            logger.error(f"Error during executor shutdown: {e}")

        # Очистка временных файлов
        try:
            import shutil

            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.debug(f"Cleaned up temp directory: {self.temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp directory: {e}")

        logger.info("Media processor shutdown completed")

    def log_statistics(self):
        """
        Логирование статистики (Phase 2 Task 2.3: Include error statistics).
        Синхронный метод для обратной совместимости.
        """
        logger.info("=== Media Processor Statistics ===")
        logger.info(f"Processed files: {self._processed_files}")
        logger.info(f"Cache hits: {self._cache_hits}")
        logger.info(f"Total bytes processed: {self._bytes_processed}")

        # ⚠️ Статистика ошибок (Phase 2 Task 2.3)
        logger.info(f"Failed tasks: {self._failed_tasks}")
        if self._worker_errors:
            logger.info(f"Workers with errors: {len(self._worker_errors)}")
            for worker_name, errors in self._worker_errors.items():
                logger.info(f"  {worker_name}: {len(errors)} error(s)")
        if self._failed_items_log:
            logger.info(f"Failed items logged: {len(self._failed_items_log)}")

        if self._downloader:
            self._downloader.log_statistics()

        # 🚀 Download Queue Statistics
        if self._download_queue:
            self._download_queue.log_stats()

        if self._video_processor:
            self._video_processor.log_statistics()

        if self._audio_processor:
            self._audio_processor.log_statistics()

        if self._image_processor:
            self._image_processor.log_statistics()

    def get_error_statistics(self) -> Dict[str, Any]:
        """
        Получить статистику ошибок (Phase 2 Task 2.3).

        Returns:
            Словарь с информацией об ошибках
        """
        return {
            "failed_tasks": self._failed_tasks,
            "worker_errors": dict(self._worker_errors),
            "failed_items_log": self._failed_items_log,
            "total_workers_with_errors": len(self._worker_errors),
        }
