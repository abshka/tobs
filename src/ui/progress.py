"""
Progress management module for TOBS.
Handles progress tracking, reporting, and user feedback during export operations.
Provides modular progress management functionality.
"""

import asyncio
import time
from typing import Dict, Optional

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from ..utils import logger


class ProgressTask:
    """Represents a single progress task."""

    def __init__(self, task_id: TaskID, name: str, total: int):
        self.task_id = task_id
        self.name = name
        self.total = total
        self.completed = 0
        self.start_time = time.time()
        self.description = ""
        self.status = "running"  # running, completed, failed

    @property
    def completion_percentage(self) -> float:
        """Get completion percentage."""
        if self.total == 0:
            return 100.0
        return (self.completed / self.total) * 100

    @property
    def elapsed_time(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    @property
    def estimated_remaining(self) -> float:
        """Estimate remaining time in seconds."""
        if self.completed == 0:
            return 0.0

        rate = self.completed / self.elapsed_time
        remaining_items = self.total - self.completed

        if rate > 0:
            return remaining_items / rate
        return 0.0


class ProgressManager:
    """
    Manages progress tracking for export operations.
    Provides rich console interface with multiple progress bars.
    """

    def __init__(self):
        self.console = Console()
        self.progress = Progress(
            TextColumn("[bold blue]{task.fields[name]}", justify="left"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            MofNCompleteColumn(),
            "•",
            TimeElapsedColumn(),
            "•",
            TimeRemainingColumn(),
            console=self.console,
            expand=True,
        )

        self.tasks: Dict[str, ProgressTask] = {}
        self.live_display: Optional[Live] = None
        self._queue: Optional[asyncio.Queue] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Start the progress manager and begin live display."""
        if self._running:
            return

        self._running = True
        self._queue = asyncio.Queue()

        # Start the progress worker
        self._worker_task = asyncio.create_task(self._progress_worker())

        # Start live display
        self.live_display = Live(
            self.progress, console=self.console, refresh_per_second=4, transient=False
        )
        self.live_display.start()

        logger.info("Progress manager started")

    async def stop(self):
        """Stop the progress manager and clean up."""
        if not self._running:
            return

        self._running = False

        # Stop worker
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # Stop live display
        if self.live_display:
            self.live_display.stop()
            self.live_display = None

        logger.info("Progress manager stopped")

    async def add_task(
        self, task_id: str, name: str, total: int, description: str = ""
    ) -> str:
        """
        Add a new progress task.

        Args:
            task_id: Unique identifier for the task
            name: Display name for the task
            total: Total number of items to process
            description: Optional description

        Returns:
            Task ID for reference
        """
        if self._queue:
            await self._queue.put(
                {
                    "type": "add",
                    "task_id": task_id,
                    "name": name,
                    "total": total,
                    "description": description,
                }
            )
        return task_id

    async def update_task(
        self, task_id: str, advance: int = 1, description: Optional[str] = None
    ):
        """
        Update progress for a task.

        Args:
            task_id: Task identifier
            advance: Number of items to advance
            description: Optional new description
        """
        if self._queue:
            update_data = {"type": "update", "task_id": task_id, "advance": advance}

            if description is not None:
                update_data["description"] = description

            await self._queue.put(update_data)

    async def complete_task(self, task_id: str, status: str = "completed"):
        """
        Mark a task as completed.

        Args:
            task_id: Task identifier
            status: Final status (completed, failed, etc.)
        """
        if self._queue:
            await self._queue.put(
                {"type": "complete", "task_id": task_id, "status": status}
            )

    async def remove_task(self, task_id: str):
        """
        Remove a task from display.

        Args:
            task_id: Task identifier
        """
        if self._queue:
            await self._queue.put({"type": "remove", "task_id": task_id})

    async def _progress_worker(self):
        """Worker coroutine that processes progress updates."""
        try:
            while self._running:
                try:
                    # Wait for progress update with timeout
                    if self._queue is not None:
                        update = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                        await self._process_update(update)
                    else:
                        await asyncio.sleep(0.1)

                except asyncio.TimeoutError:
                    # No updates, continue
                    continue
                except Exception as e:
                    logger.error(f"Progress worker error: {e}")

        except asyncio.CancelledError:
            logger.debug("Progress worker cancelled")
            raise

    async def _process_update(self, update: Dict):
        """Process a single progress update."""
        update_type = update.get("type")
        task_id = update.get("task_id")

        if not task_id:
            return

        if update_type == "add":
            await self._add_task_internal(update)
        elif update_type == "update":
            await self._update_task_internal(update)
        elif update_type == "complete":
            await self._complete_task_internal(update)
        elif update_type == "remove":
            await self._remove_task_internal(update)

    async def _add_task_internal(self, update: Dict):
        """Internal method to add a task."""
        task_id = update["task_id"]
        name = update["name"]
        total = update["total"]
        description = update.get("description", "")

        # Add to rich progress
        rich_task_id = self.progress.add_task(name, total=total, name=name)

        # Create our task object
        progress_task = ProgressTask(rich_task_id, name, total)
        progress_task.description = description
        self.tasks[task_id] = progress_task

        logger.debug(f"Added progress task: {name} ({total} items)")

    async def _update_task_internal(self, update: Dict):
        """Internal method to update a task."""
        task_id = update["task_id"]
        advance = update.get("advance", 0)
        description = update.get("description")

        if task_id not in self.tasks:
            logger.warning(f"Unknown task ID: {task_id}")
            return

        task = self.tasks[task_id]

        # Update progress
        if advance > 0:
            task.completed += advance
            self.progress.update(task.task_id, advance=advance)

        # Update description
        if description is not None:
            task.description = description
            self.progress.update(task.task_id, description=description)

    async def _complete_task_internal(self, update: Dict):
        """Internal method to complete a task."""
        task_id = update["task_id"]
        status = update.get("status", "completed")

        if task_id not in self.tasks:
            return

        task = self.tasks[task_id]
        task.status = status

        # Update progress to 100%
        remaining = task.total - task.completed
        if remaining > 0:
            self.progress.update(task.task_id, advance=remaining)

        # Update description with status
        status_icon = "✅" if status == "completed" else "❌"
        self.progress.update(
            task.task_id, description=f"{status_icon} {status.title()}"
        )

        logger.debug(f"Completed task: {task.name} ({status})")

    async def _remove_task_internal(self, update: Dict):
        """Internal method to remove a task."""
        task_id = update["task_id"]

        if task_id not in self.tasks:
            return

        task = self.tasks[task_id]

        # Remove from rich progress
        self.progress.remove_task(task.task_id)

        # Remove from our tracking
        del self.tasks[task_id]

        logger.debug(f"Removed task: {task.name}")

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """
        Get current status of a task.

        Args:
            task_id: Task identifier

        Returns:
            Dictionary with task status or None if not found
        """
        if task_id not in self.tasks:
            return None

        task = self.tasks[task_id]
        return {
            "name": task.name,
            "total": task.total,
            "completed": task.completed,
            "percentage": task.completion_percentage,
            "elapsed": task.elapsed_time,
            "remaining": task.estimated_remaining,
            "status": task.status,
            "description": task.description,
        }

    def get_overall_progress(self) -> Dict:
        """Get overall progress across all tasks."""
        if not self.tasks:
            return {
                "total_tasks": 0,
                "completed_tasks": 0,
                "running_tasks": 0,
                "failed_tasks": 0,
                "overall_percentage": 0.0,
            }

        total_tasks = len(self.tasks)
        completed_tasks = sum(1 for t in self.tasks.values() if t.status == "completed")
        failed_tasks = sum(1 for t in self.tasks.values() if t.status == "failed")
        running_tasks = total_tasks - completed_tasks - failed_tasks

        # Calculate overall percentage based on items, not tasks
        total_items = sum(t.total for t in self.tasks.values())
        completed_items = sum(t.completed for t in self.tasks.values())

        overall_percentage = 0.0
        if total_items > 0:
            overall_percentage = (completed_items / total_items) * 100

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "running_tasks": running_tasks,
            "failed_tasks": failed_tasks,
            "overall_percentage": overall_percentage,
            "total_items": total_items,
            "completed_items": completed_items,
        }

    async def create_progress_queue(self) -> asyncio.Queue:
        """
        Create a queue for external progress updates.

        Returns:
            Queue that can be used to send progress updates
        """
        return self._queue if self._queue else asyncio.Queue()


# Global progress manager instance
_global_progress_manager: Optional[ProgressManager] = None


async def get_progress_manager() -> ProgressManager:
    """Get or create global progress manager instance."""
    global _global_progress_manager

    if _global_progress_manager is None:
        _global_progress_manager = ProgressManager()
        await _global_progress_manager.start()

    return _global_progress_manager


async def cleanup_progress_manager():
    """Cleanup global progress manager."""
    global _global_progress_manager

    if _global_progress_manager:
        await _global_progress_manager.stop()
        _global_progress_manager = None


class SimpleProgressReporter:
    """
    Simple progress reporter for cases where full ProgressManager is overkill.
    Provides basic console output without rich interface.
    """

    def __init__(self, name: str, total: int):
        self.name = name
        self.total = total
        self.completed = 0
        self.start_time = time.time()
        self.last_report = 0.0

    def update(self, advance: int = 1, description: Optional[str] = None):
        """Update progress and optionally print status."""
        self.completed += advance

        # Report progress every 10% or every 10 seconds
        now = time.time()
        percentage = (self.completed / self.total) * 100 if self.total > 0 else 0

        should_report = (
            percentage >= self.last_report + 10  # Every 10%
            or now - self.start_time >= 10  # Every 10 seconds
            or self.completed >= self.total  # At completion
        )

        if should_report:
            elapsed = now - self.start_time
            rate = self.completed / elapsed if elapsed > 0 else 0

            status_msg = (
                f"{self.name}: {self.completed}/{self.total} ({percentage:.1f}%)"
            )
            if rate > 0:
                status_msg += f" - {rate:.1f} items/sec"

            if description:
                status_msg += f" - {description}"

            logger.info(status_msg)
            self.last_report = percentage

    def complete(self, status: str = "completed"):
        """Mark as completed and print final status."""
        elapsed = time.time() - self.start_time
        rate = self.completed / elapsed if elapsed > 0 else 0

        final_msg = (
            f"{self.name} {status}: {self.completed}/{self.total} in {elapsed:.1f}s"
        )
        if rate > 0:
            final_msg += f" ({rate:.1f} items/sec)"

        logger.info(final_msg)
