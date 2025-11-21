"""
Tests for connection.py - Enums and ConnectionConfig.

Batch 1 of Session 7: Foundational types and configuration.
"""

import pytest

from src.core.connection import BackoffStrategy, ConnectionConfig, PoolType


class TestBackoffStrategy:
    """Tests for BackoffStrategy enum."""

    def test_backoff_strategy_has_four_values(self):
        """BackoffStrategy should have exactly 4 strategies."""
        strategies = list(BackoffStrategy)
        assert len(strategies) == 4

    def test_backoff_strategy_fixed_exists(self):
        """FIXED strategy should exist with correct value."""
        assert BackoffStrategy.FIXED.value == "fixed"

    def test_backoff_strategy_linear_exists(self):
        """LINEAR strategy should exist with correct value."""
        assert BackoffStrategy.LINEAR.value == "linear"

    def test_backoff_strategy_exponential_exists(self):
        """EXPONENTIAL strategy should exist with correct value."""
        assert BackoffStrategy.EXPONENTIAL.value == "exponential"

    def test_backoff_strategy_adaptive_exists(self):
        """ADAPTIVE strategy should exist with correct value."""
        assert BackoffStrategy.ADAPTIVE.value == "adaptive"

    def test_backoff_strategy_values_are_unique(self):
        """All strategy values should be unique."""
        values = [s.value for s in BackoffStrategy]
        assert len(values) == len(set(values))


class TestPoolType:
    """Tests for PoolType enum."""

    def test_pool_type_has_five_values(self):
        """PoolType should have exactly 5 types."""
        pool_types = list(PoolType)
        assert len(pool_types) == 5

    def test_pool_type_download_exists(self):
        """DOWNLOAD pool type should exist."""
        assert PoolType.DOWNLOAD.value == "download"

    def test_pool_type_io_exists(self):
        """IO pool type should exist."""
        assert PoolType.IO.value == "io"

    def test_pool_type_processing_exists(self):
        """PROCESSING pool type should exist."""
        assert PoolType.PROCESSING.value == "processing"

    def test_pool_type_ffmpeg_exists(self):
        """FFMPEG pool type should exist."""
        assert PoolType.FFMPEG.value == "ffmpeg"

    def test_pool_type_api_exists(self):
        """API pool type should exist."""
        assert PoolType.API.value == "api"

    def test_pool_type_values_are_unique(self):
        """All pool type values should be unique."""
        values = [p.value for p in PoolType]
        assert len(values) == len(set(values))


class TestConnectionConfig:
    """Tests for ConnectionConfig dataclass."""

    def test_connection_config_default_values(self):
        """ConnectionConfig should initialize with correct defaults."""
        config = ConnectionConfig()

        # Retry settings
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.strategy == BackoffStrategy.EXPONENTIAL
        assert config.jitter is True
        assert config.jitter_range == 0.1
        assert config.backoff_multiplier == 2.0

        # Timeout settings
        assert config.base_timeout == 300.0
        assert config.large_file_timeout == 3600.0
        assert config.huge_file_timeout == 7200.0

        # Throttling settings
        assert config.speed_threshold_kbps == 50.0
        assert config.detection_window == 5
        assert config.cooldown_multiplier == 2.0

        # Concurrency settings
        assert config.max_concurrent == 5
        assert config.auto_scale is True
        assert config.scale_threshold == 0.8

    def test_connection_config_custom_retry_values(self):
        """ConnectionConfig should accept custom retry values."""
        config = ConnectionConfig(
            max_attempts=5,
            base_delay=2.0,
            max_delay=120.0,
            strategy=BackoffStrategy.LINEAR,
        )

        assert config.max_attempts == 5
        assert config.base_delay == 2.0
        assert config.max_delay == 120.0
        assert config.strategy == BackoffStrategy.LINEAR

    def test_connection_config_custom_timeout_values(self):
        """ConnectionConfig should accept custom timeout values."""
        config = ConnectionConfig(
            base_timeout=600.0,
            large_file_timeout=7200.0,
            huge_file_timeout=14400.0,
        )

        assert config.base_timeout == 600.0
        assert config.large_file_timeout == 7200.0
        assert config.huge_file_timeout == 14400.0

    def test_connection_config_custom_throttling_values(self):
        """ConnectionConfig should accept custom throttling values."""
        config = ConnectionConfig(
            speed_threshold_kbps=100.0,
            detection_window=10,
            cooldown_multiplier=3.0,
        )

        assert config.speed_threshold_kbps == 100.0
        assert config.detection_window == 10
        assert config.cooldown_multiplier == 3.0

    def test_connection_config_custom_concurrency_values(self):
        """ConnectionConfig should accept custom concurrency values."""
        config = ConnectionConfig(
            max_concurrent=10,
            auto_scale=False,
            scale_threshold=0.9,
        )

        assert config.max_concurrent == 10
        assert config.auto_scale is False
        assert config.scale_threshold == 0.9

    def test_connection_config_all_parameters_custom(self):
        """ConnectionConfig should accept all parameters at once."""
        config = ConnectionConfig(
            # Retry
            max_attempts=7,
            base_delay=3.0,
            max_delay=180.0,
            strategy=BackoffStrategy.ADAPTIVE,
            jitter=False,
            jitter_range=0.2,
            backoff_multiplier=3.0,
            # Timeout
            base_timeout=900.0,
            large_file_timeout=10800.0,
            huge_file_timeout=21600.0,
            # Throttling
            speed_threshold_kbps=75.0,
            detection_window=7,
            cooldown_multiplier=2.5,
            # Concurrency
            max_concurrent=15,
            auto_scale=True,
            scale_threshold=0.85,
        )

        # Verify all values
        assert config.max_attempts == 7
        assert config.base_delay == 3.0
        assert config.max_delay == 180.0
        assert config.strategy == BackoffStrategy.ADAPTIVE
        assert config.jitter is False
        assert config.jitter_range == 0.2
        assert config.backoff_multiplier == 3.0

        assert config.base_timeout == 900.0
        assert config.large_file_timeout == 10800.0
        assert config.huge_file_timeout == 21600.0

        assert config.speed_threshold_kbps == 75.0
        assert config.detection_window == 7
        assert config.cooldown_multiplier == 2.5

        assert config.max_concurrent == 15
        assert config.auto_scale is True
        assert config.scale_threshold == 0.85
