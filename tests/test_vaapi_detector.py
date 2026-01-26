"""
Unit tests for VA-API Auto-Detection (TIER C-1).

Tests for vaapi_detector module covering all detection scenarios:
- Available hardware with working drivers
- Unavailable hardware (/dev/dri missing, device inaccessible)
- vainfo command not installed
- Error scenarios (timeout, parse failures)
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.media.vaapi_detector import (
    VAAPICapabilities,
    VAAPIDetector,
    VAAPIStatus,
    get_vaapi_capabilities,
)


class TestVAAPIDetector:
    """Test suite for VAAPIDetector class."""

    def test_detect_vaapi_available(self):
        """Test VA-API detection when hardware is available."""
        mock_output = """
vainfo: VA-API version: 1.14.0
vainfo: Driver version: Intel i965 driver for Intel(R) Kaby Lake - 2.4.1
VAProfileH264Main           : VAEntrypointVLD
VAProfileH264Main           : VAEntrypointEncSlice
VAProfileHEVCMain           : VAEntrypointVLD
VAProfileHEVCMain           : VAEntrypointEncSlice
        """

        with patch("os.path.exists", return_value=True), patch(
            "os.access", return_value=True
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.AVAILABLE
            assert caps.driver is not None
            assert "Intel i965" in caps.driver
            assert "h264_vaapi" in caps.encoders
            assert "hevc_vaapi" in caps.encoders
            assert "h264" in caps.decoders
            assert "hevc" in caps.decoders

    def test_detect_vaapi_unavailable_no_dri(self):
        """Test VA-API detection when /dev/dri doesn't exist."""
        with patch("os.path.exists", return_value=False):
            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.UNAVAILABLE
            assert caps.driver is None
            assert len(caps.encoders) == 0
            assert len(caps.decoders) == 0

    def test_detect_vaapi_device_not_accessible(self):
        """Test VA-API detection when device exists but is not accessible."""
        with patch("os.path.exists") as mock_exists, patch(
            "os.access", return_value=False
        ):
            # /dev/dri exists but device is not accessible
            mock_exists.side_effect = lambda path: path == "/dev/dri"

            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.UNAVAILABLE
            assert caps.driver is None

    def test_detect_vaapi_vainfo_not_installed(self):
        """Test fallback when vainfo command not found."""
        with patch("os.path.exists", return_value=True), patch(
            "os.access", return_value=True
        ), patch("subprocess.run", side_effect=FileNotFoundError):
            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.UNAVAILABLE
            assert caps.driver is None
            assert len(caps.encoders) == 0

    def test_detect_vaapi_command_failure(self):
        """Test when vainfo command fails (non-zero exit code)."""
        with patch("os.path.exists", return_value=True), patch(
            "os.access", return_value=True
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Failed to initialize"
            )

            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.ERROR
            assert caps.driver is None

    def test_detect_vaapi_timeout(self):
        """Test when vainfo command times out."""
        with patch("os.path.exists", return_value=True), patch(
            "os.access", return_value=True
        ), patch("subprocess.run", side_effect=subprocess.TimeoutExpired("vainfo", 5)):
            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.ERROR

    def test_detect_vaapi_no_driver_in_output(self):
        """Test when vainfo output doesn't contain driver information."""
        mock_output = """
VAProfileH264Main           : VAEntrypointEncSlice
        """

        with patch("os.path.exists", return_value=True), patch(
            "os.access", return_value=True
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)

            caps = VAAPIDetector.detect()

            assert caps.status == VAAPIStatus.ERROR
            assert caps.driver is None

    def test_parse_driver_various_formats(self):
        """Test driver parsing from various vainfo output formats."""
        # Format 1: "Driver version:"
        output1 = "vainfo: Driver version: Intel i965 driver - 2.4.1"
        driver1 = VAAPIDetector._parse_driver(output1)
        assert driver1 is not None
        assert "Intel i965" in driver1

        # Format 2: "Driver name:"
        output2 = "vainfo: Driver name: iHD"
        driver2 = VAAPIDetector._parse_driver(output2)
        assert driver2 == "iHD"

    def test_parse_encoders_various_profiles(self):
        """Test encoder parsing for different video profiles."""
        output = """
VAProfileH264Main           : VAEntrypointEncSlice
VAProfileHEVCMain           : VAEntrypointEncSliceLP
VAProfileVP8Version0_3      : VAEntrypointEncSlice
VAProfileVP9Profile0        : VAEntrypointEncSlice
        """

        encoders = VAAPIDetector._parse_encoders(output)

        assert "h264_vaapi" in encoders
        assert "hevc_vaapi" in encoders
        assert "vp8_vaapi" in encoders
        assert "vp9_vaapi" in encoders

    def test_parse_decoders_various_profiles(self):
        """Test decoder parsing for different video profiles."""
        output = """
VAProfileH264Main           : VAEntrypointVLD
VAProfileHEVCMain           : VAEntrypointVLD
VAProfileVP8Version0_3      : VAEntrypointVLD
VAProfileVP9Profile0        : VAEntrypointVLD
        """

        decoders = VAAPIDetector._parse_decoders(output)

        assert "h264" in decoders
        assert "hevc" in decoders
        assert "vp8" in decoders
        assert "vp9" in decoders

    def test_get_vaapi_capabilities_singleton(self):
        """Test that get_vaapi_capabilities returns cached result."""
        # Reset global cache
        import src.media.vaapi_detector as vaapi_module

        vaapi_module._vaapi_caps = None

        with patch("os.path.exists", return_value=False):
            caps1 = get_vaapi_capabilities()
            caps2 = get_vaapi_capabilities()

            # Should return same instance (cached)
            assert caps1 is caps2
            assert caps1.status == VAAPIStatus.UNAVAILABLE

    def test_custom_device_path(self):
        """Test detection with custom device path."""
        custom_device = "/dev/dri/renderD129"

        with patch("os.path.exists") as mock_exists, patch("os.access", return_value=True), patch(
            "subprocess.run"
        ) as mock_run:
            mock_exists.side_effect = lambda path: path in [
                "/dev/dri",
                custom_device,
            ]
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Driver version: test\nVAProfileH264Main : VAEntrypointEncSlice",
            )

            caps = VAAPIDetector.detect(device_path=custom_device)

            assert caps.device_path == custom_device
            assert caps.status == VAAPIStatus.AVAILABLE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
