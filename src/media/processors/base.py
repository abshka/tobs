"""
Base processor class for media processing.

Defines the abstract interface that all media processors must implement.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..models import ProcessingSettings, ProcessingTask


class BaseProcessor(ABC):
    """Базовый класс для всех процессоров медиа."""

    def __init__(
        self, io_executor, cpu_executor, settings: Optional[ProcessingSettings] = None
    ):
        self.io_executor = io_executor
        self.cpu_executor = cpu_executor
        self.settings = settings or ProcessingSettings()

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
