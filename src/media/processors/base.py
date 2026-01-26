"""
Base processor class for media processing.

Defines the abstract interface that all media processors must implement.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from ..models import ProcessingSettings, ProcessingTask


class BaseProcessor(ABC):
    """Базовый класс для всех процессоров медиа."""

    def __init__(
        self, 
        thread_pool: Any,  # UnifiedThreadPool instance
        settings: Optional[ProcessingSettings] = None
    ):
        """
        Initialize base processor.
        
        Args:
            thread_pool: Unified thread pool for CPU-bound operations
            settings: Processing settings
        """
        self.thread_pool = thread_pool
        self.settings = settings or ProcessingSettings()
        
        # Legacy compatibility - deprecated, will be removed
        self.io_executor = None
        self.cpu_executor = None

    @abstractmethod
    async def process(self, task: ProcessingTask, worker_name: str) -> bool:
        """
        Обработка медиафайла.

        Args:
            task: Задача обработки
            worker_name: Имя воркера для логирования

        Returns:
            True если обработка успешна, False иначе
        """
        pass

    @abstractmethod
    def needs_processing(self, file_path: Path, settings: ProcessingSettings) -> bool:
        """
        Определяет, нужна ли обработка файла.

        Args:
            file_path: Путь к файлу
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        pass
