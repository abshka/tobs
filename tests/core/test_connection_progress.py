"""
Tests for connection.py - DownloadProgress.

Batch 3 of Session 7: Progress tracking and calculations.
"""

import time
from unittest.mock import patch

import pytest

from src.core.connection import DownloadProgress


class TestDownloadProgressInitialization:
    """Tests for DownloadProgress initialization."""

    def test_download_progress_default_initialization(self):
        """DownloadProgress should initialize with default values."""
        progress = DownloadProgress()

        assert progress.total_bytes == 0
        assert progress.downloaded_bytes == 0
        assert progress.start_time == 0.0
        assert progress.last_progress_time == 0.0

    def test_download_progress_with_custom_values(self):
        """DownloadProgress should accept custom initialization values."""
        progress = DownloadProgress(
            total_bytes=1000000,
            downloaded_bytes=500000,
            start_time=1000.0,
            last_progress_time=1005.0,
        )

        assert progress.total_bytes == 1000000
        assert progress.downloaded_bytes == 500000
        assert progress.start_time == 1000.0
        assert progress.last_progress_time == 1005.0


class TestProgressPercent:
    """Tests for progress_percent property."""

    def test_progress_percent_calculation(self):
        """progress_percent should calculate percentage correctly."""
        progress = DownloadProgress(
            total_bytes=1000,
            downloaded_bytes=250,
        )

        assert progress.progress_percent == 25.0

    def test_progress_percent_zero_total(self):
        """progress_percent should return 0.0 when total_bytes is zero."""
        progress = DownloadProgress(
            total_bytes=0,
            downloaded_bytes=0,
        )

        assert progress.progress_percent == 0.0

    def test_progress_percent_complete_download(self):
        """progress_percent should return 100.0 for complete download."""
        progress = DownloadProgress(
            total_bytes=5000,
            downloaded_bytes=5000,
        )

        assert progress.progress_percent == 100.0

    def test_progress_percent_partial_download(self):
        """progress_percent should handle partial downloads correctly."""
        progress = DownloadProgress(
            total_bytes=10000,
            downloaded_bytes=7500,
        )

        assert progress.progress_percent == 75.0


class TestCurrentSpeedKbps:
    """Tests for current_speed_kbps property."""

    @patch("time.time", return_value=1010.0)
    def test_current_speed_kbps_calculation(self, mock_time):
        """current_speed_kbps should calculate speed correctly."""
        progress = DownloadProgress(
            downloaded_bytes=10240,  # 10 KB
            start_time=1000.0,  # 10 seconds elapsed
        )

        # Speed = 10240 bytes / 10 seconds / 1024 = 1.0 KB/s
        assert progress.current_speed_kbps == 1.0

    @patch("time.time", return_value=1000.0)
    def test_current_speed_kbps_zero_elapsed_time(self, mock_time):
        """current_speed_kbps should return 0.0 when no time elapsed."""
        progress = DownloadProgress(
            downloaded_bytes=5000,
            start_time=1000.0,  # No time elapsed
        )

        assert progress.current_speed_kbps == 0.0

    @patch("time.time", return_value=1005.0)
    def test_current_speed_kbps_realistic_download(self, mock_time):
        """current_speed_kbps should handle realistic download speeds."""
        progress = DownloadProgress(
            downloaded_bytes=512000,  # 500 KB
            start_time=1000.0,  # 5 seconds elapsed
        )

        # Speed = 512000 / 5 / 1024 = 100 KB/s
        assert progress.current_speed_kbps == 100.0


class TestEtaSeconds:
    """Tests for eta_seconds property."""

    @patch("time.time", return_value=1010.0)
    def test_eta_seconds_calculation(self, mock_time):
        """eta_seconds should calculate remaining time correctly."""
        progress = DownloadProgress(
            total_bytes=20480,  # 20 KB total
            downloaded_bytes=10240,  # 10 KB downloaded
            start_time=1000.0,  # 10 seconds elapsed
        )

        # Speed = 10 KB / 10 sec = 1 KB/s
        # Remaining = (20 - 10) KB = 10 KB
        # ETA = 10 KB / 1 KB/s = 10 seconds
        assert progress.eta_seconds == 10.0

    @patch("time.time", return_value=1000.0)
    def test_eta_seconds_infinite_when_zero_speed(self, mock_time):
        """eta_seconds should return infinity when speed is zero."""
        progress = DownloadProgress(
            total_bytes=10000,
            downloaded_bytes=0,
            start_time=1000.0,  # No time elapsed
        )

        assert progress.eta_seconds == float("inf")

    @patch("time.time", return_value=1005.0)
    def test_eta_seconds_with_partial_download(self, mock_time):
        """eta_seconds should handle partial downloads correctly."""
        progress = DownloadProgress(
            total_bytes=1024000,  # 1000 KB
            downloaded_bytes=512000,  # 500 KB
            start_time=1000.0,  # 5 seconds elapsed
        )

        # Speed = 500 KB / 5 sec = 100 KB/s
        # Remaining = 500 KB
        # ETA = 500 / 100 = 5 seconds
        assert progress.eta_seconds == 5.0

    @patch("time.time", return_value=1010.0)
    def test_eta_seconds_nearly_complete(self, mock_time):
        """eta_seconds should handle downloads that are nearly complete."""
        progress = DownloadProgress(
            total_bytes=10240,
            downloaded_bytes=10000,
            start_time=1000.0,  # 10 seconds elapsed
        )

        # Speed = 10000 / 10 / 1024 ≈ 0.977 KB/s
        # Remaining = 240 bytes ≈ 0.234 KB
        # ETA ≈ 0.234 / 0.977 ≈ 0.24 seconds
        eta = progress.eta_seconds
        assert 0.2 < eta < 0.3
