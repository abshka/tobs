"""
Tests for ConnectionManager Telegram error handling.
Covers: handle_telegram_error

Batch 10: Telegram Error Handling (~12 tests)

Note: We test the error handling logic by creating mock errors that match
the string patterns, since we can't easily mock isinstance() for telethon classes.
"""

from unittest.mock import MagicMock, patch

import pytest

try:
    from telethon.errors import FloodWaitError, RPCError, SlowModeWaitError
    from telethon.errors import TimeoutError as TelegramTimeoutError

    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

    # Create mock classes for testing
    class FloodWaitError(Exception):
        def __init__(self, seconds):
            self.seconds = seconds
            super().__init__(f"Flood wait: {seconds}s")

    class SlowModeWaitError(Exception):
        def __init__(self, seconds):
            self.seconds = seconds
            super().__init__(f"Slow mode wait: {seconds}s")

    class TelegramTimeoutError(Exception):
        pass

    class RPCError(Exception):
        pass


class TestHandleTelegramError:
    """Tests for handle_telegram_error method."""

    @pytest.mark.asyncio
    async def test_handle_flood_wait_error(self, connection_manager):
        """Should return wait time from FloodWaitError.seconds."""
        # Create a mock that will pass isinstance check
        error = MagicMock(spec=FloodWaitError)
        error.seconds = 120

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=1
            )

        assert delay == 120.0
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "FloodWait for test_op" in call_args
        assert "120s" in call_args

    @pytest.mark.asyncio
    async def test_handle_slow_mode_wait_error(self, connection_manager):
        """Should return wait time from SlowModeWaitError.seconds."""
        # Create a mock that will pass isinstance check
        error = MagicMock(spec=SlowModeWaitError)
        error.seconds = 60

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=1
            )

        assert delay == 60.0
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "SlowMode wait for test_op" in call_args
        assert "60s" in call_args

    @pytest.mark.asyncio
    async def test_handle_telegram_timeout_error_first_occurrence(
        self, connection_manager
    ):
        """Should calculate delay and increment timeout_count on first timeout."""
        error = TelegramTimeoutError("Connection timeout")
        stats = connection_manager.get_stats("test_op")

        # Ensure timeout_count starts at 0
        assert stats.timeout_count == 0

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=2
            )

        # base_delay = 10.0 + (2 * 5.0) = 20.0
        # No multiplier since timeout_count was 0 (checked before increment)
        assert delay == 20.0
        assert stats.timeout_count == 1
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_telegram_timeout_error_multiple_occurrences(
        self, connection_manager
    ):
        """Should apply multiplier based on timeout_count."""
        error = TelegramTimeoutError("Connection timeout")
        stats = connection_manager.get_stats("test_op")
        stats.timeout_count = 3

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=1
            )

        # base_delay = 10.0 + (1 * 5.0) = 15.0
        # multiplier = min(3, 5) = 3 (since timeout_count > 1)
        # After increment, timeout_count becomes 4
        # delay = 15.0 * min(4, 5) = 15.0 * 4 = 60.0
        assert delay == 60.0
        assert stats.timeout_count == 4

    @pytest.mark.asyncio
    async def test_handle_telegram_timeout_error_capped_at_300(
        self, connection_manager
    ):
        """Should cap timeout delay at 300 seconds."""
        error = TelegramTimeoutError("Connection timeout")
        stats = connection_manager.get_stats("test_op")
        stats.timeout_count = 10  # Very high count

        delay = await connection_manager.handle_telegram_error(
            error,
            "test_op",
            attempt=20,  # High attempt
        )

        # base_delay = 10.0 + (20 * 5.0) = 110.0
        # After increment, timeout_count becomes 11
        # multiplier = min(11, 5) = 5
        # uncapped = 110.0 * 5 = 550.0
        # Should be capped at 300
        assert delay == 300.0

    @pytest.mark.asyncio
    async def test_handle_timeout_error_string(self, connection_manager):
        """Should handle errors containing 'TimeoutError' in string."""
        error = Exception("TimeoutError: Request timed out")
        stats = connection_manager.get_stats("test_op")

        delay = await connection_manager.handle_telegram_error(
            error, "test_op", attempt=1
        )

        # Should follow same logic as TelegramTimeoutError
        # base_delay = 10.0 + (1 * 5.0) = 15.0
        assert delay == 15.0
        assert stats.timeout_count == 1

    @pytest.mark.asyncio
    async def test_handle_get_file_request_error(self, connection_manager):
        """Should handle GetFileRequest errors with specific delay."""
        error = Exception("GetFileRequest failed: file not found")

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=3
            )

        # delay = 5.0 + (3 * 2.0) = 11.0, capped at 60
        assert delay == 11.0
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "GetFileRequest error for test_op" in call_args

    @pytest.mark.asyncio
    async def test_handle_get_file_request_error_capped(self, connection_manager):
        """Should cap GetFileRequest delay at 60 seconds."""
        error = Exception("GetFileRequest failed")

        delay = await connection_manager.handle_telegram_error(
            error,
            "test_op",
            attempt=50,  # Very high attempt
        )

        # delay = 5.0 + (50 * 2.0) = 105.0, should be capped at 60
        assert delay == 60.0

    @pytest.mark.asyncio
    async def test_handle_rpc_error(self, connection_manager):
        """Should handle RPCError with specific delay formula."""
        # Create a mock that will pass isinstance check
        error = MagicMock(spec=RPCError)

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=4
            )

        # delay = 3.0 + (4 * 1.5) = 9.0, capped at 30
        assert delay == 9.0
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "RPC error for test_op" in call_args

    @pytest.mark.asyncio
    async def test_handle_rpc_error_capped(self, connection_manager):
        """Should cap RPC error delay at 30 seconds."""
        # Create a mock that will pass isinstance check
        error = MagicMock(spec=RPCError)

        delay = await connection_manager.handle_telegram_error(
            error,
            "test_op",
            attempt=30,  # Very high attempt
        )

        # delay = 3.0 + (30 * 1.5) = 48.0, should be capped at 30
        assert delay == 30.0

    @pytest.mark.asyncio
    async def test_handle_unknown_error(self, connection_manager):
        """Should handle unknown errors with fallback delay."""
        error = ValueError("Some random error")

        with patch("src.core.connection.logger") as mock_logger:
            delay = await connection_manager.handle_telegram_error(
                error, "test_op", attempt=5
            )

        # delay = 2.0 + 5 = 7.0, capped at 60
        assert delay == 7.0
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "Unknown error for test_op" in call_args

    @pytest.mark.asyncio
    async def test_handle_unknown_error_capped(self, connection_manager):
        """Should cap unknown error delay at 60 seconds."""
        error = RuntimeError("Unexpected error")

        delay = await connection_manager.handle_telegram_error(
            error,
            "test_op",
            attempt=100,  # Very high attempt
        )

        # delay = 2.0 + 100 = 102.0, should be capped at 60
        assert delay == 60.0
