"""
Metrics collection and reporting for TOBS (Phase 3 Task B.1).

Provides centralized metrics collection with JSON export for performance monitoring.
All metrics are collected in-memory and can be exported to JSON for analysis.

Metrics collected:
- Export statistics (per-target duration, success rate)
- Worker statistics (per-worker success rate, error count)
- Queue statistics (depth, wait time)
- System statistics (uptime, error frequency)
- Message fetch statistics (timeouts, retries)
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExportMetrics:
    """Metrics for a single export target."""

    target_name: str
    target_type: str  # 'channel', 'chat', 'forum', etc.
    start_time: float
    end_time: Optional[float] = None
    duration_seconds: float = 0.0
    message_count: int = 0
    success: bool = False
    error: Optional[str] = None
    error_count: int = 0
    retry_count: int = 0
    timeout_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class WorkerMetrics:
    """Metrics for a single worker in media/manager.py."""

    worker_id: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_retried: int = 0
    total_errors: int = 0
    uptime_seconds: float = 0.0
    error_history: List[Dict[str, Any]] = field(default_factory=list)  # Last 10 errors

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Only keep last 10 errors
        data["error_history"] = self.error_history[-10:]
        return data


@dataclass
class QueueMetrics:
    """Metrics for queue operations."""

    total_items_queued: int = 0
    total_items_processed: int = 0
    current_depth: int = 0
    max_depth: int = 0
    avg_wait_time_ms: float = 0.0
    timeout_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class SystemMetrics:
    """Overall system metrics."""

    start_time: float = field(default_factory=time.time)
    uptime_seconds: float = 0.0
    total_exports: int = 0
    successful_exports: int = 0
    failed_exports: int = 0
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    error_frequency: float = 0.0  # Errors per minute

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class MetricsCollector:
    """
    Centralized metrics collection for the TOBS system.

    Thread-safe in-memory collection with JSON export.
    Designed for performance monitoring without significant overhead.

    Usage:
        collector = MetricsCollector()
        collector.record_export_start(target_name, target_type)
        # ... perform export ...
        collector.record_export_complete(target_name, duration=45.2, success=True)

        # Export metrics
        metrics_dict = collector.get_snapshot()
        collector.export_json("metrics.json")
    """

    def __init__(self):
        """Initialize metrics collector."""
        self.system = SystemMetrics()
        self.exports: Dict[str, ExportMetrics] = {}
        self.workers: Dict[str, WorkerMetrics] = {}
        self.queue = QueueMetrics()
        self._export_in_progress: Dict[str, float] = {}  # target_name -> start_time
        self._error_log: List[Dict[str, Any]] = []  # Global error log (last 100)

        logger.debug("MetricsCollector initialized")

    def record_export_start(self, target_name: str, target_type: str) -> None:
        """
        Record the start of an export operation.

        Args:
            target_name: Name of the export target
            target_type: Type of target (channel, chat, forum, etc.)
        """
        start_time = time.time()
        self._export_in_progress[target_name] = start_time

        self.exports[target_name] = ExportMetrics(
            target_name=target_name, target_type=target_type, start_time=start_time
        )
        self.system.total_exports += 1

        logger.debug(f"Export started for {target_name} ({target_type})")

    def record_export_complete(
        self,
        target_name: str,
        message_count: int = 0,
        success: bool = True,
        error: Optional[str] = None,
        error_count: int = 0,
        retry_count: int = 0,
        timeout_count: int = 0,
    ) -> None:
        """
        Record completion of an export operation.

        Args:
            target_name: Name of the export target
            message_count: Number of messages exported
            success: Whether export was successful
            error: Error message if failed
            error_count: Number of errors during export
            retry_count: Number of retries
            timeout_count: Number of timeouts
        """
        if target_name not in self.exports:
            logger.warning(
                f"Export completion recorded for unknown target: {target_name}"
            )
            self.record_export_start(target_name, "unknown")

        now = time.time()
        metrics = self.exports[target_name]
        metrics.end_time = now
        metrics.duration_seconds = now - metrics.start_time
        metrics.message_count = message_count
        metrics.success = success
        metrics.error = error
        metrics.error_count = error_count
        metrics.retry_count = retry_count
        metrics.timeout_count = timeout_count

        if success:
            self.system.successful_exports += 1
        else:
            self.system.failed_exports += 1

        # Remove from in-progress
        self._export_in_progress.pop(target_name, None)

        log_msg = (
            f"Export completed for {target_name}: "
            f"{message_count} messages, "
            f"duration={metrics.duration_seconds:.1f}s, "
            f"success={success}"
        )
        if error:
            log_msg += f", error={error}"
        logger.info(log_msg)

    def record_worker_task(
        self,
        worker_id: str,
        success: bool = True,
        error: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> None:
        """
        Record completion of a task by a worker.

        Args:
            worker_id: ID of the worker
            success: Whether task succeeded
            error: Error message if failed
            duration_ms: Task duration in milliseconds
        """
        if worker_id not in self.workers:
            self.workers[worker_id] = WorkerMetrics(worker_id=worker_id)

        worker = self.workers[worker_id]
        self.system.total_tasks += 1

        if success:
            worker.tasks_completed += 1
            self.system.successful_tasks += 1
        else:
            worker.tasks_failed += 1
            self.system.failed_tasks += 1

            error_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": error or "Unknown error",
                "duration_ms": duration_ms,
            }
            worker.error_history.append(error_entry)

            # Keep only last 10 errors per worker
            if len(worker.error_history) > 10:
                worker.error_history = worker.error_history[-10:]

            # Log to global error log
            self._log_error(worker_id, error or "Unknown error", duration_ms)

    def record_worker_retry(self, worker_id: str) -> None:
        """Record a retry attempt by a worker."""
        if worker_id not in self.workers:
            self.workers[worker_id] = WorkerMetrics(worker_id=worker_id)

        self.workers[worker_id].tasks_retried += 1

    def record_queue_event(self, depth: int, processed: int = 0) -> None:
        """
        Record queue state change.

        Args:
            depth: Current queue depth
            processed: Number of items processed in this batch
        """
        self.queue.current_depth = depth
        self.queue.max_depth = max(self.queue.max_depth, depth)
        self.queue.total_items_processed += processed

    def record_queue_timeout(self) -> None:
        """Record a queue timeout event."""
        self.queue.timeout_count += 1

    def record_message_fetch(
        self, count: int = 0, timeout_count: int = 0, retry_count: int = 0
    ) -> None:
        """
        Record message fetch statistics.

        Args:
            count: Number of messages fetched
            timeout_count: Number of timeouts during fetch
            retry_count: Number of retries
        """
        if timeout_count > 0:
            # Add to error log
            self._log_error(
                "telegram_client",
                f"Message fetch: {timeout_count} timeouts, {retry_count} retries",
                0,
            )

    def _log_error(self, source: str, error: str, duration_ms: float) -> None:
        """
        Log an error to global error log.

        Args:
            source: Source of the error (worker_id, component name, etc.)
            error: Error description
            duration_ms: Duration related to error
        """
        error_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "error": error,
            "duration_ms": duration_ms,
        }
        self._error_log.append(error_entry)

        # Keep only last 100 errors
        if len(self._error_log) > 100:
            self._error_log = self._error_log[-100:]

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Get current metrics snapshot.

        Returns:
            Dictionary with all collected metrics
        """
        # Update system uptime
        self.system.uptime_seconds = time.time() - self.system.start_time

        # Calculate error frequency (errors per minute)
        if self.system.uptime_seconds > 0:
            minutes = self.system.uptime_seconds / 60
            if minutes > 0:
                self.system.error_frequency = self.system.failed_tasks / minutes

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": self.system.to_dict(),
            "exports": {
                name: metrics.to_dict() for name, metrics in self.exports.items()
            },
            "workers": {
                worker_id: metrics.to_dict()
                for worker_id, metrics in self.workers.items()
            },
            "queue": self.queue.to_dict(),
            "error_log": self._error_log[-50:],  # Last 50 errors in snapshot
            "in_progress": {
                target: time.time() - start_time
                for target, start_time in self._export_in_progress.items()
            },
        }

        return snapshot

    def export_json(self, filepath: str) -> None:
        """
        Export metrics to JSON file.

        Args:
            filepath: Path to write JSON file to
        """
        try:
            snapshot = self.get_snapshot()

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, default=str)

            logger.info(f"Metrics exported to {filepath}")
        except Exception as e:
            logger.error(f"Failed to export metrics to {filepath}: {e}")

    def get_export_summary(self) -> Dict[str, Any]:
        """
        Get summary of all exports.

        Returns:
            Dictionary with export statistics
        """
        total = len(self.exports)
        successful = sum(1 for m in self.exports.values() if m.success)
        failed = total - successful

        total_messages = sum(m.message_count for m in self.exports.values())
        total_duration = sum(m.duration_seconds for m in self.exports.values())
        avg_duration = total_duration / total if total > 0 else 0

        return {
            "total_exports": total,
            "successful_exports": successful,
            "failed_exports": failed,
            "success_rate": successful / total if total > 0 else 0,
            "total_messages_exported": total_messages,
            "total_duration_seconds": total_duration,
            "average_duration_per_export": avg_duration,
            "by_type": self._get_exports_by_type(),
        }

    def _get_exports_by_type(self) -> Dict[str, Dict[str, Any]]:
        """Get export statistics grouped by type."""
        by_type: Dict[str, List[ExportMetrics]] = {}

        for metrics in self.exports.values():
            if metrics.target_type not in by_type:
                by_type[metrics.target_type] = []
            by_type[metrics.target_type].append(metrics)

        result = {}
        for target_type, metrics_list in by_type.items():
            total = len(metrics_list)
            successful = sum(1 for m in metrics_list if m.success)

            result[target_type] = {
                "count": total,
                "successful": successful,
                "failed": total - successful,
                "success_rate": successful / total if total > 0 else 0,
                "total_messages": sum(m.message_count for m in metrics_list),
                "total_duration_seconds": sum(m.duration_seconds for m in metrics_list),
            }

        return result

    def get_worker_summary(self) -> Dict[str, Any]:
        """
        Get summary of all workers.

        Returns:
            Dictionary with worker statistics
        """
        if not self.workers:
            return {"total_workers": 0}

        total_workers = len(self.workers)
        total_completed = sum(w.tasks_completed for w in self.workers.values())
        total_failed = sum(w.tasks_failed for w in self.workers.values())

        return {
            "total_workers": total_workers,
            "total_tasks_completed": total_completed,
            "total_tasks_failed": total_failed,
            "success_rate": total_completed / (total_completed + total_failed)
            if (total_completed + total_failed) > 0
            else 0,
            "by_worker": {
                worker_id: {
                    "tasks_completed": w.tasks_completed,
                    "tasks_failed": w.tasks_failed,
                    "success_rate": w.tasks_completed
                    / (w.tasks_completed + w.tasks_failed)
                    if (w.tasks_completed + w.tasks_failed) > 0
                    else 0,
                }
                for worker_id, w in self.workers.items()
            },
        }

    def reset(self) -> None:
        """Reset all metrics (for testing purposes)."""
        self.system = SystemMetrics()
        self.exports.clear()
        self.workers.clear()
        self.queue = QueueMetrics()
        self._export_in_progress.clear()
        self._error_log.clear()

        logger.debug("Metrics collector reset")


# Global singleton instance
_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """
    Get or create the global metrics collector instance.

    Returns:
        The global MetricsCollector instance
    """
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


def reset_metrics_collector() -> None:
    """Reset the global metrics collector (for testing)."""
    global _collector
    if _collector:
        _collector.reset()
