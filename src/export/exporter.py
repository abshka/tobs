"""
Export functionality for TOBS - modular exporter implementation with progress tracking.
Provides core export orchestration and processing capabilities with visual progress bars.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set

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
from telethon.errors import FloodWaitError

from ..config import EXPORT_OPERATION_TIMEOUT, Config, ExportTarget
from ..export_reporter import ExportReporterManager
from ..media import MediaProcessor
from ..note_generator import NoteGenerator
from ..telegram_client import TelegramManager
from ..utils import is_voice_message, logger, sanitize_filename


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

        # Initialize the reporter manager
        self.reporter_manager = ExportReporterManager(
            base_monitoring_path=self.config.export_path,
            performance_monitor=self.performance_monitor,
        )

    async def initialize(self):
        """Initialize exporter and all components."""
        try:
            # Telegram connection should already be established in main.py
            # We just verify it's connected
            if not self.telegram_manager.client_connected:
                logger.info("Telegram not connected, connecting...")
                await self.telegram_manager.connect()
            logger.info("✅ Telegram connection verified")

            # Initialize media processor
            logger.info("✅ Media processor ready")

            # Initialize cache (only if it's SimpleCacheManager)
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
        """
        Export a single target (channel, chat, or forum) with timeout protection (Phase 2 Task 2.2).

        Args:
            target: ExportTarget to process
            progress_queue: Optional progress reporting queue
            task_id: Optional task ID for progress tracking

        Returns:
            ExportStatistics with operation results
        """
        logger.info(f"Starting export for target: {target.name} (ID: {target.id})")

        try:
            # Reset statistics for this export
            self.statistics = ExportStatistics()

            # Determine export type and delegate with timeout protection
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
            entity_reporter.start_export(entity_name, "regular", export_settings={})

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

                # Open chat file for writing (using aiofiles for async I/O)
                async with aiofiles.open(chat_file, "w", encoding="utf-8") as f:
                    # Write chat header
                    await f.write(f"# Chat Export: {entity_name}\n\n")
                    await f.write(f"Export Date: {self._get_current_datetime()}\n")
                    await f.write("Total Messages: Processing...\n\n")
                    await f.write("---\n\n")

                    # Single-pass streaming: process messages as they arrive
                    logger.info(f"📊 Starting streaming export for {entity_name}...")

                    # Retry loop for FloodWaitError with single-pass processing
                    max_retries = 10  # Increased from 3 to handle persistent FloodWait
                    retry_count = 0
                    processing_started = False
                    # Track offset for resuming after FloodWait
                    current_offset_id = 0
                    consecutive_flood_waits = 0

                    while retry_count < max_retries:
                        try:
                            # Use offset_id to resume from last processed message (prevents duplication on retry)
                            async for (
                                message
                            ) in self.telegram_manager.client.iter_messages(
                                entity, reverse=True, offset_id=current_offset_id
                            ):
                                if not (message.text or message.media):
                                    continue

                                if not processing_started:
                                    logger.info(
                                        f"📈 Processing messages from {entity_name}..."
                                    )
                                    processing_started = True
                                try:
                                    message_count += 1
                                    self.statistics.messages_processed += 1

                                    # Update offset for FloodWait recovery (so we don't reprocess messages on retry)
                                    current_offset_id = message.id

                                    # Update entity state
                                    entity_data.processed_message_ids.add(message.id)
                                    entity_data.processed_messages = len(
                                        entity_data.processed_message_ids
                                    )
                                    entity_data.last_message_id = message.id
                                    entity_reporter.record_message_processed(
                                        message.id, has_media=bool(message.media)
                                    )

                                    # Periodic save every 100 messages
                                    if message_count % 100 == 0:
                                        try:
                                            await self.cache_manager.set(
                                                cache_key, entity_data
                                            )
                                            entity_reporter.save_metrics()
                                            # Reset consecutive flood waits on successful batch
                                            consecutive_flood_waits = 0
                                            logger.info(
                                                f"💾 Periodic save: {message_count} messages processed for {entity_name}"
                                            )
                                        except Exception as save_error:
                                            logger.warning(
                                                f"Failed periodic save at message {message_count} for {entity_name}: {save_error}"
                                            )

                                    # Get sender name
                                    sender_name = await self._get_sender_name(message)

                                    # Format timestamp
                                    timestamp = self._format_timestamp(message.date)

                                    # Write message header in Telegram format
                                    await f.write(f"{sender_name}, [{timestamp}]\n")

                                    # Write message text
                                    if message.text:
                                        await f.write(f"{message.text}\n")

                                    # Handle media
                                    if message.media and self.config.media_download:
                                        try:
                                            media_paths = await self.media_processor.download_and_process_media(
                                                message=message,
                                                entity_id=target.id,
                                                entity_media_path=media_dir,
                                            )
                                            if media_paths:
                                                media_count += len(media_paths)
                                                self.statistics.media_downloaded += len(
                                                    media_paths
                                                )

                                                # Record media downloads in monitoring
                                                for media_path in media_paths:
                                                    try:
                                                        file_size = (
                                                            media_path.stat().st_size
                                                        )
                                                        entity_reporter.record_media_downloaded(
                                                            message.id,
                                                            file_size,
                                                            str(media_path),
                                                        )
                                                    except Exception as size_error:
                                                        logger.debug(
                                                            f"Failed to get file size for {media_path}: {size_error}"
                                                        )

                                                # Add media references
                                                for media_path in media_paths:
                                                    try:
                                                        relative_path = (
                                                            media_path.relative_to(
                                                                output_dir
                                                            )
                                                        )
                                                        await f.write(
                                                            f"![[{relative_path}]]\n"
                                                        )

                                                        # Transcribe voice messages if enabled
                                                        if (
                                                            self.config.enable_transcription
                                                            and is_voice_message(
                                                                message
                                                            )
                                                        ):
                                                            try:
                                                                logger.debug(
                                                                    f"Transcribing voice message {message.id}"
                                                                )
                                                                transcription = await self.media_processor.transcribe_audio(
                                                                    media_path
                                                                )
                                                                if transcription:
                                                                    await f.write(
                                                                        f"**Расшифровка:** {transcription}\n"
                                                                    )
                                                                    logger.debug(
                                                                        f"Transcription added for message {message.id}"
                                                                    )
                                                            except (
                                                                Exception
                                                            ) as trans_err:
                                                                logger.warning(
                                                                    f"Failed to transcribe message {message.id}: {trans_err}"
                                                                )

                                                    except ValueError:
                                                        # Fallback if relative_to fails
                                                        await f.write(
                                                            f"![[{media_path.name}]]\n"
                                                        )
                                            else:
                                                await f.write(
                                                    "[[No files downloaded]]\n"
                                                )

                                        except Exception as e:
                                            logger.warning(
                                                f"Failed to process media for message {message.id}: {e}"
                                            )
                                            await f.write("[[Failed to download]]\n")
                                    elif message.media:
                                        # Media present but download disabled
                                        media_type = self._get_media_type_name(
                                            message.media
                                        )
                                        await f.write(f"[{media_type}]\n")

                                    await f.write("\n")  # Empty line after each message

                                    # Update progress bar
                                    progress.update(
                                        task_id_progress,
                                        messages=message_count,
                                        media=media_count,
                                        description=f"[cyan]Exporting {entity_name}...",
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

                                except Exception as e:
                                    logger.error(
                                        f"Error processing message {message.id}: {e}"
                                    )
                                    self.statistics.errors_encountered += 1

                            break  # Success, exit retry loop

                        except FloodWaitError as e:
                            retry_count += 1
                            consecutive_flood_waits += 1
                            wait_time = e.seconds
                            
                            # Add exponential backoff buffer (caps at 60s extra)
                            backoff_buffer = min(consecutive_flood_waits * 5, 60)
                            total_wait = wait_time + backoff_buffer
                            
                            logger.warning(
                                f"⏳ FloodWait detected: Telegram requires {wait_time}s wait "
                                f"(+ {backoff_buffer}s buffer = {total_wait}s total) "
                                f"(attempt {retry_count}/{max_retries})"
                            )
                            logger.info(
                                f"  📍 Current progress: {message_count} messages processed, last message ID: {current_offset_id}"
                            )
                            logger.info(
                                f"  🔄 Will resume from message ID: {current_offset_id} (offset_id parameter)"
                            )
                            
                            if retry_count < max_retries:
                                logger.info(
                                    f"⏱️  Waiting {total_wait} seconds before retry..."
                                )
                                # Update progress bar to show we're waiting
                                progress.update(
                                    task_id_progress,
                                    description=f"[yellow]⏳ FloodWait: waiting {total_wait}s (retry {retry_count}/{max_retries})[/yellow]",
                                )
                                await asyncio.sleep(total_wait)
                                
                                # Reset description after wait
                                progress.update(
                                    task_id_progress,
                                    description=f"[cyan]Exporting {entity_name}... (resuming)",
                                )
                                logger.info("🔄 Resuming export after FloodWait...")
                            else:
                                logger.error(
                                    f"❌ Max retries ({max_retries}) reached for FloodWait"
                                )
                                logger.warning(
                                    f"⚠️  Partial export completed: {message_count} messages saved to {chat_file}"
                                )
                                # Don't raise - allow partial export to be saved
                                entity_reporter.record_error(
                                    "FloodWait max retries exceeded",
                                    {"messages_processed": message_count, "media_downloaded": media_count}
                                )
                                break  # Exit retry loop with partial results

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
                entity_reporter.finish_export()
                entity_reporter.save_report()

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
        """Get formatted sender name for message."""
        try:
            if message.sender:
                if hasattr(message.sender, "first_name"):
                    # User
                    name_parts = []
                    if message.sender.first_name:
                        name_parts.append(message.sender.first_name)
                    if getattr(message.sender, "last_name", None):
                        name_parts.append(message.sender.last_name)
                    return (
                        " ".join(name_parts)
                        if name_parts
                        else f"User {message.sender.id}"
                    )
                elif hasattr(message.sender, "title"):
                    # Channel/Group
                    return str(message.sender.title)
                else:
                    return f"User {message.sender.id}"
            else:
                return "Unknown User"
        except Exception:
            return "Unknown User"

    def _format_timestamp(self, dt) -> str:
        """Format datetime in Telegram export format."""
        return str(dt.strftime("%d.%m.%Y %H:%M"))

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

        # Export all configured targets
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
