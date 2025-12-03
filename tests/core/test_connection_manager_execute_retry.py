"""
Tests for ConnectionManager.execute_with_retry integration.

Covers:
- Successful execution on first attempt
- Success after retries
- Final failure after max_retries
- Timeout handling
- Throttling integration
- Telegram-specific error handling
- Stats updates
- Different pool types
- Sync vs async operations
"""

import asyncio
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.connection import (
    BackoffStrategy,
    ConnectionConfig,
    ConnectionManager,
    PoolType,
)

# ============================================================================
# Batch 11: execute_with_retry Integration Tests
# ============================================================================


class TestExecuteWithRetrySuccess:
    """Test successful execution scenarios."""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self, connection_manager):
        """Should succeed on first attempt and update stats."""
        # Arrange
        operation = MagicMock(return_value="success_result")
        op_name = "test_operation"

        # Патчим pool.submit для возврата корутины
        with patch.object(
            connection_manager.pools[PoolType.API],
            "submit",
            new_callable=AsyncMock,
        ) as mock_submit:
            mock_submit.return_value = "success_result"

            # Act
            result = await connection_manager.execute_with_retry(
                operation, op_name, pool_type=PoolType.API
            )

            # Assert
            assert result == "success_result"
            mock_submit.assert_called_once()
            stats = connection_manager.get_stats(op_name)
            assert stats.total_attempts == 1
            assert stats.successful_attempts == 1
            assert stats.failed_attempts == 0

    @pytest.mark.asyncio
    async def test_success_after_retries(self, connection_manager):
        """Should succeed after retries and update stats correctly."""
        # Arrange
        operation = MagicMock()
        op_name = "test_retry_operation"
        attempt_count = 0

        async def side_effect_func(*args, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ValueError("Temporary failure")
            return "success_after_retries"

        config = ConnectionConfig(max_attempts=5)

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_submit.side_effect = side_effect_func

            # Act
            result = await connection_manager.execute_with_retry(
                operation, op_name, config=config
            )

            # Assert
            assert result == "success_after_retries"
            assert mock_submit.call_count == 3
            stats = connection_manager.get_stats(op_name)
            assert stats.total_attempts == 3
            assert stats.successful_attempts == 1
            assert stats.failed_attempts == 2


class TestExecuteWithRetryFailure:
    """Test failure scenarios."""

    @pytest.mark.asyncio
    async def test_final_failure_after_max_retries(self, connection_manager):
        """Should fail after max_retries and raise last exception."""
        # Arrange
        operation = MagicMock()
        op_name = "test_fail_operation"
        error = RuntimeError("Persistent failure")

        config = ConnectionConfig(max_attempts=3)

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_submit.side_effect = error

            # Act & Assert
            with pytest.raises(RuntimeError, match="Persistent failure"):
                await connection_manager.execute_with_retry(
                    operation, op_name, config=config
                )

            assert mock_submit.call_count == 3
            stats = connection_manager.get_stats(op_name)
            assert stats.total_attempts == 3
            assert stats.failed_attempts == 3
            assert stats.successful_attempts == 0

    @pytest.mark.asyncio
    async def test_timeout_exception(self, connection_manager):
        """Should handle timeout exceptions correctly."""
        # Arrange
        operation = MagicMock()
        op_name = "test_timeout_operation"

        config = ConnectionConfig(max_attempts=2, base_timeout=1.0)

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_submit.side_effect = asyncio.TimeoutError("Operation timed out")

            # Act & Assert
            with pytest.raises(asyncio.TimeoutError):
                await connection_manager.execute_with_retry(
                    operation, op_name, config=config, timeout_override=1.0
                )

            assert mock_submit.call_count == 2
            stats = connection_manager.get_stats(op_name)
            assert stats.failed_attempts == 2


class TestExecuteWithRetryTimeout:
    """Test timeout calculation integration."""

    @pytest.mark.asyncio
    async def test_file_size_based_timeout(self, connection_manager):
        """Should calculate timeout based on file size."""
        # Arrange
        operation = MagicMock()
        op_name = "test_file_download"
        file_size = 500 * 1024 * 1024  # 500 MB

        with (
            patch.object(
                connection_manager.pools[PoolType.DOWNLOAD],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.wait_for") as mock_wait_for,
        ):
            mock_submit.return_value = "downloaded"
            mock_wait_for.return_value = "downloaded"

            # Act
            await connection_manager.execute_with_retry(
                operation,
                op_name,
                pool_type=PoolType.DOWNLOAD,
                file_size=file_size,
            )

            # Assert
            # Проверяем, что wait_for вызван с вычисленным таймаутом
            assert mock_wait_for.called
            call_args = mock_wait_for.call_args
            timeout_used = call_args[1]["timeout"]
            # Для 500MB должен быть large_file_timeout (1800s) или больше
            assert timeout_used >= 180  # Минимум 3 минуты

    @pytest.mark.asyncio
    async def test_timeout_override(self, connection_manager):
        """Should use timeout_override when provided."""
        # Arrange
        operation = MagicMock()
        op_name = "test_custom_timeout"
        custom_timeout = 42.0

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.wait_for") as mock_wait_for,
        ):
            mock_submit.return_value = "result"
            mock_wait_for.return_value = "result"

            # Act
            await connection_manager.execute_with_retry(
                operation, op_name, timeout_override=custom_timeout
            )

            # Assert
            mock_wait_for.assert_called_once()
            assert mock_wait_for.call_args[1]["timeout"] == custom_timeout


class TestExecuteWithRetryThrottling:
    """Test throttling integration."""

    @pytest.mark.asyncio
    async def test_throttle_delay_applied(self, connection_manager):
        """Should apply throttle delay when throttled."""
        # Arrange
        operation = MagicMock()
        op_name = "test_throttled_operation"

        # Симулируем throttled состояние
        with (
            patch.object(
                connection_manager, "calculate_throttle_delay", new_callable=AsyncMock
            ) as mock_throttle_delay,
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch(
                "src.core.connection.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_throttle_delay.return_value = 5.0
            mock_submit.return_value = "success"

            # Act
            await connection_manager.execute_with_retry(operation, op_name)

            # Assert
            mock_throttle_delay.assert_called_once()
            # Проверяем, что sleep был вызван с throttle delay
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert 5.0 in sleep_calls

    @pytest.mark.asyncio
    async def test_no_throttle_delay_when_not_throttled(self, connection_manager):
        """Should not apply delay when not throttled."""
        # Arrange
        operation = MagicMock()
        op_name = "test_not_throttled"

        with (
            patch.object(
                connection_manager, "calculate_throttle_delay", new_callable=AsyncMock
            ) as mock_throttle_delay,
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch(
                "src.core.connection.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_throttle_delay.return_value = 0.0
            mock_submit.return_value = "success"

            # Act
            await connection_manager.execute_with_retry(operation, op_name)

            # Assert
            mock_throttle_delay.assert_called_once()
            # Sleep не должен быть вызван для throttle delay (только для retries если были)
            assert mock_sleep.call_count == 0  # Нет ошибок = нет retry delays


class TestExecuteWithRetryTelegramErrors:
    """Test Telegram-specific error handling integration."""

    @pytest.mark.asyncio
    async def test_flood_wait_error_handling(self, connection_manager):
        """Should handle FloodWaitError with proper delay."""
        # Arrange
        operation = MagicMock()
        op_name = "telegram_flood_operation"

        # Создаем mock FloodWaitError
        flood_error = MagicMock()
        flood_error.seconds = 30
        flood_error.__class__.__name__ = "FloodWaitError"

        attempt_count = 0

        async def side_effect_func(*args, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                # Имитируем FloodWaitError через isinstance
                raise flood_error
            return "success_after_flood"

        config = ConnectionConfig(max_attempts=3)

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch(
                "src.core.connection.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
            patch.object(
                connection_manager, "handle_telegram_error", new_callable=AsyncMock
            ) as mock_handle_tg,
        ):
            mock_submit.side_effect = side_effect_func
            mock_handle_tg.return_value = 30.0

            # Act
            result = await connection_manager.execute_with_retry(
                operation, op_name, config=config
            )

            # Assert
            assert result == "success_after_flood"
            mock_handle_tg.assert_called_once()
            # Должен был быть sleep на 30 секунд
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert 30.0 in sleep_calls

    @pytest.mark.asyncio
    async def test_telegram_timeout_error_handling(self, connection_manager):
        """Should handle TelegramTimeoutError correctly."""
        # Arrange
        operation = MagicMock()
        op_name = "telegram_timeout_operation"

        timeout_error = Exception("TelegramTimeoutError: Connection timeout")

        config = ConnectionConfig(max_attempts=2)

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_submit.side_effect = [timeout_error, timeout_error]

            # Act & Assert
            with pytest.raises(Exception):
                await connection_manager.execute_with_retry(
                    operation, op_name, config=config
                )

            # Проверяем, что все попытки были выполнены
            assert mock_submit.call_count == 2
            stats = connection_manager.get_stats(op_name)
            assert stats.failed_attempts == 2
            # Timeout count должен увеличиваться при каждой попытке
            assert stats.timeout_count >= 1


class TestExecuteWithRetryPoolTypes:
    """Test execution with different pool types."""

    @pytest.mark.asyncio
    async def test_download_pool_type(self, connection_manager):
        """Should use DOWNLOAD pool when specified."""
        # Arrange
        operation = MagicMock()
        op_name = "download_operation"

        with patch.object(
            connection_manager.pools[PoolType.DOWNLOAD],
            "submit",
            new_callable=AsyncMock,
        ) as mock_submit:
            mock_submit.return_value = "download_result"

            # Act
            result = await connection_manager.execute_with_retry(
                operation, op_name, pool_type=PoolType.DOWNLOAD
            )

            # Assert
            assert result == "download_result"
            mock_submit.assert_called_once_with(operation)

    @pytest.mark.asyncio
    async def test_io_pool_type(self, connection_manager):
        """Should use IO pool when specified."""
        # Arrange
        operation = MagicMock()
        op_name = "io_operation"

        with patch.object(
            connection_manager.pools[PoolType.IO],
            "submit",
            new_callable=AsyncMock,
        ) as mock_submit:
            mock_submit.return_value = "io_result"

            # Act
            result = await connection_manager.execute_with_retry(
                operation, op_name, pool_type=PoolType.IO
            )

            # Assert
            assert result == "io_result"
            mock_submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_processing_pool_type(self, connection_manager):
        """Should use PROCESSING pool when specified."""
        # Arrange
        operation = MagicMock()
        op_name = "processing_operation"

        with patch.object(
            connection_manager.pools[PoolType.PROCESSING],
            "submit",
            new_callable=AsyncMock,
        ) as mock_submit:
            mock_submit.return_value = "processing_result"

            # Act
            result = await connection_manager.execute_with_retry(
                operation, op_name, pool_type=PoolType.PROCESSING
            )

            # Assert
            assert result == "processing_result"
            mock_submit.assert_called_once()


class TestExecuteWithRetryStats:
    """Test stats updates during execution."""

    @pytest.mark.asyncio
    async def test_stats_updated_on_success(self, connection_manager):
        """Should update success stats correctly."""
        # Arrange
        operation = MagicMock()
        op_name = "success_stats_operation"

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.time.time") as mock_time,
        ):
            mock_submit.return_value = "success"
            # Добавляем дополнительное значение для update_success()
            mock_time.side_effect = [
                100.0,
                100.5,
                100.5,
            ]  # start, end, last_success_time

            # Act
            await connection_manager.execute_with_retry(operation, op_name)

            # Assert
            stats = connection_manager.get_stats(op_name)
            assert stats.successful_attempts == 1
            assert stats.failed_attempts == 0
            assert stats.consecutive_successes == 1
            assert stats.consecutive_failures == 0
            # Average response time должен быть ~0.5s
            assert stats.avg_response_time == pytest.approx(0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_stats_updated_on_failure(self, connection_manager):
        """Should update failure stats correctly."""
        # Arrange
        operation = MagicMock()
        op_name = "failure_stats_operation"

        config = ConnectionConfig(max_attempts=2)

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch("src.core.connection.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_submit.side_effect = RuntimeError("Failure")

            # Act & Assert
            with pytest.raises(RuntimeError):
                await connection_manager.execute_with_retry(
                    operation, op_name, config=config
                )

            stats = connection_manager.get_stats(op_name)
            assert stats.successful_attempts == 0
            assert stats.failed_attempts == 2
            assert stats.consecutive_successes == 0
            assert stats.consecutive_failures == 2


class TestExecuteWithRetryOperationArgs:
    """Test passing arguments to operations."""

    @pytest.mark.asyncio
    async def test_args_and_kwargs_passed_to_operation(self, connection_manager):
        """Should pass positional and keyword args to operation."""
        # Arrange
        operation = MagicMock()
        op_name = "args_operation"

        with patch.object(
            connection_manager.pools[PoolType.API],
            "submit",
            new_callable=AsyncMock,
        ) as mock_submit:
            mock_submit.return_value = "result"

            # Act
            # Сигнатура: execute_with_retry(operation, operation_name, pool_type=..., config=...,
            #                               timeout_override=..., file_size=..., *args, **kwargs)
            # Поэтому positional args идут ПОСЛЕ всех named parameters
            await connection_manager.execute_with_retry(
                operation,
                op_name,
                PoolType.API,  # pool_type
                None,  # config
                None,  # timeout_override
                None,  # file_size
                "arg1",  # *args начинаются здесь
                "arg2",
                123,
                key1="value1",  # **kwargs
                key2=42,
            )

            # Assert
            mock_submit.assert_called_once_with(
                operation, "arg1", "arg2", 123, key1="value1", key2=42
            )

    @pytest.mark.asyncio
    async def test_only_kwargs_passed_to_operation(self, connection_manager):
        """Should pass only keyword args to operation."""
        # Arrange
        operation = MagicMock()
        op_name = "kwargs_operation"

        with patch.object(
            connection_manager.pools[PoolType.API],
            "submit",
            new_callable=AsyncMock,
        ) as mock_submit:
            mock_submit.return_value = "result"

            # Act
            await connection_manager.execute_with_retry(
                operation,
                op_name,
                pool_type=PoolType.API,
                param1="value1",
                param2=42,
            )

            # Assert
            mock_submit.assert_called_once_with(operation, param1="value1", param2=42)


class TestExecuteWithRetryBackoffStrategies:
    """Test retry behavior with different backoff strategies."""

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self, connection_manager):
        """Should apply exponential backoff delays."""
        # Arrange
        operation = MagicMock()
        op_name = "exponential_backoff_operation"

        config = ConnectionConfig(
            max_attempts=4, strategy=BackoffStrategy.EXPONENTIAL, base_delay=2.0
        )

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch(
                "src.core.connection.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_submit.side_effect = [
                ValueError("Fail 1"),
                ValueError("Fail 2"),
                ValueError("Fail 3"),
                ValueError("Fail 4"),
            ]

            # Act & Assert
            with pytest.raises(ValueError):
                await connection_manager.execute_with_retry(
                    operation, op_name, config=config
                )

            # Проверяем delays: EXPONENTIAL = base * (2 ** (attempt - 1)) * jitter
            # Attempt 1: 2 * 2^0 = 2, Attempt 2: 2 * 2^1 = 4, Attempt 3: 2 * 2^2 = 8
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert len(sleep_calls) == 3  # 3 retries после первой попытки
            # Проверяем примерный порядок величин (с учётом jitter 0.8-1.2)
            assert 1.6 <= sleep_calls[0] <= 2.4  # 2.0 * (0.8 to 1.2)
            assert 3.2 <= sleep_calls[1] <= 4.8  # 4.0 * (0.8 to 1.2)
            assert 6.4 <= sleep_calls[2] <= 9.6  # 8.0 * (0.8 to 1.2)

    @pytest.mark.asyncio
    async def test_fixed_backoff_delays(self, connection_manager):
        """Should apply fixed backoff delays."""
        # Arrange
        operation = MagicMock()
        op_name = "fixed_backoff_operation"

        config = ConnectionConfig(
            max_attempts=3, strategy=BackoffStrategy.FIXED, base_delay=5.0
        )

        with (
            patch.object(
                connection_manager.pools[PoolType.API],
                "submit",
                new_callable=AsyncMock,
            ) as mock_submit,
            patch(
                "src.core.connection.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_submit.side_effect = [
                ValueError("Fail 1"),
                ValueError("Fail 2"),
                ValueError("Fail 3"),
            ]

            # Act & Assert
            with pytest.raises(ValueError):
                await connection_manager.execute_with_retry(
                    operation, op_name, config=config
                )

            # Проверяем delays: FIXED = base_delay * jitter (0.8-1.2)
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert len(sleep_calls) == 2  # 2 retries
            # Проверяем, что все delays в пределах 5.0 * (0.8 to 1.2)
            assert all(4.0 <= delay <= 6.0 for delay in sleep_calls)
