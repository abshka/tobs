"""
Media processing module.

Provides high-performance media download, processing, and validation
for video, audio, and image files from Telegram messages.

Main components:
- MediaProcessor: Main orchestrator for media operations
- MediaDownloader: Download management with resume support
- Hardware acceleration detection and configuration
- Specialized processors for video/audio/image files
- Metadata extraction and file validation
"""

from .downloader import MediaDownloader
from .hardware import HardwareAccelerationDetector
from .manager import MediaProcessor
from .models import MediaMetadata, ProcessingSettings, ProcessingTask

__all__ = [
    "MediaProcessor",
    "MediaDownloader",
    "HardwareAccelerationDetector",
    "MediaMetadata",
    "ProcessingSettings",
    "ProcessingTask",
]
