"""
Tests for connection.py - OperationStats.

Batch 2 of Session 7: Statistics tracking and calculations.
"""

import time
from unittest.mock import patch

import pytest

from src.core.connection import OperationStats


class TestOperationStatsInitialization:
    """Tests for OperationStats initialization."""

    def test_operation_stats_default_initialization(self):
        """OperationStats should initialize with default values."""
        stats = OperationStats()

        assert stats.total_attempts == 0
        assert stats.successful_attempts == 0
        assert stats.failed_attempts == 0
        assert stats.avg_response_time == 0.0
        assert stats.last_success_time == 0.0
        assert stats.last_failure_time == 0.0
        assert stats.consecutive_failures == 0
        assert stats.consecutive_successes == 0
        assert stats.speed_history == []
        assert stats.stall_count == 0
        assert stats.timeout_count == 0


class TestOperationStatsSuccess:
    """Tests for success tracking."""

    def test_update_success_increments_total_attempts(self):
        """update_success should increment total attempts counter."""
        stats = OperationStats()
        stats.update_success()

        assert stats.total_attempts == 1

    def test_update_success_increments_successful_attempts(self):
        """update_success should increment successful attempts counter."""
        stats = OperationStats()
        stats.update_success()

        assert stats.successful_attempts == 1

    def test_update_success_increments_consecutive_successes(self):
        """update_success should increment consecutive successes."""
        stats = OperationStats()
        stats.update_success()
        stats.update_success()

        assert stats.consecutive_successes == 2

    def test_update_success_resets_consecutive_failures(self):
        """update_success should reset consecutive failures to zero."""
        stats = OperationStats()
        stats.consecutive_failures = 5

        stats.update_success()

        assert stats.consecutive_failures == 0

    @patch("time.time", return_value=1000.0)
    def test_update_success_records_last_success_time(self, mock_time):
        """update_success should record the current time."""
        stats = OperationStats()
        stats.update_success()

        assert stats.last_success_time == 1000.0

    def test_update_success_initializes_avg_response_time(self):
        """update_success should set avg_response_time on first call."""
        stats = OperationStats()
        stats.update_success(response_time=5.0)

        assert stats.avg_response_time == 5.0

    def test_update_success_calculates_weighted_avg_response_time(self):
        """update_success should calculate weighted average (80-20 split)."""
        stats = OperationStats()
        stats.update_success(response_time=10.0)  # Initialize
        stats.update_success(response_time=20.0)  # Update

        # Expected: 10.0 * 0.8 + 20.0 * 0.2 = 8.0 + 4.0 = 12.0
        assert stats.avg_response_time == 12.0


class TestOperationStatsFailure:
    """Tests for failure tracking."""

    def test_update_failure_increments_total_attempts(self):
        """update_failure should increment total attempts counter."""
        stats = OperationStats()
        stats.update_failure()

        assert stats.total_attempts == 1

    def test_update_failure_increments_failed_attempts(self):
        """update_failure should increment failed attempts counter."""
        stats = OperationStats()
        stats.update_failure()

        assert stats.failed_attempts == 1

    def test_update_failure_increments_consecutive_failures(self):
        """update_failure should increment consecutive failures."""
        stats = OperationStats()
        stats.update_failure()
        stats.update_failure()

        assert stats.consecutive_failures == 2

    def test_update_failure_resets_consecutive_successes(self):
        """update_failure should reset consecutive successes to zero."""
        stats = OperationStats()
        stats.consecutive_successes = 3

        stats.update_failure()

        assert stats.consecutive_successes == 0

    @patch("time.time", return_value=2000.0)
    def test_update_failure_records_last_failure_time(self, mock_time):
        """update_failure should record the current time."""
        stats = OperationStats()
        stats.update_failure()

        assert stats.last_failure_time == 2000.0


class TestOperationStatsSuccessRate:
    """Tests for success rate calculation."""

    def test_success_rate_with_mixed_results(self):
        """success_rate should calculate correctly with mixed success/failure."""
        stats = OperationStats()
        stats.update_success()
        stats.update_success()
        stats.update_success()
        stats.update_failure()

        # 3 successes, 1 failure = 3/4 = 0.75
        assert stats.success_rate == 0.75

    def test_success_rate_all_success(self):
        """success_rate should be 1.0 with all successes."""
        stats = OperationStats()
        stats.update_success()
        stats.update_success()

        assert stats.success_rate == 1.0

    def test_success_rate_all_failures(self):
        """success_rate should be 0.0 with all failures."""
        stats = OperationStats()
        stats.update_failure()
        stats.update_failure()

        assert stats.success_rate == 0.0

    def test_success_rate_no_attempts(self):
        """success_rate should default to 1.0 with no attempts."""
        stats = OperationStats()

        assert stats.success_rate == 1.0



class TestOperationStatsSpeedTracking:
    """Tests for speed tracking."""

    def test_record_speed_adds_to_history(self):
        """record_speed should add speed to history."""
        stats = OperationStats()
        stats.record_speed(100.5)

        assert len(stats.speed_history) == 1
        assert stats.speed_history[0] == 100.5

    def test_record_speed_maintains_window_size(self):
        """record_speed should maintain the specified window size."""
        stats = OperationStats()
        
        # Add 7 speeds with window size 5
        for speed in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]:
            stats.record_speed(speed, window_size=5)

        # Should only keep last 5
        assert len(stats.speed_history) == 5
        assert stats.speed_history == [30.0, 40.0, 50.0, 60.0, 70.0]

    def test_record_speed_uses_default_window_size(self):
        """record_speed should use window_size=5 as default."""
        stats = OperationStats()
        
        # Add 10 speeds without specifying window
        for speed in range(10):
            stats.record_speed(float(speed))

        # Should keep last 5 by default
        assert len(stats.speed_history) == 5

    def test_avg_speed_kbps_calculates_correctly(self):
        """avg_speed_kbps should calculate average of speed history."""
        stats = OperationStats()
        stats.record_speed(100.0)
        stats.record_speed(200.0)
        stats.record_speed(300.0)

        # Average: (100 + 200 + 300) / 3 = 200.0
        assert stats.avg_speed_kbps == 200.0

    def test_avg_speed_kbps_empty_history(self):
        """avg_speed_kbps should return 0.0 for empty history."""
        stats = OperationStats()

        assert stats.avg_speed_kbps == 0.0

    def test_avg_speed_kbps_single_entry(self):
        """avg_speed_kbps should handle single entry correctly."""
        stats = OperationStats()
        stats.record_speed(150.5)

        assert stats.avg_speed_kbps == 150.5
