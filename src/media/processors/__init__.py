"""
Media processors package.

Contains specialized processors for different media types and audio transcription.

Version: 5.0.0 - Simplified standalone implementation
"""

from .audio import AudioProcessor
from .base import BaseProcessor
from .image import ImageProcessor
from .transcription import TranscriptionResult, WhisperTranscriber
from .video import VideoProcessor

__all__ = [
    # Media Processors
    "BaseProcessor",
    "VideoProcessor",
    "AudioProcessor",
    "ImageProcessor",
    # Transcription
    "WhisperTranscriber",
    "TranscriptionResult",
]
