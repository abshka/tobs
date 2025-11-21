"""
Phase 2 Task 2.2: Timeout configuration tests.

Tests validate that timeout constants are properly defined and have logical values.
"""

import pytest

from src.config import (
    EXPORT_OPERATION_TIMEOUT,
    HEALTH_CHECK_TIMEOUT,
    ITER_MESSAGES_TIMEOUT,
    MEDIA_DOWNLOAD_TIMEOUT,
    QUEUE_OPERATION_TIMEOUT,
)


class TestTimeoutConstants:
    """Test that timeout constants are properly defined and valid."""

    def test_iter_messages_timeout_defined(self):
        """ITER_MESSAGES_TIMEOUT should be 300 seconds."""
        assert ITER_MESSAGES_TIMEOUT == 300

    def test_export_operation_timeout_defined(self):
        """EXPORT_OPERATION_TIMEOUT should be 600 seconds."""
        assert EXPORT_OPERATION_TIMEOUT == 600

    def test_queue_operation_timeout_defined(self):
        """QUEUE_OPERATION_TIMEOUT should be 30 seconds."""
        assert QUEUE_OPERATION_TIMEOUT == 30

    def test_timeouts_are_positive(self):
        """All timeouts should be positive numbers."""
        assert ITER_MESSAGES_TIMEOUT > 0
        assert EXPORT_OPERATION_TIMEOUT > 0
        assert QUEUE_OPERATION_TIMEOUT > 0

    def test_export_timeout_greater_than_iter_timeout(self):
        """Export timeout should be >= iter messages timeout (logical hierarchy)."""
        assert EXPORT_OPERATION_TIMEOUT >= ITER_MESSAGES_TIMEOUT


class TestTimeoutHierarchy:
    """Test logical timeout hierarchy relationships."""

    def test_timeout_hierarchy_order(self):
        """
        Verify timeout hierarchy makes logical sense:
        QUEUE_OPERATION_TIMEOUT < ITER_MESSAGES_TIMEOUT < EXPORT_OPERATION_TIMEOUT
        """
        assert QUEUE_OPERATION_TIMEOUT < ITER_MESSAGES_TIMEOUT
        assert ITER_MESSAGES_TIMEOUT < EXPORT_OPERATION_TIMEOUT

    def test_timeout_constants_exist(self):
        """All expected timeout constants should be defined in config.py."""
        # Verify all constants exist and are not None
        assert ITER_MESSAGES_TIMEOUT is not None
        assert EXPORT_OPERATION_TIMEOUT is not None
        assert QUEUE_OPERATION_TIMEOUT is not None
        assert HEALTH_CHECK_TIMEOUT is not None
        assert MEDIA_DOWNLOAD_TIMEOUT is not None

    def test_timeout_values_are_reasonable(self):
        """Timeout values should be reasonable for their operation type."""
        # Queue timeout should be quick (workers polling)
        assert QUEUE_OPERATION_TIMEOUT <= 60

        # Message iteration can take time (network delays)
        assert 60 <= ITER_MESSAGES_TIMEOUT <= 600

        # Export can take significant time (multiple operations)
        assert 300 <= EXPORT_OPERATION_TIMEOUT <= 3600
