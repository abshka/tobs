"""
Tests for Phase 3 Task B.1 - MetricsCollector Foundation.

Tests verify:
1. Metrics collection and recording
2. JSON export functionality
3. Summary generation
4. Data integrity and threading safety
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.core.metrics import (
    MetricsCollector,
    ExportMetrics,
    WorkerMetrics,
    QueueMetrics,
    SystemMetrics,
    get_metrics_collector,
    reset_metrics_collector,
)


class TestMetricsCollectorBasics:
    """Test basic MetricsCollector functionality."""

    def test_collector_initialization(self):
        """Verify collector initializes correctly."""
        collector = MetricsCollector()
        
        assert collector.system is not None
        assert collector.exports == {}
        assert collector.workers == {}
        assert collector.queue is not None

    def test_singleton_pattern(self):
        """Verify get_metrics_collector returns same instance."""
        reset_metrics_collector()
        
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        
        assert collector1 is collector2

    def test_reset_functionality(self):
        """Verify reset clears all data."""
        collector = get_metrics_collector()
        collector.record_export_start("test_target", "channel")
        
        assert len(collector.exports) > 0
        
        collector.reset()
        assert len(collector.exports) == 0
        assert collector.system.total_exports == 0


class TestExportMetrics:
    """Test export metrics recording."""

    def test_record_export_start(self):
        """Verify export start recording."""
        collector = MetricsCollector()
        
        collector.record_export_start("test_channel", "channel")
        
        assert "test_channel" in collector.exports
        assert collector.exports["test_channel"].target_name == "test_channel"
        assert collector.exports["test_channel"].target_type == "channel"
        assert collector.system.total_exports == 1

    def test_record_export_complete_success(self):
        """Verify successful export completion."""
        collector = MetricsCollector()
        
        collector.record_export_start("test_channel", "channel")
        collector.record_export_complete(
            "test_channel",
            message_count=100,
            success=True,
            error_count=0,
            retry_count=1
        )
        
        metrics = collector.exports["test_channel"]
        assert metrics.success is True
        assert metrics.message_count == 100
        assert metrics.error_count == 0
        assert metrics.retry_count == 1
        assert metrics.duration_seconds > 0
        assert collector.system.successful_exports == 1

    def test_record_export_complete_failure(self):
        """Verify failed export recording."""
        collector = MetricsCollector()
        
        collector.record_export_start("test_chat", "chat")
        collector.record_export_complete(
            "test_chat",
            success=False,
            error="Connection timeout",
            error_count=3
        )
        
        metrics = collector.exports["test_chat"]
        assert metrics.success is False
        assert metrics.error == "Connection timeout"
        assert metrics.error_count == 3
        assert collector.system.failed_exports == 1

    def test_export_summary(self):
        """Verify export summary generation."""
        collector = MetricsCollector()
        
        # Record multiple exports
        for i in range(3):
            collector.record_export_start(f"target_{i}", "channel")
            collector.record_export_complete(
                f"target_{i}",
                message_count=50 + i * 10,
                success=(i < 2)  # First two succeed
            )
        
        summary = collector.get_export_summary()
        
        assert summary["total_exports"] == 3
        assert summary["successful_exports"] == 2
        assert summary["failed_exports"] == 1
        assert summary["success_rate"] == pytest.approx(2/3)
        assert summary["total_messages_exported"] == 180  # 50 + 60 + 70


class TestWorkerMetrics:
    """Test worker metrics collection."""

    def test_record_worker_task_success(self):
        """Verify successful worker task recording."""
        collector = MetricsCollector()
        
        collector.record_worker_task("worker_0", success=True, duration_ms=100)
        
        assert "worker_0" in collector.workers
        assert collector.workers["worker_0"].tasks_completed == 1
        assert collector.workers["worker_0"].tasks_failed == 0
        assert collector.system.successful_tasks == 1

    def test_record_worker_task_failure(self):
        """Verify failed worker task recording."""
        collector = MetricsCollector()
        
        collector.record_worker_task(
            "worker_0",
            success=False,
            error="Processing failed",
            duration_ms=500
        )
        
        assert collector.workers["worker_0"].tasks_failed == 1
        assert len(collector.workers["worker_0"].error_history) == 1
        assert collector.workers["worker_0"].error_history[0]["error"] == "Processing failed"
        assert collector.system.failed_tasks == 1

    def test_worker_retry_recording(self):
        """Verify worker retry recording."""
        collector = MetricsCollector()
        
        collector.record_worker_retry("worker_0")
        collector.record_worker_retry("worker_0")
        
        assert collector.workers["worker_0"].tasks_retried == 2

    def test_worker_error_history_limit(self):
        """Verify error history is limited to 10 entries."""
        collector = MetricsCollector()
        worker = collector.workers["worker_0"] = WorkerMetrics("worker_0")
        
        # Add 15 errors
        for i in range(15):
            collector.record_worker_task(
                "worker_0",
                success=False,
                error=f"Error {i}"
            )
        
        # Should only keep last 10
        assert len(collector.workers["worker_0"].error_history) == 10
        assert collector.workers["worker_0"].error_history[0]["error"] == "Error 5"

    def test_worker_summary(self):
        """Verify worker summary generation."""
        collector = MetricsCollector()
        
        # Record tasks for multiple workers
        for worker_id in ["w1", "w2", "w3"]:
            for i in range(5):
                collector.record_worker_task(
                    worker_id,
                    success=(i < 3)  # First 3 succeed
                )
        
        summary = collector.get_worker_summary()
        
        assert summary["total_workers"] == 3
        assert summary["total_tasks_completed"] == 9
        assert summary["total_tasks_failed"] == 6
        assert summary["success_rate"] == pytest.approx(0.6)


class TestQueueMetrics:
    """Test queue metrics recording."""

    def test_record_queue_event(self):
        """Verify queue event recording."""
        collector = MetricsCollector()
        
        collector.record_queue_event(depth=5, processed=2)
        
        assert collector.queue.current_depth == 5
        assert collector.queue.total_items_processed == 2
        assert collector.queue.max_depth == 5

    def test_queue_max_depth_tracking(self):
        """Verify max depth tracking."""
        collector = MetricsCollector()
        
        collector.record_queue_event(depth=3)
        collector.record_queue_event(depth=8)
        collector.record_queue_event(depth=5)
        
        assert collector.queue.max_depth == 8

    def test_record_queue_timeout(self):
        """Verify queue timeout recording."""
        collector = MetricsCollector()
        
        collector.record_queue_timeout()
        collector.record_queue_timeout()
        
        assert collector.queue.timeout_count == 2


class TestMessageFetchMetrics:
    """Test message fetch metrics."""

    def test_record_message_fetch(self):
        """Verify message fetch recording."""
        collector = MetricsCollector()
        
        collector.record_message_fetch(count=100, timeout_count=2, retry_count=3)
        
        # Check that error was logged
        assert len(collector._error_log) == 1
        assert "timeouts" in collector._error_log[0]["error"].lower()


class TestMetricsSnapshot:
    """Test snapshot generation."""

    def test_get_snapshot(self):
        """Verify snapshot contains all metrics."""
        collector = MetricsCollector()
        
        collector.record_export_start("test", "channel")
        collector.record_export_complete("test", message_count=100, success=True)
        collector.record_worker_task("w1", success=True)
        collector.record_queue_event(depth=5)
        
        snapshot = collector.get_snapshot()
        
        assert "timestamp" in snapshot
        assert "system" in snapshot
        assert "exports" in snapshot
        assert "workers" in snapshot
        assert "queue" in snapshot
        assert "error_log" in snapshot
        assert "in_progress" in snapshot
        
        assert snapshot["system"]["total_exports"] == 1
        assert "test" in snapshot["exports"]
        assert "w1" in snapshot["workers"]

    def test_snapshot_includes_in_progress(self):
        """Verify in-progress exports in snapshot."""
        collector = MetricsCollector()
        
        collector.record_export_start("export1", "channel")
        collector.record_export_start("export2", "chat")
        
        snapshot = collector.get_snapshot()
        
        assert "export1" in snapshot["in_progress"]
        assert "export2" in snapshot["in_progress"]


class TestJSONExport:
    """Test JSON export functionality."""

    def test_export_json(self):
        """Verify JSON export works."""
        collector = MetricsCollector()
        
        # Record some data
        collector.record_export_start("test_channel", "channel")
        collector.record_export_complete(
            "test_channel",
            message_count=100,
            success=True
        )
        
        # Export to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        
        try:
            collector.export_json(temp_path)
            
            # Verify file exists and is valid JSON
            assert Path(temp_path).exists()
            
            with open(temp_path, 'r') as f:
                data = json.load(f)
            
            assert "timestamp" in data
            assert "system" in data
            assert "test_channel" in data["exports"]
            
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_export_json_with_complex_data(self):
        """Verify JSON export with complex metrics."""
        collector = MetricsCollector()
        
        # Record multiple types of data
        for i in range(3):
            collector.record_export_start(f"export_{i}", "channel")
            collector.record_export_complete(
                f"export_{i}",
                message_count=50 * (i + 1),
                success=(i < 2),
                error="Test error" if i == 2 else None
            )
        
        for j in range(3):
            for k in range(5):
                collector.record_worker_task(
                    f"worker_{j}",
                    success=(k < 3)
                )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        
        try:
            collector.export_json(temp_path)
            
            with open(temp_path, 'r') as f:
                data = json.load(f)
            
            # Verify structure
            assert len(data["exports"]) == 3
            assert len(data["workers"]) == 3
            
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestDataTypes:
    """Test data type conversions."""

    def test_export_metrics_to_dict(self):
        """Verify ExportMetrics.to_dict()."""
        metrics = ExportMetrics(
            target_name="test",
            target_type="channel",
            start_time=100.0,
            end_time=105.0,
            duration_seconds=5.0,
            message_count=50,
            success=True
        )
        
        data = metrics.to_dict()
        
        assert isinstance(data, dict)
        assert data["target_name"] == "test"
        assert data["message_count"] == 50

    def test_worker_metrics_to_dict(self):
        """Verify WorkerMetrics.to_dict()."""
        worker = WorkerMetrics(worker_id="w1")
        worker.tasks_completed = 10
        worker.tasks_failed = 2
        
        data = worker.to_dict()
        
        assert isinstance(data, dict)
        assert data["worker_id"] == "w1"
        assert data["tasks_completed"] == 10

    def test_system_metrics_to_dict(self):
        """Verify SystemMetrics.to_dict()."""
        system = SystemMetrics()
        system.total_exports = 5
        system.successful_exports = 4
        
        data = system.to_dict()
        
        assert isinstance(data, dict)
        assert data["total_exports"] == 5


class TestExportsByType:
    """Test export grouping by type."""

    def test_get_exports_by_type(self):
        """Verify exports grouped by type."""
        collector = MetricsCollector()
        
        # Record exports of different types
        for target_type in ["channel", "chat", "forum"]:
            for i in range(2):
                collector.record_export_start(
                    f"{target_type}_{i}",
                    target_type
                )
                collector.record_export_complete(
                    f"{target_type}_{i}",
                    success=(i == 0),
                    message_count=100
                )
        
        summary = collector.get_export_summary()
        by_type = summary["by_type"]
        
        assert len(by_type) == 3
        assert all(t in by_type for t in ["channel", "chat", "forum"])
        assert all(by_type[t]["count"] == 2 for t in by_type)
        assert all(by_type[t]["success_rate"] == 0.5 for t in by_type)


class TestErrorTracking:
    """Test error tracking functionality."""

    def test_error_log_limited(self):
        """Verify error log is limited to 100 entries."""
        collector = MetricsCollector()
        
        # Add 150 errors
        for i in range(150):
            collector._log_error("source", f"error_{i}", 0)
        
        # Should keep only last 100
        assert len(collector._error_log) == 100

    def test_snapshot_shows_recent_errors(self):
        """Verify snapshot shows recent errors."""
        collector = MetricsCollector()
        
        for i in range(20):
            collector._log_error("source", f"error_{i}", 0)
        
        snapshot = collector.get_snapshot()
        
        # Should include last 50 (but we only have 20)
        assert len(snapshot["error_log"]) == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
