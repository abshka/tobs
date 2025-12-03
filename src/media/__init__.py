"""
Media processing module.

Provides high-performance media download, processing, and validation
for video, audio, and image files from Telegram messages.

Main components:
- MediaProcessor: Main orchestrator for media operations
- MediaDownloader: Download management with resume support
- MediaDownloadQueue: Background async download queue
- Hardware acceleration detection and configuration
- Specialized processors for video/audio/image files
- Metadata extraction and file validation
"""

from .download_queue import DownloadStatus, DownloadTask, MediaDownloadQueue, QueueStats
from .downloader import MediaDownloader
from .hardware import HardwareAccelerationDetector
from .manager import MediaProcessor
from .models import MediaMetadata, ProcessingSettings, ProcessingTask

__all__ = [
    "MediaProcessor",
    "MediaDownloader",
    "MediaDownloadQueue",
    "DownloadTask",
    "DownloadStatus",
    "QueueStats",
    "HardwareAccelerationDetector",
    "MediaMetadata",
    "ProcessingSettings",
    "ProcessingTask",
]

