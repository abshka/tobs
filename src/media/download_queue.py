"""
Background Media Download Queue.

Provides non-blocking media downloads during export by decoupling
download operations from the message processing loop.

Architecture:
- Producer: Message processor queues download tasks
- Consumer: Background workers process queue with controlled concurrency
- Result: Message export continues without waiting for downloads
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from loguru import logger
from telethon.tl.types import Message


class DownloadStatus(Enum):
    """Status of a download task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    """A single download task in the queue."""

    task_id: str
    message: Message
    entity_id: Union[str, int]
    output_path: Path
    media_type: str
    expected_size: int = 0
    status: DownloadStatus = DownloadStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    attempts: int = 0
    max_attempts: int = 3
    error: Optional[str] = None
    result_path: Optional[Path] = None

    @property
    def duration(self) -> Optional[float]:
        """Get download duration if completed."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None

    @property
    def wait_time(self) -> float:
        """Time spent waiting in queue."""
        start = self.started_at or time.time()
        return start - self.created_at


@dataclass
class QueueStats:
    """Statistics for the download queue."""

    total_queued: int = 0
    total_completed: int = 0
    total_failed: int = 0
    total_bytes_downloaded: int = 0
    total_download_time: float = 0.0
    peak_queue_size: int = 0

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.total_completed + self.total_failed
        if total == 0:
            return 1.0
        return self.total_completed / total

    @property
    def avg_download_time(self) -> float:
        """Average download time per file."""
        if self.total_completed == 0:
            return 0.0
        return self.total_download_time / self.total_completed


class MediaDownloadQueue:
    """
    Background queue for non-blocking media downloads.

    Usage:
        queue = MediaDownloadQueue(downloader, max_workers=3)
        await queue.start()

        # In message processing loop:
        task_id = await queue.enqueue(message, entity_id, output_path, media_type)
        # Returns immediately, download happens in background

        # At end of export:
        await queue.wait_all()  # Optional: wait for downloads to complete
        await queue.stop()
    """

    def __init__(
        self,
        downloader: Any,  # MediaDownloader instance
        max_workers: int = 3,
        max_queue_size: int = 1000,
        retry_delay: float = 2.0,
    ):
        """
        Initialize the download queue.

        Args:
            downloader: MediaDownloader instance for actual downloads
            max_workers: Maximum concurrent download workers
            max_queue_size: Maximum tasks in queue (0 = unlimited)
            retry_delay: Delay between retry attempts
        """
        self.downloader = downloader
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.retry_delay = retry_delay

        # Queue and tracking
        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=max_queue_size if max_queue_size > 0 else 0
        )
        self._tasks: Dict[str, DownloadTask] = {}
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Statistics
        self.stats = QueueStats()

        # Callbacks
        self._on_complete: Optional[Callable[[DownloadTask], None]] = None
        self._on_error: Optional[Callable[[DownloadTask], None]] = None

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        logger.info(f"MediaDownloadQueue initialized with {max_workers} workers")

    async def start(self) -> None:
        """Start the background download workers."""
        if self._running:
            logger.warning("Download queue already running")
            return

        self._running = True
        self._shutdown_event.clear()

        # Start worker coroutines
        for i in range(self.max_workers):
            worker = asyncio.create_task(
                self._worker_loop(f"worker_{i}"), name=f"download_worker_{i}"
            )
            self._workers.append(worker)

        logger.info(f"Started {self.max_workers} download workers")

    async def stop(self, wait_for_completion: bool = True) -> None:
        """
        Stop the download queue.

        Args:
            wait_for_completion: If True, wait for pending downloads to complete
        """
        if not self._running:
            return

        logger.info("Stopping download queue...")

        if wait_for_completion:
            await self.wait_all()

        self._running = False
        self._shutdown_event.set()

        # Cancel all workers
        for worker in self._workers:
            worker.cancel()

        # Wait for workers to finish
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)

        self._workers.clear()
        logger.info("Download queue stopped")

    async def enqueue(
        self,
        message: Message,
        entity_id: Union[str, int],
        output_path: Path,
        media_type: str,
    ) -> str:
        """
        Add a download task to the queue.

        Returns immediately - download happens in background.

        Args:
            message: Telegram message with media
            entity_id: Entity ID (chat/channel)
            output_path: Path where file should be saved
            media_type: Type of media (photo, video, etc.)

        Returns:
            Task ID for tracking
        """
        task_id = f"{entity_id}_{message.id}_{int(time.time() * 1000)}"

        expected_size = 0
        if hasattr(message, "file") and message.file:
            expected_size = getattr(message.file, "size", 0)

        task = DownloadTask(
            task_id=task_id,
            message=message,
            entity_id=entity_id,
            output_path=output_path,
            media_type=media_type,
            expected_size=expected_size,
        )

        async with self._lock:
            self._tasks[task_id] = task
            self.stats.total_queued += 1
            current_size = self._queue.qsize() + 1
            if current_size > self.stats.peak_queue_size:
                self.stats.peak_queue_size = current_size

        await self._queue.put(task)
        logger.debug(
            f"Queued download: {task_id} ({media_type}, {expected_size} bytes)"
        )

        return task_id

    def get_task_status(self, task_id: str) -> Optional[DownloadTask]:
        """Get status of a specific task."""
        return self._tasks.get(task_id)

    def get_pending_count(self) -> int:
        """Get number of pending downloads."""
        return self._queue.qsize()

    def get_in_progress_count(self) -> int:
        """Get number of downloads in progress."""
        return sum(
            1 for t in self._tasks.values() if t.status == DownloadStatus.IN_PROGRESS
        )

    def get_completed_paths(self) -> List[Path]:
        """Get list of successfully downloaded file paths."""
        return [
            t.result_path
            for t in self._tasks.values()
            if t.status == DownloadStatus.COMPLETED and t.result_path
        ]

    async def wait_all(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for all queued downloads to complete.

        Args:
            timeout: Maximum time to wait (None = wait forever)

        Returns:
            True if all downloads completed, False if timeout
        """
        start_time = time.time()

        while True:
            pending = self.get_pending_count()
            in_progress = self.get_in_progress_count()

            if pending == 0 and in_progress == 0:
                logger.info("All downloads completed")
                return True

            if timeout and (time.time() - start_time) > timeout:
                logger.warning(
                    f"Timeout waiting for downloads. Pending: {pending}, In Progress: {in_progress}"
                )
                return False

            await asyncio.sleep(0.5)

    def get_progress_info(self) -> dict:
        """
        Get current progress information for display.
        
        Returns:
            Dict with completed, failed, pending, in_progress, total counts
        """
        completed = self.stats.total_completed
        failed = self.stats.total_failed
        pending = self.get_pending_count()
        in_progress = self.get_in_progress_count()
        total = self.stats.total_queued
        
        return {
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "in_progress": in_progress,
            "total": total,
            "bytes_downloaded": self.stats.total_bytes_downloaded,
        }

    async def _worker_loop(self, worker_name: str) -> None:
        """Main loop for a download worker."""
        logger.debug(f"{worker_name} started")

        while self._running and not self._shutdown_event.is_set():
            try:
                try:
                    task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                await self._process_task(task, worker_name)
                self._queue.task_done()

            except asyncio.CancelledError:
                logger.debug(f"{worker_name} cancelled")
                break
            except Exception as e:
                logger.error(f"{worker_name} error: {e}")
                await asyncio.sleep(1.0)

        logger.debug(f"{worker_name} stopped")

    async def _process_task(self, task: DownloadTask, worker_name: str) -> None:
        """Process a single download task."""
        task.status = DownloadStatus.IN_PROGRESS
        task.started_at = time.time()
        task.attempts += 1

        logger.debug(
            f"{worker_name} processing: {task.task_id} (attempt {task.attempts}/{task.max_attempts})"
        )

        try:
            result_path = await self.downloader.download_media(
                message=task.message,
                progress_queue=None,
                task_id=task.task_id,
            )

            if result_path and result_path.exists():
                task.status = DownloadStatus.COMPLETED
                task.completed_at = time.time()
                task.result_path = result_path

                async with self._lock:
                    self.stats.total_completed += 1
                    if task.duration:
                        self.stats.total_download_time += task.duration
                    try:
                        self.stats.total_bytes_downloaded += result_path.stat().st_size
                    except Exception:
                        pass

                # Move to output path if different
                if result_path != task.output_path:
                    try:
                        import shutil
                        task.output_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(result_path), str(task.output_path))
                        task.result_path = task.output_path
                    except Exception as e:
                        logger.warning(f"Failed to move file to output path: {e}")

                logger.debug(
                    f"{worker_name} completed: {task.task_id} ({task.duration:.1f}s)"
                )

                if self._on_complete:
                    try:
                        self._on_complete(task)
                    except Exception:
                        pass
            else:
                raise RuntimeError("Download returned no result")

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"{worker_name} failed: {task.task_id} - {error_msg}")

            if task.attempts < task.max_attempts:
                task.status = DownloadStatus.PENDING
                await asyncio.sleep(self.retry_delay * task.attempts)
                await self._queue.put(task)
                logger.debug(f"Re-queued: {task.task_id}")
            else:
                task.status = DownloadStatus.FAILED
                task.completed_at = time.time()
                task.error = error_msg

                async with self._lock:
                    self.stats.total_failed += 1

                logger.error(
                    f"{worker_name} gave up: {task.task_id} after {task.attempts} attempts"
                )

                if self._on_error:
                    try:
                        self._on_error(task)
                    except Exception:
                        pass

    def set_callbacks(
        self,
        on_complete: Optional[Callable[[DownloadTask], None]] = None,
        on_error: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """Set callbacks for task completion/error."""
        self._on_complete = on_complete
        self._on_error = on_error

    def get_stats_summary(self) -> Dict[str, Any]:
        """Get summary of queue statistics."""
        return {
            "total_queued": self.stats.total_queued,
            "total_completed": self.stats.total_completed,
            "total_failed": self.stats.total_failed,
            "pending": self.get_pending_count(),
            "in_progress": self.get_in_progress_count(),
            "success_rate": f"{self.stats.success_rate:.1%}",
            "total_bytes": self.stats.total_bytes_downloaded,
            "total_mb": f"{self.stats.total_bytes_downloaded / (1024 * 1024):.1f}",
            "avg_download_time": f"{self.stats.avg_download_time:.1f}s",
            "peak_queue_size": self.stats.peak_queue_size,
        }

    def log_stats(self) -> None:
        """Log current queue statistics."""
        stats = self.get_stats_summary()
        logger.info(
            f"📊 Download Queue Stats: "
            f"Completed: {stats['total_completed']}, "
            f"Failed: {stats['total_failed']}, "
            f"Pending: {stats['pending']}, "
            f"In Progress: {stats['in_progress']}, "
            f"Total: {stats['total_mb']} MB"
        )
