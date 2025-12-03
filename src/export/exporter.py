"""
Export functionality for TOBS - modular exporter implementation with progress tracking.
Provides core export orchestration and processing capabilities with visual progress bars.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import aiofiles
import aiohttp
from rich import print as rprint
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from telethon import errors
from telethon.errors import FloodWaitError
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.functions.account import (
    FinishTakeoutSessionRequest,
    InitTakeoutSessionRequest,
)

from ..config import EXPORT_OPERATION_TIMEOUT, Config, ExportTarget
from ..export_reporter import ExportReporterManager
from ..media import MediaProcessor
from ..note_generator import NoteGenerator
from ..telegram_client import TelegramManager
from ..utils import is_voice_message, logger, sanitize_filename


class TakeoutSessionWrapper:
    """
    Context manager for manual Takeout session management.
    Bypasses Telethon's client.takeout() to avoid client-side state conflicts.
    """

    def __init__(self, client, config):
        self.client = client
        self.config = config
        self.takeout_id = None
        self.max_file_size = getattr(config, "max_file_size_mb", 2000) * 1024 * 1024

    async def __aenter__(self):
        # 1. Check for existing session on client (Reuse)
        existing_id = getattr(
            self.client, "takeout_id", getattr(self.client, "_takeout_id", None)
        )
        if existing_id:
            logger.info(f"♻️ Reusing existing Takeout ID: {existing_id}")
            self.takeout_id = existing_id
            return self

        # 2. Init new session manually
        try:
            init_req = InitTakeoutSessionRequest(
                contacts=True,
                message_users=True,
                message_chats=True,
                message_megagroups=True,
                message_channels=True,
                files=True,
                file_max_size=self.max_file_size,
            )
            takeout_sess = await self.client(init_req)
            self.takeout_id = takeout_sess.id
            logger.info(f"✅ Manual Takeout Init Successful. ID: {self.takeout_id}")
            return self
        except Exception as e:
            if "TakeoutInitDelayError" in str(type(e).__name__):
                raise errors.TakeoutInitDelayError()
            raise e

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.takeout_id:
            try:
                # Only finish if we created it?
                # For now, let's finish it to be clean, unless we reused it?
                # If we reused it, we probably shouldn't finish it?
                # But here we are the top-level manager.
                await self.client(
                    InvokeWithTakeoutRequest(
                        takeout_id=self.takeout_id,
                        query=FinishTakeoutSessionRequest(success=True),
                    )
                )
                logger.info("✅ Takeout session finished manually.")
            except Exception as e:
                logger.warning(f"⚠️ Error finishing takeout: {e}")

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def __call__(self, request, ordered=False):
        if self.takeout_id:
            return await self.client(
                InvokeWithTakeoutRequest(takeout_id=self.takeout_id, query=request),
                ordered=ordered,
            )
        return await self.client(request, ordered=ordered)


class AsyncBufferedSaver:
    """
    Buffered file writer that accumulates writes to reduce I/O syscalls and thread context switches.
    Wraps aiofiles to provide a similar interface but with internal buffering.
    """

    def __init__(
        self, path, mode="w", encoding="utf-8", buffer_size=131072
    ):  # 128KB buffer
        self.path = path
        self.mode = mode
        self.encoding = encoding
        self.buffer_size = buffer_size
        self._buffer = []
        self._current_size = 0
        self._file = None

    async def __aenter__(self):
        self._file = await aiofiles.open(self.path, self.mode, encoding=self.encoding)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.flush()
        if self._file:
            await self._file.close()

    async def write(self, data: str):
        self._buffer.append(data)
        self._current_size += len(data)
        if self._current_size >= self.buffer_size:
            await self.flush()

    async def flush(self):
        if not self._buffer:
            # Even if buffer is empty, we might want to flush the underlying file
            if self._file:
                await self._file.flush()
            return

        content = "".join(self._buffer)
        self._buffer = []
        self._current_size = 0

        if self._file:
            await self._file.write(content)
            await self._file.flush()


class ForumTopic:
    """Represents a forum topic with metadata."""

    def __init__(self, topic_id: int, title: str, message_count: int = 0):
        self.topic_id = topic_id
        self.title = title
        self.message_count = message_count
        self.sanitized_name = sanitize_filename(title)

    def __repr__(self):
        return f"ForumTopic(id={self.topic_id}, title='{self.title}', messages={self.message_count})"


@dataclass
class EntityCacheData:
    """Данные кэша для сущности."""

    entity_id: str
    entity_name: str
    entity_type: str
    total_messages: int = 0
    processed_messages: int = 0
    last_message_id: Optional[int] = None
    processed_message_ids: Set[int] = field(default_factory=set)


class ExportStatistics:
    """Statistics tracking for export operations."""

    def __init__(self):
        self.start_time = time.time()
        self.end_time = None
        self.messages_processed = 0
        self.media_downloaded = 0
        self.notes_created = 0
        self.errors_encountered = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.avg_cpu_percent = 0.0
        self.peak_memory_mb = 0.0

    @property
    def duration(self) -> float:
        """Get duration in seconds."""
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def messages_per_minute(self) -> float:
        """Calculate messages per minute rate."""
        if self.duration > 0:
            return (self.messages_processed / self.duration) * 60
        return 0.0


class Exporter:
    """
    Main exporter class handling all export operations.
    Replaces monolithic functions from main.py with clean modular architecture.
    """

    def __init__(
        self,
        config: Config,
        telegram_manager: TelegramManager,
        cache_manager,
        media_processor: MediaProcessor,
        note_generator: NoteGenerator,
        http_session: aiohttp.ClientSession,
        performance_monitor=None,
    ):
        self.config = config
        self.telegram_manager = telegram_manager
        self.cache_manager = cache_manager
        self.media_processor = media_processor
        self.note_generator = note_generator
        self.http_session = http_session
        self.performance_monitor = performance_monitor
        self.statistics = ExportStatistics()
        self._shutdown_requested = False
        self.progress = None  # Progress bar instance

        # 🚀 Medium Win 3: Sender name cache
        self._sender_name_cache: Dict[int, str] = {}

        # 🚀 Medium Win 2: Prefetch pipeline
        self._prefetch_task = None
        self._prefetch_result = None
        self._prefetch_stats = {"hits": 0, "misses": 0}

        # Initialize the reporter manager
        self.reporter_manager = ExportReporterManager(
            base_monitoring_path=self.config.export_path,
            performance_monitor=self.performance_monitor,
        )

    async def initialize(self):
        """Initialize exporter and all components."""
        try:
            # Telegram connection should already be established in main.py
            if not self.telegram_manager.client_connected:
                logger.info("Telegram not connected, connecting...")
                await self.telegram_manager.connect()
            logger.info("✅ Telegram connection verified")

            # Initialize media processor
            logger.info("✅ Media processor ready")

            # Initialize cache
            if hasattr(self.cache_manager, "load_cache"):
                await self.cache_manager.load_cache()
                logger.info("✅ Cache loaded")
            else:
                logger.info("✅ Cache manager ready")

            logger.info("🚀 Exporter initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize exporter: {e}")
            raise

    async def export_target(
        self, target: ExportTarget, progress_queue=None, task_id=None
    ) -> ExportStatistics:
        """Export a single target with timeout protection."""
        logger.info(f"Starting export for target: {target.name} (ID: {target.id})")

        try:
            # Reset statistics
            self.statistics = ExportStatistics()

            # 🚀 Medium Win 3: Clear caches
            self._sender_name_cache.clear()
            self._prefetch_stats = {"hits": 0, "misses": 0}
            if self._prefetch_task and not self._prefetch_task.done():
                self._prefetch_task.cancel()
            self._prefetch_task = None
            self._prefetch_result = None

            # Determine export type
            try:
                if target.type in ["forum", "forum_chat", "forum_topic"]:
                    result = await asyncio.wait_for(
                        self._export_forum(target, progress_queue, task_id),
                        timeout=EXPORT_OPERATION_TIMEOUT,
                    )
                else:
                    result = await asyncio.wait_for(
                        self._export_regular_target(target, progress_queue, task_id),
                        timeout=EXPORT_OPERATION_TIMEOUT,
                    )
                return result
            except asyncio.TimeoutError:
                logger.error(
                    f"⏰ Export timeout for target {target.name}: "
                    f"exceeded {EXPORT_OPERATION_TIMEOUT}s limit"
                )
                self.statistics.errors_encountered += 1
                raise

        except Exception as e:
            logger.error(f"Export failed for target {target.name}: {e}")
            self.statistics.errors_encountered += 1
            raise
        finally:
            self.statistics.end_time = time.time()

    async def _fetch_messages_batch(self, entity, min_id, limit=100):
        """Fetch a batch of messages (MW1)."""
        # Uses whatever client is currently in telegram_manager (Standard or Takeout)
        return await self.telegram_manager.client.get_messages(
            entity, limit=limit, min_id=min_id, reverse=True
        )

    async def _process_message_parallel(
        self, message, target, media_dir, output_dir, entity_reporter
    ):
        """Process a single message in parallel (MW3)."""
        try:
            # Get sender name
            sender_name = await self._get_sender_name(message)

            # Format timestamp
            timestamp = self._format_timestamp(message.date)

            content = []
            content.append(f"{sender_name}, [{timestamp}]\n")

            if message.text:
                content.append(f"{message.text}\n")

            local_media_count = 0

            # Handle media
            if message.media and self.config.media_download:
                try:
                    media_paths = await self.media_processor.download_and_process_media(
                        message=message,
                        entity_id=target.id,
                        entity_media_path=media_dir,
                    )
                    if media_paths:
                        local_media_count = len(media_paths)

                        # Record media downloads
                        for media_path in media_paths:
                            try:
                                file_size = media_path.stat().st_size
                                entity_reporter.record_media_downloaded(
                                    message.id, file_size, str(media_path)
                                )
                            except Exception:
                                pass

                        # Add references
                        for media_path in media_paths:
                            try:
                                relative_path = media_path.relative_to(output_dir)
                                content.append(f"![[{relative_path}]]\n")

                                # Transcription
                                if (
                                    self.config.enable_transcription
                                    and is_voice_message(message)
                                ):
                                    try:
                                        transcription = (
                                            await self.media_processor.transcribe_audio(
                                                media_path
                                            )
                                        )
                                        if transcription:
                                            content.append(
                                                f"**Расшифровка:** {transcription}\n"
                                            )
                                    except Exception:
                                        pass
                            except ValueError:
                                content.append(f"![[{media_path.name}]]\n")
                    else:
                        content.append("[[No files downloaded]]\n")
                except Exception as e:
                    logger.warning(
                        f"Failed to process media for message {message.id}: {e}"
                    )
                    content.append("[[Failed to download]]\n")
            elif message.media:
                media_type = self._get_media_type_name(message.media)
                content.append(f"[{media_type}]\n")

            content.append("\n")
            return (
                "".join(content),
                message.id,
                bool(message.media),
                local_media_count,
            )

        except Exception as e:
            logger.error(f"Error processing message {message.id}: {e}")
            return "", message.id, False, 0

    async def _export_regular_target(
        self, target: ExportTarget, progress_queue=None, task_id=None
    ) -> ExportStatistics:
        """Export regular channel or chat to single file with Telegram-like format."""
        logger.info(f"Exporting regular target: {target.name}")

        try:
            entity = await self.telegram_manager.resolve_entity(target.id)
            entity_name = getattr(
                entity, "title", getattr(entity, "first_name", str(target.id))
            )

            # Load entity state from core cache
            cache_key = f"entity_state_{target.id}"
            entity_data = await self.cache_manager.get(cache_key)

            # Handle dict restoration (from JSON cache)
            if isinstance(entity_data, dict):
                try:
                    if "processed_message_ids" in entity_data and isinstance(
                        entity_data["processed_message_ids"], list
                    ):
                        entity_data["processed_message_ids"] = set(
                            entity_data["processed_message_ids"]
                        )
                    entity_data = EntityCacheData(**entity_data)
                except Exception as e:
                    logger.warning(f"Failed to restore EntityCacheData from dict: {e}")
                    entity_data = None

            if not isinstance(entity_data, EntityCacheData):
                entity_data = EntityCacheData(
                    entity_id=str(target.id),
                    entity_name=entity_name,
                    entity_type="regular",
                )

            # Create output directory structure FIRST
            output_dir = self.config.get_export_path_for_entity(target.id)
            media_dir = self.config.get_media_path_for_entity(target.id)

            # Create monitoring directory inside entity export folder
            monitoring_dir = output_dir / ".monitoring"
            await asyncio.to_thread(monitoring_dir.mkdir, parents=True, exist_ok=True)

            # Get reporter with entity-specific monitoring path
            entity_reporter = self.reporter_manager.get_reporter(
                target.id, monitoring_dir
            )

            # Prepare export settings for monitoring
            export_settings = {
                "sharding_enabled": self.config.sharding_enabled,
                "shard_count": self.config.shard_count,
                "use_takeout": self.config.use_takeout,
                "performance_profile": self.config.performance_profile,
            }
            entity_reporter.start_export(
                entity_name, "regular", export_settings=export_settings
            )

            # Update entity info
            entity_data.entity_name = entity_name

            # Debug logging for paths
            logger.info(f"🔍 Export paths for {entity_name} (ID: {target.id}):")
            logger.info(f"  📁 Output dir: {output_dir}")
            logger.info(f"  📁 Media dir: {media_dir}")
            logger.info(f"  ⚙️  Structured export: {self.config.use_structured_export}")

            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(media_dir.mkdir, parents=True, exist_ok=True)

            logger.info(f"📁 Monitoring directory created: {monitoring_dir}")
            logger.info(f"📊 Monitoring file: monitoring_{target.id}.json")
            logger.info(f"💾 Cache key: {cache_key}")

            # Create single chat file
            safe_name = (
                entity_name.replace("/", "_").replace("\\", "_").replace(":", "_")
            )
            chat_file = output_dir / f"{safe_name}.md"

            logger.info(f"  📄 Chat file: {chat_file}")

            # Initialize counters
            message_count = 0
            media_count = 0

            # Create media subdirectory if needed
            media_dir = output_dir / "media"
            if self.config.media_download:
                await asyncio.to_thread(media_dir.mkdir, exist_ok=True)

            # Use Rich progress bar for better UX (streaming mode - no percentage)
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TextColumn("•"),
                TextColumn("[cyan]{task.fields[messages]} msgs"),
                TextColumn("•"),
                TextColumn("[green]{task.fields[media]} media"),
                transient=False,
            ) as progress:
                task_id_progress = progress.add_task(
                    f"[cyan]Exporting {entity_name}...", total=None, messages=0, media=0
                )

                # Open chat file for writing (using AsyncBufferedSaver for optimized I/O)
                async with AsyncBufferedSaver(chat_file, "w", encoding="utf-8") as f:
                    # Write chat header
                    await f.write(f"# Chat Export: {entity_name}\n\n")
                    await f.write(f"Export Date: {self._get_current_datetime()}\n")
                    await f.write("Total Messages: Processing...\n\n")
                    await f.write("---\n\n")

                    # Single-pass streaming: process messages as they arrive
                    logger.info(f"📊 Starting streaming export for {entity_name}...")

                    # Initialize loop state
                    current_min_id = 0
                    consecutive_flood_waits = 0
                    max_retries = 10
                    adaptive_delay = self.config.request_delay  # 🚀 Start with configured delay

                    # Initial fetch task (MW2: Prefetch)
                    fetch_task = asyncio.create_task(
                        self._fetch_messages_batch(entity, current_min_id)
                    )

                    while True:
                        try:
                            # Await the batch
                            batch = await fetch_task
                            consecutive_flood_waits = 0  # Reset on success
                            
                            # 🚀 Adaptive delay: decrease delay on success (minimum 0)
                            if adaptive_delay > 0:
                                adaptive_delay = max(0.0, adaptive_delay - 0.1)

                        except FloodWaitError as e:
                            # Handle FloodWait
                            consecutive_flood_waits += 1
                            if consecutive_flood_waits > max_retries:
                                logger.error("Max retries reached for FloodWait")
                                break

                            wait_time = e.seconds
                            backoff = min(consecutive_flood_waits * 5, 60)
                            total_wait = wait_time + backoff
                            
                            # 🚀 Adaptive delay: increase delay after FloodWait
                            adaptive_delay = min(adaptive_delay + 0.5, 3.0)
                            logger.info(f"📈 Adaptive delay increased to {adaptive_delay:.1f}s")

                            logger.warning(
                                f"FloodWait: {wait_time}s (+{backoff}s buffer). Retry {consecutive_flood_waits}/{max_retries}"
                            )

                            # Flush file (Fix)
                            try:
                                await f.flush()
                                logger.debug("📁 File flushed before FloodWait sleep")
                            except Exception as flush_error:
                                logger.warning(
                                    f"Failed to flush file before FloodWait: {flush_error}"
                                )

                            # Update progress
                            progress.update(
                                task_id_progress,
                                description=f"[yellow]⏳ FloodWait: {total_wait}s[/yellow]",
                            )
                            await asyncio.sleep(total_wait)
                            progress.update(
                                task_id_progress,
                                description=f"[cyan]Exporting {entity_name}...[/cyan]",
                            )

                            # Retry fetching same batch
                            fetch_task = asyncio.create_task(
                                self._fetch_messages_batch(entity, current_min_id)
                            )
                            continue

                        except Exception as e:
                            logger.error(f"Error fetching batch: {e}")
                            break

                        if not batch:
                            break

                        # Start next fetch immediately (MW2)
                        last_msg_id = batch[-1].id

                        # 🚀 Adaptive rate limiting
                        if adaptive_delay > 0:
                            await asyncio.sleep(adaptive_delay)

                        fetch_task = asyncio.create_task(
                            self._fetch_messages_batch(entity, last_msg_id)
                        )

                        # Process batch in parallel (MW3)
                        tasks = [
                            self._process_message_parallel(
                                msg, target, media_dir, output_dir, entity_reporter
                            )
                            for msg in batch
                            if (msg.text or msg.media)  # Filter empty
                        ]

                        if not tasks:
                            current_min_id = last_msg_id
                            continue

                        results = await asyncio.gather(*tasks)

                        # Write results sequentially
                        for content, msg_id, has_media, media_cnt in results:
                            if not content:
                                continue  # Skip failed

                            await f.write(content)

                            # Update stats
                            message_count += 1
                            media_count += media_cnt
                            self.statistics.messages_processed += 1
                            self.statistics.media_downloaded += media_cnt

                            # Update entity data
                            entity_data.processed_message_ids.add(msg_id)
                            entity_data.last_message_id = msg_id
                            entity_reporter.record_message_processed(
                                msg_id, has_media=has_media
                            )

                        # Periodic save
                        if message_count % 100 == 0:
                            try:
                                await self.cache_manager.set(cache_key, entity_data)
                                entity_reporter.save_metrics()
                                logger.info(
                                    f"💾 Periodic save: {message_count} messages processed for {entity_name}"
                                )
                            except Exception as save_error:
                                logger.warning(
                                    f"Failed periodic save at message {message_count}: {save_error}"
                                )

                        # Update progress bar
                        progress.update(
                            task_id_progress, messages=message_count, media=media_count
                        )

                        # Also update progress queue if provided
                        if progress_queue:
                            await progress_queue.put(
                                {
                                    "task_id": task_id,
                                    "progress": message_count,
                                    "total": None,
                                    "status": f"Processed {message_count} messages",
                                }
                            )

                        # Update loop variable
                        current_min_id = last_msg_id

            # Update total messages count in file
            await self._update_message_count(chat_file, message_count)
            self.statistics.notes_created = 1  # One file created

            logger.info(
                f"✅ Export completed: {message_count} messages, {media_count} media files"
            )

            # Calculate periodic saves count
            periodic_saves_count = message_count // 100

            # Save final entity state and monitoring data
            try:
                await self.cache_manager.set(cache_key, entity_data)

                # Collect worker stats if available
                if hasattr(self.telegram_manager, "get_worker_stats"):
                    worker_stats = self.telegram_manager.get_worker_stats()
                    if worker_stats:
                        entity_reporter.metrics.worker_stats = worker_stats.copy()
                        logger.info(
                            f"📊 Collected stats from {len(worker_stats)} workers"
                        )

                entity_reporter.finish_export()
                entity_reporter.save_report()

                # Update statistics with resource metrics
                self.statistics.avg_cpu_percent = (
                    entity_reporter.metrics.avg_cpu_percent
                )
                self.statistics.peak_memory_mb = entity_reporter.metrics.peak_memory_mb

                logger.info(f"Final save completed for {entity_name}")
                logger.info(f"  📊 Total messages: {message_count}")
                logger.info(f"  🎬 Media files: {media_count}")
                logger.info(f"  💾 Periodic saves: {periodic_saves_count}")
                logger.info(f"  📂 Export location: {output_dir}")
                logger.info(
                    f"  📈 Monitoring saved to: {monitoring_dir}/monitoring_{target.id}.json"
                )
                logger.info(f"  💾 Cache key: {cache_key}")
            except Exception as save_error:
                logger.warning(
                    f"Failed to save cache/monitoring for {entity_name}: {save_error}"
                )

        except Exception as e:
            logger.error(f"Export failed for {target.name}: {e}")
            self.statistics.errors_encountered += 1

            # Try to save cache/monitoring even on failure
            try:
                await self.cache_manager.set(cache_key, entity_data)
                entity_reporter.finish_export()
                entity_reporter.save_report()

                periodic_saves_count = message_count // 100
                logger.info(
                    f"Emergency save completed for {entity_name} after export failure"
                )
                logger.info(f"  📊 Messages processed before failure: {message_count}")
                logger.info(f"  💾 Periodic saves completed: {periodic_saves_count}")
                logger.info(
                    f"  📈 Emergency monitoring saved to: {monitoring_dir}/monitoring_{target.id}.json"
                )
                logger.info(f"  💾 Cache key: {cache_key}")
            except Exception as save_error:
                logger.warning(
                    f"Failed to save cache/monitoring after export failure for {entity_name}: {save_error}"
                )

            raise

        return self.statistics

    async def _get_sender_name(self, message) -> str:
        """Get formatted sender name for message with caching."""
        try:
            sender_id = message.sender_id
            if not sender_id:
                return "Unknown User"

            # Check cache first
            if sender_id in self._sender_name_cache:
                return self._sender_name_cache[sender_id]

            if message.sender:
                name = "Unknown User"
                if hasattr(message.sender, "first_name"):
                    # User
                    name_parts = []
                    if message.sender.first_name:
                        name_parts.append(message.sender.first_name)
                    if getattr(message.sender, "last_name", None):
                        name_parts.append(message.sender.last_name)
                    name = " ".join(name_parts) if name_parts else f"User {sender_id}"
                elif hasattr(message.sender, "title"):
                    # Channel/Group
                    name = str(message.sender.title)
                else:
                    name = f"User {sender_id}"

                # Cache the result
                self._sender_name_cache[sender_id] = name
                return name
            else:
                return f"User {sender_id}"
        except Exception:
            return "Unknown User"

    def _format_timestamp(self, dt) -> str:
        """Format datetime in Telegram export format (optimized)."""
        # f-string is faster than strftime
        return f"{dt.day:02d}.{dt.month:02d}.{dt.year} {dt.hour:02d}:{dt.minute:02d}"

    def _get_current_datetime(self) -> str:
        """Get current datetime formatted."""
        import datetime

        return datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

    def _get_media_type_name(self, media) -> str:
        """Get human-readable media type name."""
        media_type = type(media).__name__
        type_mapping = {
            "MessageMediaPhoto": "Photo",
            "MessageMediaDocument": "Document",
            "MessageMediaVideo": "Video",
            "MessageMediaAudio": "Audio",
            "MessageMediaVoice": "Voice Message",
            "MessageMediaContact": "Contact",
            "MessageMediaLocation": "Location",
            "MessageMediaPoll": "Poll",
            "MessageMediaSticker": "Sticker",
            "MessageMediaGif": "GIF",
        }
        return type_mapping.get(media_type, "Media")

    async def _update_message_count(self, file_path, count):
        """Update the total message count in the exported file."""
        try:
            # Read the file asynchronously
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            # Replace the placeholder
            content = content.replace(
                "Total Messages: Processing...", f"Total Messages: {count}"
            )

            # Write back asynchronously
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(content)
        except Exception as e:
            logger.warning(f"Failed to update message count: {e}")

        finally:
            self.statistics.end_time = time.time()

    # --- Forum Export Methods ---

    async def _export_forum(
        self, target: ExportTarget, progress_queue, task_id
    ) -> ExportStatistics:
        logger.info(f"Starting forum export: {target.name}")
        entity = await self.telegram_manager.resolve_entity(target.id)
        entity_name = getattr(entity, "title", str(target.id))
        forum_folder = self.config.export_path / sanitize_filename(entity_name)
        await asyncio.to_thread(forum_folder.mkdir, parents=True, exist_ok=True)

        all_topics = await self.telegram_manager.get_forum_topics(entity)
        topics_to_export = []
        if target.type == "forum_topic":
            topics_to_export = [t for t in all_topics if t.topic_id == target.topic_id]
        else:
            topics_to_export = all_topics

        logger.info(
            f"Found {len(topics_to_export)} topics to export in forum {entity_name}"
        )
        if not topics_to_export:
            return self.statistics

        for i, topic in enumerate(topics_to_export):
            # Simplified logic for exporting a single topic
            logger.info(
                f"Exporting topic {i + 1}/{len(topics_to_export)}: {topic.title}"
            )
            # In a real implementation, this would call a detailed method
            # similar to _export_regular_target but for a topic.
            pass

        return self.statistics

    async def export_all(
        self, targets: List[ExportTarget], progress_queue=None
    ) -> List[ExportStatistics]:
        """
        Export multiple targets sequentially.

        Args:
            targets: List of ExportTarget objects
            progress_queue: Optional progress reporting queue

        Returns:
            List of ExportStatistics for each target
        """
        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            transient=True,
        ) as progress:
            task_id_progress = progress.add_task(
                "[cyan]Exporting targets...", total=len(targets)
            )

            for i, target in enumerate(targets):
                if self._shutdown_requested:
                    logger.info("Shutdown requested, stopping export")
                    break

                progress.update(
                    task_id_progress,
                    description=f"[cyan]Exporting {i + 1}/{len(targets)}: {target.name}",
                )
                logger.info(f"Exporting target {i + 1}/{len(targets)}: {target.name}")

                try:
                    stats = await self.export_target(
                        target, progress_queue, f"target_{i}"
                    )
                    results.append(stats)

                    logger.info(f"✅ Target {target.name} exported successfully")
                    logger.info(f"   Messages: {stats.messages_processed}")
                    logger.info(f"   Media: {stats.media_downloaded}")
                    logger.info(f"   Duration: {stats.duration:.1f}s")

                except Exception as e:
                    logger.error(f"❌ Failed to export target {target.name}: {e}")
                    results.append(ExportStatistics())

                progress.advance(task_id_progress)

        # 🚀 Wait for background downloads to complete
        if getattr(self.config, "async_media_download", True):
            logger.info("⏳ Waiting for background media downloads...")
            await self.media_processor.wait_for_downloads(timeout=3600)  # 1 hour max

        # 🚀 Run Deferred Media Processing
        if self.config.deferred_processing:
            logger.info("⏳ Starting deferred media processing...")
            await self.media_processor.process_pending_tasks()

        return results

    async def shutdown(self):
        """Gracefully shutdown the exporter."""
        self._shutdown_requested = True
        logger.info("Exporter shutdown initiated")


async def run_export(
    config: Config,
    telegram_manager: TelegramManager,
    cache_manager,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession,
    progress_queue=None,
    connection_manager=None,
    performance_monitor=None,
) -> List[ExportStatistics]:
    """
    High-level export orchestration function.
    Replaces the main export logic from main.py run_export function.
    """
    exporter = Exporter(
        config=config,
        telegram_manager=telegram_manager,
        cache_manager=cache_manager,
        media_processor=media_processor,
        note_generator=note_generator,
        http_session=http_session,
        performance_monitor=performance_monitor,
    )

    try:
        # Initialize all components
        await exporter.initialize()

        # 🚀 High Win: Takeout API Integration
        if config.use_takeout:
            # 1. Check if we are already in a Takeout session (Reuse Strategy)
            current_client = telegram_manager.client
            existing_id = getattr(
                current_client,
                "takeout_id",
                getattr(current_client, "_takeout_id", None),
            )

            if existing_id:
                logger.info(
                    f"♻️ Client is already in Takeout mode (ID: {existing_id}). Skipping initialization."
                )
                telegram_manager._external_takeout_id = existing_id

                # Disable rate limiting
                original_delay = config.request_delay
                config.request_delay = 0.0

                try:
                    return await exporter.export_all(
                        config.export_targets, progress_queue
                    )
                finally:
                    config.request_delay = original_delay
                    # Do not clear _external_takeout_id here as we didn't create the session
                    logger.info("🔄 Finished export using existing Takeout session")

            # 2. If not reusing, try to init new session
            try:
                logger.info("🚀 Attempting to initiate Telegram Takeout session...")
                logger.info(
                    "⚠️  Please check your Telegram messages (Service Notifications) to ALLOW the request."
                )

                # Calculate max file size in bytes (default 2GB)
                # Telethon requires file_max_size if files=True
                max_file_size = getattr(config, "max_file_size_mb", 2000) * 1024 * 1024

                # 🧹 Force-clear stale state blindly
                # The error "Can't send a takeout request while another takeout..." is a client-side check
                # We force clear it to ensure we can start a new one if the previous one wasn't closed properly
                try:
                    telegram_manager.client._takeout_id = None
                except Exception:
                    pass

                # Use Manual Wrapper instead of client.takeout()
                async with TakeoutSessionWrapper(
                    telegram_manager.client, config
                ) as takeout_client:
                    logger.info(
                        "✅ Takeout session established! Switching to Turbo Mode."
                    )

                    # ⚡ HACK: Temporarily swap the client in the manager
                    original_client = telegram_manager.client
                    telegram_manager.client = takeout_client

                    # Pass the ID to the manager so shards can reuse it
                    takeout_id = takeout_client.takeout_id

                    logger.info(f"DEBUG: Extracted Takeout ID: {takeout_id}")

                    # Force set the attribute on the manager
                    setattr(telegram_manager, "_external_takeout_id", takeout_id)

                    if takeout_id:
                        logger.info(
                            f"♻️ Shared Takeout ID {takeout_id} with ShardedManager"
                        )
                    else:
                        logger.warning(
                            "⚠️ Could not extract Takeout ID from client! Sharding might fail."
                        )

                    # Disable rate limiting for Takeout
                    original_delay = config.request_delay
                    config.request_delay = 0.0

                    try:
                        # Export all configured targets using Takeout
                        results = await exporter.export_all(
                            config.export_targets, progress_queue
                        )
                    finally:
                        # Restore original client and settings
                        telegram_manager.client = original_client
                        if hasattr(telegram_manager, "_external_takeout_id"):
                            telegram_manager._external_takeout_id = None

                        config.request_delay = original_delay
                        logger.info("🔄 Restored standard client connection")

                    return results

            except errors.TakeoutInitDelayError:
                logger.warning(
                    "⚠️  Takeout request needs confirmation. Please allow it in Telegram."
                )
                logger.warning(
                    "   (Telegram requires a delay after approval before Takeout becomes active)"
                )
                logger.info("ℹ️  Falling back to Standard API with rate limiting.")

            except Exception as e:
                if "another takeout" in str(e):
                    logger.warning(
                        "⚠️  Detected stale Takeout session state even after force-clear."
                    )
                    # At this point, we can't do much else than fall back

                logger.warning(
                    f"⚠️  Takeout session failed ({e}). Falling back to Standard API."
                )

        # Fallback or Standard mode
        logger.info(
            f"ℹ️  Using Standard API with rate limit delay: {config.takeout_fallback_delay}s"
        )
        config.request_delay = config.takeout_fallback_delay
        results = await exporter.export_all(config.export_targets, progress_queue)
        return results

    finally:
        # Ensure cleanup happens
        await exporter.shutdown()


def print_export_summary(results: List[ExportStatistics]):
    """Print summary of export results."""
    if not results:
        rprint("[yellow]No export results to display[/yellow]")
        return

    total_messages = sum(r.messages_processed for r in results)
    total_media = sum(r.media_downloaded for r in results)
    total_errors = sum(r.errors_encountered for r in results)
    total_duration = sum(r.duration for r in results)

    rprint("\n[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint("[bold green]          EXPORT SUMMARY[/bold green]")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint(f"[cyan]Total Messages:[/cyan] {total_messages}")
    rprint(f"[cyan]Total Media Files:[/cyan] {total_media}")
    rprint(f"[cyan]Errors:[/cyan] {total_errors}")
    rprint(f"[cyan]Total Duration:[/cyan] {total_duration:.1f}s")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]\n")
