"""
TOBS - Telegram to Obsidian Exporter

Основные модули для экспорта контента из Telegram в формат Obsidian.
"""

__version__ = "1.0.0"
__author__ = "TOBS Team"
__description__ = "Telegram to Obsidian content exporter"

# Основные экспортируемые классы и функции
from .cache_manager import CacheManager
from .config import Config, ExportTarget
from .exceptions import ExporterError, TelegramConnectionError
from .media_processor import MediaProcessor
from .note_generator import NoteGenerator
from .telegram_client import TelegramManager

__all__ = [
    "Config",
    "ExportTarget",
    "TelegramManager",
    "MediaProcessor",
    "NoteGenerator",
    "CacheManager",
    "ExporterError",
    "TelegramConnectionError"
]
