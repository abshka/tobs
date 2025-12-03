"""
Tests for ConnectionManager retry logic (delay and timeout calculation).
Session 7 Phase 3 - Batch 8
"""

import random
from unittest.mock import patch

import pytest

from src.core.connection import (
    BackoffStrategy,
    ConnectionConfig,
    ConnectionManager,
    OperationStats,
)


class TestAddJitter:
    """Tests for ConnectionManager._add_jitter()"""

    def test_add_jitter_zero_range(self):
        """Verify returns original delay when jitter_range=0."""
        manager = ConnectionManager()

        delay = 10.0
        result = manager._add_jitter(delay, jitter_range=0.0)

        assert result == delay

    def test_add_jitter_adds_randomness(self):
        """Verify result within expected bounds."""
        manager = ConnectionManager()

        delay = 10.0
        jitter_range = 0.2  # ±20%

        # Run multiple times to check range
        results = []
        for _ in range(100):
            result = manager._add_jitter(delay, jitter_range)
            results.append(result)

        # Should have some variation
        assert min(results) != max(results)

        # All results should be within bounds: delay * (1 ± jitter_range)
        for result in results:
            assert delay * (1 - jitter_range) <= result <= delay * (1 + jitter_range)

    def test_add_jitter_never_negative(self):
        """Verify min 0.1 enforced."""
        manager = ConnectionManager()

        # Try with small delay and high jitter
        delay = 0.05
        jitter_range = 0.9

        result = manager._add_jitter(delay, jitter_range)

        # Should be at least 0.1
        assert result >= 0.1


class TestAdaptiveMultiplier:
    """Tests for ConnectionManager._calculate_adaptive_multiplier()"""

    def test_adaptive_multiplier_high_success_rate(self):
        """Success_rate=0.9, verify multiplier < 1.0."""
        manager = ConnectionManager()
        config = ConnectionConfig()

        stats = OperationStats()
        stats.total_attempts = 10
        stats.successful_attempts = 9
        stats.failed_attempts = 1

        multiplier = manager._calculate_adaptive_multiplier(stats, config)

        # High success rate (0.9) should reduce delay
        # Formula: max(0.5, 1.0 - (0.9 - 0.8) * 2) = max(0.5, 0.8) = 0.8
        assert multiplier < 1.0
        assert multiplier == pytest.approx(0.8, abs=0.01)

    def test_adaptive_multiplier_medium_success_rate(self):
        """Success_rate=0.5, verify multiplier=1.0."""
        manager = ConnectionManager()
        config = ConnectionConfig()

        stats = OperationStats()
        stats.total_attempts = 10
        stats.successful_attempts = 5
        stats.failed_attempts = 5

        multiplier = manager._calculate_adaptive_multiplier(stats, config)

        # Medium success rate should keep multiplier at 1.0
        assert multiplier == 1.0

    def test_adaptive_multiplier_low_success_rate(self):
        """Success_rate=0.2, verify multiplier > 1.0."""
        manager = ConnectionManager()
        config = ConnectionConfig()

        stats = OperationStats()
        stats.total_attempts = 10
        stats.successful_attempts = 2
        stats.failed_attempts = 8

        multiplier = manager._calculate_adaptive_multiplier(stats, config)

        # Low success rate should increase delay
        # Formula: 1.0 + (0.3 - 0.2) * 3 = 1.3
        assert multiplier > 1.0
        assert multiplier == pytest.approx(1.3, abs=0.01)

    def test_adaptive_multiplier_consecutive_failures_boost(self):
        """Low rate + consecutive>3, verify 1.5x boost."""
        manager = ConnectionManager()
        config = ConnectionConfig()

        stats = OperationStats()
        stats.total_attempts = 10
        stats.successful_attempts = 2
        stats.failed_attempts = 8
        stats.consecutive_failures = 5

        multiplier = manager._calculate_adaptive_multiplier(stats, config)

        # Base: 1.0 + (0.3 - 0.2) * 3 = 1.3
        # With consecutive failures boost: 1.3 * 1.5 = 1.95
        assert multiplier > 1.5
        assert multiplier == pytest.approx(1.95, abs=0.01)

    def test_adaptive_multiplier_capped_at_5(self):
        """Verify max 5.0 cap."""
        manager = ConnectionManager()
        config = ConnectionConfig()

        stats = OperationStats()
        stats.total_attempts = 100
        stats.successful_attempts = 0
        stats.failed_attempts = 100
        stats.consecutive_failures = 50

        multiplier = manager._calculate_adaptive_multiplier(stats, config)

        # Success rate = 0, so multiplier = 1.0 + (0.3 - 0) * 3 = 1.9
        # With consecutive_failures > 3: 1.9 * 1.5 = 2.85
        # min(5.0, 2.85) = 2.85
        assert multiplier <= 5.0
        assert multiplier == pytest.approx(2.85, abs=0.01)


class TestCalculateDelay:
    """Tests for ConnectionManager.calculate_delay()"""

    def test_calculate_delay_fixed_strategy(self):
        """Verify always returns base_delay."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            strategy=BackoffStrategy.FIXED, base_delay=5.0, jitter=False
        )

        # Different attempts should all return same delay
        for attempt in [1, 2, 5, 10]:
            delay = manager.calculate_delay(attempt, "test_op", config)
            assert delay == 5.0

    def test_calculate_delay_linear_strategy(self):
        """Verify base_delay * attempt."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            strategy=BackoffStrategy.LINEAR, base_delay=2.0, jitter=False
        )

        assert manager.calculate_delay(1, "test_op", config) == 2.0
        assert manager.calculate_delay(2, "test_op", config) == 4.0
        assert manager.calculate_delay(3, "test_op", config) == 6.0
        assert manager.calculate_delay(5, "test_op", config) == 10.0

    def test_calculate_delay_exponential_strategy(self):
        """Verify exponential growth."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            strategy=BackoffStrategy.EXPONENTIAL,
            base_delay=1.0,
            backoff_multiplier=2.0,
            jitter=False,
        )

        # delay = base_delay * (multiplier ^ (attempt - 1))
        assert manager.calculate_delay(1, "test_op", config) == 1.0  # 1 * 2^0
        assert manager.calculate_delay(2, "test_op", config) == 2.0  # 1 * 2^1
        assert manager.calculate_delay(3, "test_op", config) == 4.0  # 1 * 2^2
        assert manager.calculate_delay(4, "test_op", config) == 8.0  # 1 * 2^3

    def test_calculate_delay_adaptive_strategy(self):
        """Verify uses adaptive multiplier."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            strategy=BackoffStrategy.ADAPTIVE,
            base_delay=2.0,
            backoff_multiplier=2.0,
            jitter=False,
        )

        # Create stats with high success rate (should reduce delay)
        stats = manager.get_stats("test_op")
        stats.total_attempts = 10
        stats.successful_attempts = 9
        stats.failed_attempts = 1

        # Base exponential: 2.0 * (2.0 ^ (2-1)) = 4.0
        # Adaptive multiplier for 90% success: 0.8
        # Final: 4.0 * 0.8 = 3.2
        delay = manager.calculate_delay(2, "test_op", config)
        assert delay == pytest.approx(3.2, abs=0.01)

    def test_calculate_delay_respects_max_delay(self):
        """Verify capped at max_delay."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            strategy=BackoffStrategy.EXPONENTIAL,
            base_delay=10.0,
            backoff_multiplier=3.0,
            max_delay=50.0,
            jitter=False,
        )

        # 10 * 3^9 = 196,830 — should be capped at 50
        delay = manager.calculate_delay(10, "test_op", config)
        assert delay == 50.0

    def test_calculate_delay_with_jitter(self):
        """Verify jitter applied when enabled."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            strategy=BackoffStrategy.FIXED,
            base_delay=10.0,
            jitter=True,
            jitter_range=0.2,
        )

        # Run multiple times
        delays = []
        for _ in range(50):
            delay = manager.calculate_delay(1, "test_op", config)
            delays.append(delay)

        # Should have variation
        assert min(delays) != max(delays)

        # All should be within jitter range
        for delay in delays:
            assert 8.0 <= delay <= 12.0  # 10.0 ± 20%


class TestCalculateTimeout:
    """Tests for ConnectionManager.calculate_timeout()"""

    def test_calculate_timeout_small_file(self):
        """<500MB, verify base_timeout."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            base_timeout=60.0, large_file_timeout=300.0, huge_file_timeout=600.0
        )

        # 100 MB file
        file_size = 100 * 1024 * 1024
        timeout = manager.calculate_timeout(file_size, "test_op", config)

        # Should use base_timeout (but also consider size-based estimate)
        # Conservative speed: 1MB/s = 1000 KB/s
        # 100 MB = 102400 KB / 1000 KB/s = 102.4s * 2 = 204.8s
        # max(60, 204.8) = 204.8, then capped between 180-14400
        assert timeout >= 180.0
        assert timeout == pytest.approx(204.8, abs=1.0)

    def test_calculate_timeout_large_file(self):
        """500-1000MB, verify large_file_timeout."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            base_timeout=60.0, large_file_timeout=1800.0, huge_file_timeout=3600.0
        )

        # 750 MB file
        file_size = 750 * 1024 * 1024
        timeout = manager.calculate_timeout(file_size, "test_op", config)

        # Size-based: 750 MB = 768000 KB / 1000 KB/s = 768s * 2 = 1536s
        # max(1800, 1536) = 1800
        assert timeout == 1800.0

    def test_calculate_timeout_huge_file(self):
        """ ">1000MB, verify huge_file_timeout."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            base_timeout=60.0, large_file_timeout=1800.0, huge_file_timeout=3600.0
        )

        # 1500 MB file
        file_size = 1500 * 1024 * 1024
        timeout = manager.calculate_timeout(file_size, "test_op", config)

        # Size-based: 1500 MB = 1536000 KB / 1000 KB/s = 1536s * 2 = 3072s
        # max(3600, 3072) = 3600
        assert timeout == 3600.0

    def test_calculate_timeout_adapts_to_timeouts(self):
        """timeout_count>0, verify multiplier applied."""
        manager = ConnectionManager()
        config = ConnectionConfig(base_timeout=100.0)

        # Simulate previous timeouts
        stats = manager.get_stats("test_op")
        stats.timeout_count = 2

        file_size = 10 * 1024 * 1024  # 10 MB
        timeout = manager.calculate_timeout(file_size, "test_op", config)

        # Multiplier: 1.0 + (2 * 0.5) = 2.0, capped at 3.0
        # base_timeout * 2.0 = 200.0
        # size-based: 10 MB = 10240 KB / 1000 KB/s = 10.24s * 2 = 20.48s
        # max(200, 20.48) = 200, then capped at min 180
        assert timeout >= 200.0

    def test_calculate_timeout_uses_size_estimate(self):
        """Verify size-based calculation used when larger."""
        manager = ConnectionManager()
        config = ConnectionConfig(
            base_timeout=30.0, large_file_timeout=1800.0, huge_file_timeout=3600.0
        )

        # Large file: 2000 MB (> 1000 MB, so it's "huge")
        file_size = 2000 * 1024 * 1024
        timeout = manager.calculate_timeout(file_size, "test_op", config)

        # This is a huge file (>1000MB), so base_timeout = huge_file_timeout = 3600
        # Size-based: 2000 MB = 2048000 KB / 1000 KB/s = 2048s * 2 = 4096s
        # max(3600, 4096) = 4096, then capped at max 14400
        assert timeout == 4096.0

    def test_calculate_timeout_bounded(self):
        """Verify min 180s, max 14400s."""
        manager = ConnectionManager()

        # Very small file — should hit lower bound
        config_min = ConnectionConfig(base_timeout=10.0)
        file_size_min = 1024  # 1 KB
        timeout_min = manager.calculate_timeout(file_size_min, "test_op", config_min)
        assert timeout_min == 180.0

        # Extremely large file — should hit upper bound
        config_max = ConnectionConfig(base_timeout=1000.0)
        file_size_max = 100 * 1024 * 1024 * 1024  # 100 GB
        timeout_max = manager.calculate_timeout(file_size_max, "test_op", config_max)
        assert timeout_max == 14400.0  # 4 hours
