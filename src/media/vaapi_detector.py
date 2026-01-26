"""
VA-API Auto-Detection Module.

Detects VA-API (Video Acceleration API) hardware capabilities using vainfo command.
Provides detailed information about available encoders, decoders, and driver version.
"""

import os
import subprocess
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class VAAPIStatus(Enum):
    """Status of VA-API detection."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass
class VAAPICapabilities:
    """VA-API hardware capabilities."""

    status: VAAPIStatus
    driver: Optional[str]
    encoders: list[str]
    decoders: list[str]
    device_path: str = "/dev/dri/renderD128"


class VAAPIDetector:
    """Detect VA-API hardware acceleration capabilities."""

    @staticmethod
    def detect(device_path: str = "/dev/dri/renderD128") -> VAAPICapabilities:
        """
        Auto-detect VA-API support on system.

        Args:
            device_path: Path to VA-API device (default: /dev/dri/renderD128)

        Returns:
            VAAPICapabilities with detection results
        """
        # Check if /dev/dri exists
        if not os.path.exists("/dev/dri"):
            logger.info("🚫 /dev/dri not found - VA-API unavailable")
            return VAAPICapabilities(
                status=VAAPIStatus.UNAVAILABLE,
                driver=None,
                encoders=[],
                decoders=[],
                device_path=device_path,
            )

        # Check if device is accessible
        if not os.path.exists(device_path):
            logger.warning(f"⚠️ VA-API device {device_path} not found")
            return VAAPICapabilities(
                status=VAAPIStatus.UNAVAILABLE,
                driver=None,
                encoders=[],
                decoders=[],
                device_path=device_path,
            )

        # Check read/write permissions
        if not (os.access(device_path, os.R_OK) and os.access(device_path, os.W_OK)):
            logger.warning(f"⚠️ VA-API device {device_path} not accessible (check permissions)")
            return VAAPICapabilities(
                status=VAAPIStatus.UNAVAILABLE,
                driver=None,
                encoders=[],
                decoders=[],
                device_path=device_path,
            )

        # Try vainfo command
        try:
            result = subprocess.run(
                ["vainfo", "--display", "drm", "--device", device_path],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                stderr = result.stderr[:200]
                logger.warning(f"⚠️ vainfo failed (rc={result.returncode}): {stderr}")
                return VAAPICapabilities(
                    status=VAAPIStatus.ERROR,
                    driver=None,
                    encoders=[],
                    decoders=[],
                    device_path=device_path,
                )

            # Parse vainfo output
            output = result.stdout
            driver = VAAPIDetector._parse_driver(output)
            encoders = VAAPIDetector._parse_encoders(output)
            decoders = VAAPIDetector._parse_decoders(output)

            if not driver:
                logger.warning("⚠️ Could not parse driver from vainfo output")
                return VAAPICapabilities(
                    status=VAAPIStatus.ERROR,
                    driver=None,
                    encoders=[],
                    decoders=[],
                    device_path=device_path,
                )

            logger.info(
                f"✅ VA-API available: {driver} "
                f"({len(encoders)} encoders, {len(decoders)} decoders)"
            )
            return VAAPICapabilities(
                status=VAAPIStatus.AVAILABLE,
                driver=driver,
                encoders=encoders,
                decoders=decoders,
                device_path=device_path,
            )

        except FileNotFoundError:
            logger.info("🚫 vainfo not installed - CPU fallback (install libva-utils)")
            return VAAPICapabilities(
                status=VAAPIStatus.UNAVAILABLE,
                driver=None,
                encoders=[],
                decoders=[],
                device_path=device_path,
            )
        except subprocess.TimeoutExpired:
            logger.error("❌ vainfo command timeout (5s)")
            return VAAPICapabilities(
                status=VAAPIStatus.ERROR,
                driver=None,
                encoders=[],
                decoders=[],
                device_path=device_path,
            )
        except Exception as e:
            logger.error(f"❌ VA-API detection error: {e}")
            return VAAPICapabilities(
                status=VAAPIStatus.ERROR,
                driver=None,
                encoders=[],
                decoders=[],
                device_path=device_path,
            )

    @staticmethod
    def _parse_driver(output: str) -> Optional[str]:
        """
        Extract driver name from vainfo output.

        Example line: "vainfo: Driver version: Intel i965 driver for Intel(R) Kaby Lake - 2.4.1"
        """
        for line in output.split("\n"):
            if "Driver version:" in line or "Driver name:" in line:
                # Extract text after colon
                driver_text = line.split(":", 2)[-1].strip()
                return driver_text if driver_text else None
        return None

    @staticmethod
    def _parse_encoders(output: str) -> list[str]:
        """
        Extract available encoders from vainfo output.

        Example lines:
            VAProfileH264Main           : VAEntrypointEncSlice
            VAProfileHEVCMain           : VAEntrypointEncSlice
        """
        encoders = []
        for line in output.split("\n"):
            line = line.strip()
            # Check for encoding entrypoint
            if "VAEntrypointEncSlice" in line or "VAEntrypointEncSliceLP" in line:
                if "VAProfileH264" in line:
                    if "h264_vaapi" not in encoders:
                        encoders.append("h264_vaapi")
                elif "VAProfileHEVC" in line or "VAProfileH265" in line:
                    if "hevc_vaapi" not in encoders:
                        encoders.append("hevc_vaapi")
                elif "VAProfileVP8" in line:
                    if "vp8_vaapi" not in encoders:
                        encoders.append("vp8_vaapi")
                elif "VAProfileVP9" in line:
                    if "vp9_vaapi" not in encoders:
                        encoders.append("vp9_vaapi")
        return encoders

    @staticmethod
    def _parse_decoders(output: str) -> list[str]:
        """
        Extract available decoders from vainfo output.

        Example lines:
            VAProfileH264Main           : VAEntrypointVLD
            VAProfileHEVCMain           : VAEntrypointVLD
        """
        decoders = []
        for line in output.split("\n"):
            line = line.strip()
            # Check for decoding entrypoint
            if "VAEntrypointVLD" in line:
                if "VAProfileH264" in line:
                    if "h264" not in decoders:
                        decoders.append("h264")
                elif "VAProfileHEVC" in line or "VAProfileH265" in line:
                    if "hevc" not in decoders:
                        decoders.append("hevc")
                elif "VAProfileVP8" in line:
                    if "vp8" not in decoders:
                        decoders.append("vp8")
                elif "VAProfileVP9" in line:
                    if "vp9" not in decoders:
                        decoders.append("vp9")
        return decoders


# Global singleton cache
_vaapi_caps: Optional[VAAPICapabilities] = None


def get_vaapi_capabilities(device_path: str = "/dev/dri/renderD128") -> VAAPICapabilities:
    """
    Get cached VA-API capabilities (runs detection once per process).

    Args:
        device_path: Path to VA-API device

    Returns:
        VAAPICapabilities with detection results
    """
    global _vaapi_caps
    if _vaapi_caps is None:
        _vaapi_caps = VAAPIDetector.detect(device_path)
    return _vaapi_caps
