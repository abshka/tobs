"""
Tests for ConnectionManager throttling logic.
Covers: is_throttled, calculate_throttle_delay

Batch 9: Throttling Logic (~12 tests)
"""

from unittest.mock import patch

import pytest

from src.core.connection import ConnectionConfig


class TestIsThrottled:
    """Tests for is_throttled method."""

    @pytest.mark.asyncio
    async def test_is_throttled_insufficient_data(self, connection_manager):
        """Should return False when speed_history has fewer than detection_window items."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")

        # Add only 3 items (less than detection_window=5)
        stats.speed_history = [150, 120, 110]

        result = connection_manager.is_throttled("test_op", config)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_throttled_exactly_detection_window(self, connection_manager):
        """Should evaluate throttling when exactly detection_window items present."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")

        # Add exactly 5 items with avg speed < threshold
        stats.speed_history = [80, 85, 90, 75, 70]  # avg = 80 < 100

        result = connection_manager.is_throttled("test_op", config)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_throttled_above_threshold(self, connection_manager):
        """Should return False when avg speed is above threshold."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")

        # avg speed = 120 > 100
        stats.speed_history = [110, 120, 130, 115, 125]

        result = connection_manager.is_throttled("test_op", config)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_throttled_below_threshold(self, connection_manager):
        """Should return True when avg speed is below threshold."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")

        # avg speed = 60 < 100
        stats.speed_history = [50, 60, 70, 55, 65]

        result = connection_manager.is_throttled("test_op", config)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_throttled_exact_threshold(self, connection_manager):
        """Should return False when avg speed equals threshold (not strictly less)."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")

        # avg speed = exactly 100
        stats.speed_history = [100, 100, 100, 100, 100]

        result = connection_manager.is_throttled("test_op", config)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_throttled_more_than_detection_window(self, connection_manager):
        """Should only evaluate the most recent detection_window items."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")

        # 10 items total, last 5 should be evaluated
        stats.speed_history = [200, 180, 190, 210, 195,  # old data (ignored)
                                50, 60, 55, 65, 70]        # recent 5 (avg=60 < 100)

        result = connection_manager.is_throttled("test_op", config)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_throttled_default_config(self, connection_manager):
        """Should use default config when config=None."""
        stats = connection_manager.get_stats("test_op")

        # default_config has detection_window=10, speed_threshold_kbps=50
        stats.speed_history = [30] * 10  # avg=30 < 50

        result = connection_manager.is_throttled("test_op", config=None)
        assert result is True


class TestCalculateThrottleDelay:
    """Tests for calculate_throttle_delay method."""

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_not_throttled(self, connection_manager):
        """Should return 0.0 when not throttled."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [150, 140, 160, 155, 145]  # avg > threshold

        delay = await connection_manager.calculate_throttle_delay("test_op", config)
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_throttled_no_failures(self, connection_manager):
        """Should return small delay when throttled but no consecutive failures."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [50, 60, 55, 65, 70]  # avg < threshold
        stats.consecutive_failures = 0

        with patch('src.core.connection.random.uniform', return_value=1.0):
            delay = await connection_manager.calculate_throttle_delay("test_op", config)

        # base_delay = min(30, 0 * 2) = 0 → jittered to 0
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_throttled_with_failures(self, connection_manager):
        """Should increase delay based on consecutive_failures."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [50, 60, 55, 65, 70]  # avg < threshold
        stats.consecutive_failures = 5

        with patch('src.core.connection.random.uniform', return_value=1.0):
            delay = await connection_manager.calculate_throttle_delay("test_op", config)

        # base_delay = min(30, 5 * 2) = 10 → jittered with 1.0 → 10.0
        assert delay == 10.0

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_capped_at_30(self, connection_manager):
        """Should cap base_delay at 30 seconds."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [50, 60, 55, 65, 70]  # avg < threshold
        stats.consecutive_failures = 20  # would give 40 without cap

        with patch('src.core.connection.random.uniform', return_value=1.0):
            delay = await connection_manager.calculate_throttle_delay("test_op", config)

        # base_delay = min(30, 20 * 2) = 30 → jittered with 1.0 → 30.0
        assert delay == 30.0

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_jitter_min(self, connection_manager):
        """Should apply jitter correctly (min range)."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [50, 60, 55, 65, 70]  # avg < threshold
        stats.consecutive_failures = 5

        with patch('src.core.connection.random.uniform', return_value=0.8):
            delay = await connection_manager.calculate_throttle_delay("test_op", config)

        # base_delay = 10.0, jitter = 0.8 → 8.0
        assert delay == 8.0

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_jitter_max(self, connection_manager):
        """Should apply jitter correctly (max range)."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [50, 60, 55, 65, 70]  # avg < threshold
        stats.consecutive_failures = 5

        with patch('src.core.connection.random.uniform', return_value=1.2):
            delay = await connection_manager.calculate_throttle_delay("test_op", config)

        # base_delay = 10.0, jitter = 1.2 → 12.0
        assert delay == 12.0

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_logs_when_throttled(self, connection_manager):
        """Should log info message when applying throttle delay."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [50, 60, 55, 65, 70]  # avg < threshold
        stats.consecutive_failures = 5

        # Use mock to verify logger was called
        with patch('src.core.connection.random.uniform', return_value=1.0):
            with patch('src.core.connection.logger') as mock_logger:
                delay = await connection_manager.calculate_throttle_delay("test_op", config)
                
                assert delay == 10.0
                # Verify logger.info was called with correct message
                mock_logger.info.assert_called_once()
                call_args = mock_logger.info.call_args[0][0]
                assert "Applying throttle delay for test_op" in call_args
                assert "10.0s" in call_args

    @pytest.mark.asyncio
    async def test_calculate_throttle_delay_no_log_when_not_throttled(self, connection_manager):
        """Should not log when delay is 0 (not throttled)."""
        config = ConnectionConfig(detection_window=5, speed_threshold_kbps=100)
        stats = connection_manager.get_stats("test_op")
        stats.speed_history = [150, 140, 160, 155, 145]  # avg > threshold

        # Use mock to verify logger was NOT called
        with patch('src.core.connection.logger') as mock_logger:
            delay = await connection_manager.calculate_throttle_delay("test_op", config)
            
            assert delay == 0.0
            # Verify logger.info was not called
            mock_logger.info.assert_not_called()
