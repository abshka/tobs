"""
Data models for media processing.

Contains all dataclasses and type definitions used across media modules.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


@dataclass(slots=True)
class MediaMetadata:
    """Метаданные медиа файла."""

    file_size: int
    mime_type: str
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    bitrate: Optional[int] = None
    codec: Optional[str] = None
    fps: Optional[float] = None
    channels: Optional[int] = None
    sample_rate: Optional[int] = None
    checksum: Optional[str] = None


@dataclass(slots=True)
class ProcessingSettings:
    """Настройки обработки медиа."""

    max_video_resolution: Tuple[int, int] = (1920, 1080)
    max_video_bitrate: int = 2000  # kbps
    max_audio_bitrate: int = 128  # kbps
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    image_quality: int = 85
    image_max_size: Tuple[int, int] = (2048, 2048)
    enable_hardware_acceleration: bool = True
    prefer_vp9: bool = False
    aggressive_compression: bool = False


@dataclass(slots=True)
class ProcessingTask:
    """Задача обработки медиа."""

    input_path: Path
    output_path: Path
    media_type: str
    priority: int = 5
    processing_settings: Optional[ProcessingSettings] = None
    metadata: Optional[MediaMetadata] = None
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    max_attempts: int = 3
