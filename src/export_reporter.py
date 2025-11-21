"""
Система мониторинга и анализа экспорта.
Собирает метрики, создает отчеты и анализирует производительность экспорта.
"""

import json
import platform
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import psutil

from src.core.performance import PerformanceMonitor
from src.utils import logger


@dataclass
class ExportMetrics:
    """Метрики экспорта."""

    # Временные метки
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_seconds: float = 0.0

    # Сообщения
    total_messages: int = 0
    processed_messages: int = 0
    failed_messages: int = 0
    skipped_messages: int = 0

    # Медиа
    total_media_files: int = 0
    downloaded_media_files: int = 0
    processed_media_files: int = 0
    failed_media_files: int = 0
    total_media_size_bytes: int = 0

    # Производительность
    messages_per_second: float = 0.0
    media_per_second: float = 0.0
    bytes_per_second: float = 0.0
    peak_memory_mb: float = 0.0
    avg_cpu_percent: float = 0.0

    # Ошибки
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)

    # Настройки экспорта
    export_settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemInfo:
    """Информация о системе."""

    platform: str = field(default_factory=lambda: platform.platform())
    python_version: str = field(default_factory=lambda: platform.python_version())
    cpu_count: int = field(default_factory=lambda: psutil.cpu_count() or 0)
    memory_total_gb: float = field(
        default_factory=lambda: psutil.virtual_memory().total / (1024**3)
    )
    disk_free_gb: float = 0.0

    def __post_init__(self):
        if self.disk_free_gb == 0.0:
            try:
                self.disk_free_gb = psutil.disk_usage(".").free / (1024**3)
            except Exception:
                self.disk_free_gb = 0.0


@dataclass
class EntityReport:
    """Отчет по сущности."""

    entity_id: str
    entity_name: str
    entity_type: str
    metrics: ExportMetrics
    system_info: SystemInfo

    # Версия отчета
    report_version: str = "1.0"

    # Временная метка создания отчета
    created_at: float = field(default_factory=time.time)


class ExportReporter:
    """Монитор экспорта для сущности."""

    def __init__(
        self,
        entity_id: Union[str, int],
        monitoring_path: Path,
        performance_monitor: PerformanceMonitor,
    ):
        self.entity_id = str(entity_id)
        self.monitoring_path = monitoring_path
        self.monitoring_file = monitoring_path / f"monitoring_{self.entity_id}.json"
        self.metrics = ExportMetrics()
        self.performance_monitor = performance_monitor  # Use the core monitor
        self.entity_name = ""
        self.entity_type = "unknown"
        self.export_started = False
        self.export_finished = False

    def start_export(
        self, entity_name: str, entity_type: str, export_settings: Dict[str, Any]
    ):
        """Начать мониторинг экспорта."""
        self.entity_name = entity_name
        self.entity_type = entity_type
        self.metrics.export_settings = export_settings.copy()
        self.metrics.start_time = time.time()
        self.export_started = True

        logger.info(f"Started monitoring export for {entity_name} ({entity_type})")

    def finish_export(self):
        """Завершить мониторинг экспорта."""
        if not self.export_started:
            logger.warning("Cannot finish export monitoring - not started")
            return

        self.metrics.end_time = time.time()
        self.metrics.duration_seconds = self.metrics.end_time - self.metrics.start_time

        # Финальный сбор ресурсов
        self.performance_monitor.sample_resources()

        # Вычисляем производительность
        if self.metrics.duration_seconds > 0:
            self.metrics.messages_per_second = (
                self.metrics.processed_messages / self.metrics.duration_seconds
            )
            self.metrics.media_per_second = (
                self.metrics.downloaded_media_files / self.metrics.duration_seconds
            )
            self.metrics.bytes_per_second = (
                self.metrics.total_media_size_bytes / self.metrics.duration_seconds
            )

        self.metrics.peak_memory_mb = self.performance_monitor.get_peak_memory()
        self.metrics.avg_cpu_percent = self.performance_monitor.get_avg_cpu()

        self.export_finished = True

        logger.info(
            f"Finished monitoring export for {self.entity_name}: "
            f"{self.metrics.processed_messages} messages in {self.metrics.duration_seconds:.1f}s"
        )

    def record_message_processed(self, message_id: int, has_media: bool = False):
        """Записать обработанное сообщение."""
        self.metrics.processed_messages += 1

        if has_media:
            self.metrics.total_media_files += 1

        # Периодически собираем ресурсы
        if (
            time.time() - self.performance_monitor.last_sample_time > 10
        ):  # каждые 10 секунд
            self.performance_monitor.sample_resources()

    def record_message_failed(
        self, message_id: int, error: str, error_type: str = "general"
    ):
        """Записать неудачное сообщение."""
        self.metrics.failed_messages += 1

        error_record = {
            "message_id": message_id,
            "error": error,
            "error_type": error_type,
            "timestamp": time.time(),
        }
        self.metrics.errors.append(error_record)

    def record_message_skipped(self, message_id: int, reason: str):
        """Записать пропущенное сообщение."""
        self.metrics.skipped_messages += 1

    def record_media_downloaded(self, message_id: int, file_size: int, file_path: str):
        """Записать загруженное медиа."""
        self.metrics.downloaded_media_files += 1
        self.metrics.total_media_size_bytes += file_size

    def record_media_processed(self, message_id: int, processing_type: str):
        """Записать обработанное медиа."""
        self.metrics.processed_media_files += 1

    def record_media_failed(
        self, message_id: int, error: str, file_path: Optional[str] = None
    ):
        """Записать неудачное медиа."""
        self.metrics.failed_media_files += 1

        error_record = {
            "message_id": message_id,
            "error": error,
            "error_type": "media",
            "file_path": file_path,
            "timestamp": time.time(),
        }
        self.metrics.errors.append(error_record)

    def record_warning(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Записать предупреждение."""
        warning_record = {
            "message": message,
            "context": context or {},
            "timestamp": time.time(),
        }
        self.metrics.warnings.append(warning_record)

    def set_total_messages(self, total: int):
        """Установить общее количество сообщений."""
        self.metrics.total_messages = total

    def get_progress(self) -> Dict[str, Any]:
        """Получить текущий прогресс."""
        total = self.metrics.total_messages
        processed = self.metrics.processed_messages
        failed = self.metrics.failed_messages

        progress_percent = 0.0
        if total > 0:
            progress_percent = (processed + failed) / total * 100

        eta_seconds = None
        if processed > 0 and total > processed:
            elapsed = time.time() - self.metrics.start_time
            rate = processed / elapsed
            remaining = total - processed
            eta_seconds = remaining / rate if rate > 0 else None

        return {
            "total_messages": total,
            "processed_messages": processed,
            "failed_messages": failed,
            "skipped_messages": self.metrics.skipped_messages,
            "progress_percent": progress_percent,
            "eta_seconds": eta_seconds,
            "messages_per_second": self.metrics.messages_per_second,
            "media_files": {
                "total": self.metrics.total_media_files,
                "downloaded": self.metrics.downloaded_media_files,
                "processed": self.metrics.processed_media_files,
                "failed": self.metrics.failed_media_files,
            },
            "current_memory_mb": self.performance_monitor.memory_samples[-1]
            if self.performance_monitor.memory_samples
            else 0,
            "peak_memory_mb": self.performance_monitor.get_peak_memory(),
            "avg_cpu_percent": self.performance_monitor.get_avg_cpu(),
        }

    def save_metrics(self):
        """Сохранить текущие метрики в единый файл мониторинга."""
        try:
            # Создаем папку мониторинга если её нет
            self.monitoring_path.mkdir(parents=True, exist_ok=True)

            # Генерируем полный отчет с системной информацией
            system_info = SystemInfo()

            monitoring_data = {
                "entity_id": self.entity_id,
                "entity_name": self.entity_name,
                "entity_type": self.entity_type,
                "metrics": asdict(self.metrics),
                "progress": self.get_progress(),
                "system_info": asdict(system_info),
                "export_finished": self.export_finished,
                "report_version": "1.0",
                "timestamp": time.time(),
            }

            with open(self.monitoring_file, "w", encoding="utf-8") as f:
                json.dump(monitoring_data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Failed to save monitoring data for {self.entity_id}: {e}")

    def generate_report(self) -> EntityReport:
        """Сгенерировать финальный отчет."""
        system_info = SystemInfo()

        report = EntityReport(
            entity_id=self.entity_id,
            entity_name=self.entity_name,
            entity_type=self.entity_type,
            metrics=self.metrics,
            system_info=system_info,
        )

        return report

    def save_report(self) -> bool:
        """Сохранить финальный отчет (использует тот же файл что и save_metrics, но помечает как завершенный)."""
        try:
            # Просто вызываем save_metrics, который теперь сохраняет всё в один файл
            self.save_metrics()
            logger.info(
                f"Saved final monitoring report for {self.entity_name}: {self.monitoring_file}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to save final report for {self.entity_id}: {e}")
            return False

    def load_previous_report(self) -> Optional[EntityReport]:
        """Загрузить предыдущий отчет из файла мониторинга."""
        if not self.monitoring_file.exists():
            return None

        try:
            with open(self.monitoring_file, "r", encoding="utf-8") as f:
                report_data = json.load(f)

            # Восстанавливаем объекты из словарей
            metrics_data = report_data["metrics"]
            system_data = report_data["system_info"]

            metrics = ExportMetrics(**metrics_data)
            system_info = SystemInfo(**system_data)

            report = EntityReport(
                entity_id=report_data["entity_id"],
                entity_name=report_data["entity_name"],
                entity_type=report_data["entity_type"],
                metrics=metrics,
                system_info=system_info,
                report_version=report_data.get("report_version", "1.0"),
                created_at=report_data.get("created_at", time.time()),
            )

            return report

        except Exception as e:
            logger.error(f"Failed to load previous report for {self.entity_id}: {e}")
            return None

    def get_comparison_with_previous(self) -> Optional[Dict[str, Any]]:
        """Сравнить с предыдущим экспортом."""
        previous = self.load_previous_report()
        if not previous or not self.export_finished:
            return None

        current = self.metrics
        prev = previous.metrics

        comparison = {
            "messages": {
                "current": current.processed_messages,
                "previous": prev.processed_messages,
                "difference": current.processed_messages - prev.processed_messages,
            },
            "duration": {
                "current": current.duration_seconds,
                "previous": prev.duration_seconds,
                "difference": current.duration_seconds - prev.duration_seconds,
                "improvement_percent": (
                    (prev.duration_seconds - current.duration_seconds)
                    / prev.duration_seconds
                    * 100
                )
                if prev.duration_seconds > 0
                else 0,
            },
            "performance": {
                "current_msg_per_sec": current.messages_per_second,
                "previous_msg_per_sec": prev.messages_per_second,
                "improvement_percent": (
                    (current.messages_per_second - prev.messages_per_second)
                    / prev.messages_per_second
                    * 100
                )
                if prev.messages_per_second > 0
                else 0,
            },
            "memory": {
                "current_peak_mb": current.peak_memory_mb,
                "previous_peak_mb": prev.peak_memory_mb,
                "difference_mb": current.peak_memory_mb - prev.peak_memory_mb,
            },
            "errors": {
                "current": current.failed_messages,
                "previous": prev.failed_messages,
                "difference": current.failed_messages - prev.failed_messages,
            },
        }

        return comparison

        # The core performance monitor is already running, so we just use it.
        pass


class ExportReporterManager:
    """Менеджер отчетов для всех экспортов."""

    def __init__(
        self, base_monitoring_path: Path, performance_monitor: PerformanceMonitor
    ):
        self.base_monitoring_path = base_monitoring_path
        self.reporters: Dict[str, ExportReporter] = {}
        self.performance_monitor = performance_monitor

    def get_reporter(
        self, entity_id: Union[str, int], monitoring_path: Optional[Path] = None
    ) -> ExportReporter:
        """Получить отчет для сущности."""
        entity_key = str(entity_id)
        if entity_key not in self.reporters:
            path = monitoring_path or self.base_monitoring_path
            self.reporters[entity_key] = ExportReporter(
                entity_id, path, self.performance_monitor
            )
        return self.reporters[entity_key]

    def save_all_reports(self) -> int:
        """Сохранить все отчеты."""
        saved_count = 0
        for reporter in self.reporters.values():
            if reporter.export_finished and reporter.save_report():
                saved_count += 1
        logger.info(f"Saved {saved_count}/{len(self.reporters)} export reports")
        return saved_count

    def get_global_summary(self) -> Dict[str, Any]:
        """Получить глобальную сводку всех экспортов."""
        total_entities = len(self.reporters)
        total_messages = 0
        total_processed = 0
        total_failed = 0
        total_media = 0
        total_duration = 0.0
        entity_summaries = []
        # ... (rest of the method uses self.reporters)
        for reporter in self.reporters.values():
            if reporter.export_finished:
                metrics = reporter.metrics
                total_messages += metrics.total_messages
                total_processed += metrics.processed_messages
                total_failed += metrics.failed_messages
                total_media += metrics.downloaded_media_files
                total_duration += metrics.duration_seconds

                entity_summaries.append(
                    {
                        "entity_id": reporter.entity_id,
                        "entity_name": reporter.entity_name,
                        "entity_type": reporter.entity_type,
                        "processed_messages": metrics.processed_messages,
                        "failed_messages": metrics.failed_messages,
                        "duration_seconds": metrics.duration_seconds,
                        "messages_per_second": metrics.messages_per_second,
                        "media_files": metrics.downloaded_media_files,
                        "peak_memory_mb": metrics.peak_memory_mb,
                    }
                )

        avg_performance = 0.0
        if total_duration > 0:
            avg_performance = total_processed / total_duration

        success_rate = 0.0
        total_attempts = total_processed + total_failed
        if total_attempts > 0:
            success_rate = total_processed / total_attempts

        return {
            "total_entities": total_entities,
            "total_messages": total_messages,
            "total_processed": total_processed,
            "total_failed": total_failed,
            "total_media_files": total_media,
            "total_duration_seconds": total_duration,
            "avg_messages_per_second": avg_performance,
            "overall_success_rate": success_rate,
            "entities": entity_summaries,
            "generated_at": time.time(),
        }
