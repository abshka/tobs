"""
Система мониторинга производительности и управления ресурсами.
"""

import asyncio
import inspect
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import aiofiles
import psutil

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Уровни алертов."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ResourceState(Enum):
    """Состояния ресурсов."""

    OPTIMAL = "optimal"
    STRESSED = "stressed"
    OVERLOADED = "overloaded"
    CRITICAL = "critical"


class AdaptationStrategy(Enum):
    """Стратегии адаптации."""

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass
class SystemMetrics:
    """Системные метрики."""

    timestamp: float
    cpu_percent: float
    memory_percent: float
    disk_usage_percent: float
    network_sent_mb: float
    network_recv_mb: float
    disk_read_mb: float
    disk_write_mb: float
    process_memory_mb: float
    process_cpu_percent: float
    active_threads: int
    open_files: int


@dataclass
class PerformanceAlert:
    """Алерт производительности."""

    id: str
    level: AlertLevel
    message: str
    timestamp: float
    metric_name: str
    current_value: float
    threshold: float
    resolved: bool = False
    resolved_at: Optional[float] = None


@dataclass
class PerformanceProfile:
    """Профиль производительности."""

    name: str
    description: str
    max_concurrent_downloads: int
    max_concurrent_processing: int
    chunk_size_kb: int
    cache_size_mb: int
    compression_enabled: bool
    auto_scale: bool
    memory_limit_mb: Optional[int] = None
    cpu_limit_percent: Optional[float] = None


@dataclass
class ComponentStats:
    """Статистика компонента."""

    name: str
    calls_total: int = 0
    calls_successful: int = 0
    calls_failed: int = 0
    avg_duration: float = 0.0
    max_duration: float = 0.0
    min_duration: float = float("inf")
    total_duration: float = 0.0
    last_call_time: float = 0.0

    @property
    def success_rate(self) -> float:
        """Коэффициент успешности."""
        return (
            (self.calls_successful / self.calls_total) if self.calls_total > 0 else 1.0
        )

    def record_call(self, duration: float, success: bool):
        """Запись вызова."""
        self.calls_total += 1
        self.total_duration += duration
        self.avg_duration = self.total_duration / self.calls_total
        self.max_duration = max(self.max_duration, duration)
        self.min_duration = min(self.min_duration, duration)
        self.last_call_time = time.time()

        if success:
            self.calls_successful += 1
        else:
            self.calls_failed += 1


class PerformanceMonitor:
    """Система мониторинга производительности."""

    def __init__(
        self,
        metrics_history_size: int = 1000,
        alert_history_size: int = 100,
        monitoring_interval: float = 5.0,
        data_path: Optional[Path] = None,
        performance_profile: str = "balanced",
    ):
        self.metrics_history_size = metrics_history_size
        self.alert_history_size = alert_history_size
        self.monitoring_interval = monitoring_interval
        # Используем временную директорию для глобального мониторинга
        if data_path is None:
            import tempfile

            temp_dir = Path(tempfile.gettempdir()) / "tobs_monitoring"
            temp_dir.mkdir(exist_ok=True)
            self.data_path = temp_dir
        else:
            self.data_path = data_path
            self.data_path.mkdir(exist_ok=True)

        # История метрик
        self.metrics_history: deque[SystemMetrics] = deque(maxlen=metrics_history_size)

        # Алерты
        self.alerts: Dict[str, PerformanceAlert] = {}
        self.alert_history: deque[PerformanceAlert] = deque(maxlen=alert_history_size)

        # Статистика компонентов
        self.component_stats: Dict[str, ComponentStats] = {}

        # Профилирование функций
        self.function_stats: Dict[str, ComponentStats] = {}

        # Конфигурация алертов
        self.alert_thresholds: Dict[str, Dict[str, float]] = {
            "cpu_percent": {"warning": 70.0, "critical": 90.0},
            "memory_percent": {"warning": 75.0, "critical": 90.0},
            "disk_usage_percent": {"warning": 80.0, "critical": 95.0},
            "process_memory_mb": {"warning": 1000.0, "critical": 2000.0},
            "open_files": {"warning": 500, "critical": 800},
        }

        # Состояние
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._last_network_stats = None
        self._last_disk_stats = None

        # Коллбеки (поддерживаем как sync, так и async)
        self.metric_callbacks: List[Union[Callable[[SystemMetrics], None], Callable[[SystemMetrics], Awaitable[None]]]] = []
        self.alert_callbacks: List[Union[Callable[[PerformanceAlert], None], Callable[[PerformanceAlert], Awaitable[None]]]] = []

        # Профили производительности
        self.profiles = {
            "conservative": PerformanceProfile(
                name="conservative",
                description="Консервативные настройки для стабильности",
                max_concurrent_downloads=2,
                max_concurrent_processing=2,
                chunk_size_kb=512,
                cache_size_mb=100,
                compression_enabled=True,
                auto_scale=False,
            ),
            "balanced": PerformanceProfile(
                name="balanced",
                description="Сбалансированные настройки",
                max_concurrent_downloads=5,
                max_concurrent_processing=4,
                chunk_size_kb=1024,
                cache_size_mb=500,
                compression_enabled=True,
                auto_scale=True,
            ),
            "aggressive": PerformanceProfile(
                name="aggressive",
                description="Агрессивные настройки для максимальной производительности",
                max_concurrent_downloads=10,
                max_concurrent_processing=8,
                chunk_size_kb=2048,
                cache_size_mb=1000,
                compression_enabled=False,
                auto_scale=True,
            ),
        }

        # Set the current profile based on the provided performance_profile parameter
        if performance_profile not in self.profiles:
            logger.warning(
                f"Unknown performance profile '{performance_profile}', using 'balanced'"
            )
            self.current_profile = self.profiles["balanced"]
        else:
            self.current_profile = self.profiles[performance_profile]
            logger.debug(f"Performance monitor initialized with profile: {performance_profile}")

    async def start(self):
        """Запуск мониторинга."""
        if self._monitoring:
            return

        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("Performance monitor started")

    async def stop(self):
        """Остановка мониторинга."""
        self._monitoring = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        await self._save_metrics_history()
        logger.info("Performance monitor stopped")

    async def _monitoring_loop(self):
        """Основной цикл мониторинга."""
        while self._monitoring:
            try:
                metrics = await self._collect_metrics()
                self.metrics_history.append(metrics)

                # Проверяем алерты
                await self._check_alerts(metrics)

                # Вызываем коллбеки (поддержка sync и async)
                for callback in self.metric_callbacks:
                    try:
                        if inspect.iscoroutinefunction(callback):
                            # Async callback - создаём task
                            asyncio.create_task(callback(metrics))
                        else:
                            # Sync callback - вызываем напрямую
                            callback(metrics)
                    except Exception as e:
                        logger.error(f"Error in metric callback: {e}")

                await asyncio.sleep(self.monitoring_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.monitoring_interval)

    async def _collect_metrics(self) -> SystemMetrics:
        """Сбор системных метрик."""
        # Системные метрики
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        # Сетевые метрики
        network_stats = psutil.net_io_counters()
        network_sent_mb = network_stats.bytes_sent / (1024 * 1024)
        network_recv_mb = network_stats.bytes_recv / (1024 * 1024)

        # Дисковые метрики
        disk_stats = psutil.disk_io_counters()
        disk_read_mb = 0.0
        disk_write_mb = 0.0

        if disk_stats:
            disk_read_mb = disk_stats.read_bytes / (1024 * 1024)
            disk_write_mb = disk_stats.write_bytes / (1024 * 1024)

        # Метрики процесса
        process = psutil.Process()
        process_memory_mb = process.memory_info().rss / (1024 * 1024)
        process_cpu_percent = process.cpu_percent()

        # Подсчет открытых файлов
        try:
            open_files = len(process.open_files())
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            open_files = 0

        # Количество потоков
        active_threads = process.num_threads()

        return SystemMetrics(
            timestamp=time.time(),
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            disk_usage_percent=disk.percent,
            network_sent_mb=network_sent_mb,
            network_recv_mb=network_recv_mb,
            disk_read_mb=disk_read_mb,
            disk_write_mb=disk_write_mb,
            process_memory_mb=process_memory_mb,
            process_cpu_percent=process_cpu_percent,
            active_threads=active_threads,
            open_files=open_files,
        )

    async def _check_alerts(self, metrics: SystemMetrics):
        """Проверка и создание алертов."""
        current_time = time.time()

        for metric_name, thresholds in self.alert_thresholds.items():
            value = getattr(metrics, metric_name, None)
            if value is None:
                continue

            alert_id = f"{metric_name}_alert"

            # Определяем уровень алерта
            alert_level = None
            threshold = None

            if value >= thresholds.get("critical", float("inf")):
                alert_level = AlertLevel.CRITICAL
                threshold = thresholds["critical"]
            elif value >= thresholds.get("warning", float("inf")):
                alert_level = AlertLevel.WARNING
                threshold = thresholds["warning"]

            if alert_level:
                # Создаем или обновляем алерт
                if alert_id not in self.alerts or self.alerts[alert_id].resolved:
                    assert threshold is not None, (
                        "Threshold must be set when alert_level is set"
                    )
                    alert = PerformanceAlert(
                        id=alert_id,
                        level=alert_level,
                        message=f"{metric_name} is {alert_level.value}: {value:.2f} >= {threshold}",
                        timestamp=current_time,
                        metric_name=metric_name,
                        current_value=value,
                        threshold=threshold,
                    )

                    self.alerts[alert_id] = alert
                    self.alert_history.append(alert)

                    # Вызываем коллбеки (поддержка sync и async)
                    for callback in self.alert_callbacks:
                        try:
                            if inspect.iscoroutinefunction(callback):
                                # Async callback - создаём task
                                asyncio.create_task(callback(alert))
                            else:
                                # Sync callback - вызываем напрямую
                                callback(alert)
                        except Exception as e:
                            logger.error(f"Error in alert callback: {e}")

                    logger.warning(f"Performance alert: {alert.message}")

            else:
                # Закрываем алерт если он был активен
                if alert_id in self.alerts and not self.alerts[alert_id].resolved:
                    self.alerts[alert_id].resolved = True
                    self.alerts[alert_id].resolved_at = current_time
                    logger.info(f"Performance alert resolved: {metric_name}")

    def add_metric_callback(self, callback: Union[Callable[[SystemMetrics], None], Callable[[SystemMetrics], Awaitable[None]]]):
        """Добавление коллбека для метрик (поддерживает sync и async функции)."""
        self.metric_callbacks.append(callback)

    def add_alert_callback(self, callback: Union[Callable[[PerformanceAlert], None], Callable[[PerformanceAlert], Awaitable[None]]]):
        """Добавление коллбека для алертов (поддерживает sync и async функции)."""
        self.alert_callbacks.append(callback)

    def profile_function(self, name: str):
        """Декоратор для профилирования функций."""

        def decorator(func):
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                success = False
                try:
                    result = await func(*args, **kwargs)
                    success = True
                    return result
                except Exception:
                    raise
                finally:
                    duration = time.time() - start_time
                    self._record_function_call(name, duration, success)

            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                success = False
                try:
                    result = func(*args, **kwargs)
                    success = True
                    return result
                except Exception:
                    raise
                finally:
                    duration = time.time() - start_time
                    self._record_function_call(name, duration, success)

            return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

        return decorator

    def _record_function_call(self, name: str, duration: float, success: bool):
        """Запись вызова функции."""
        if name not in self.function_stats:
            self.function_stats[name] = ComponentStats(name)
        self.function_stats[name].record_call(duration, success)

    def record_component_metric(
        self, component: str, duration: float, success: bool = True
    ):
        """Запись метрики компонента."""
        if component not in self.component_stats:
            self.component_stats[component] = ComponentStats(component)
        self.component_stats[component].record_call(duration, success)

    def get_current_metrics(self) -> Optional[SystemMetrics]:
        """Получение текущих метрик."""
        return self.metrics_history[-1] if self.metrics_history else None

    def get_metrics_history(self, last_n: int = 100) -> List[SystemMetrics]:
        """Получение истории метрик."""
        return list(self.metrics_history)[-last_n:]

    def get_active_alerts(self) -> List[PerformanceAlert]:
        """Получение активных алертов."""
        return [alert for alert in self.alerts.values() if not alert.resolved]

    def get_alert_history(self) -> List[PerformanceAlert]:
        """Получение истории алертов."""
        return list(self.alert_history)

    def get_resource_state(self) -> ResourceState:
        """Определение текущего состояния ресурсов."""
        current = self.get_current_metrics()
        if not current:
            return ResourceState.OPTIMAL

        # Анализируем ключевые метрики
        cpu_stress = current.cpu_percent > 70
        memory_stress = current.memory_percent > 75
        disk_stress = current.disk_usage_percent > 80

        critical_count = sum(
            [
                current.cpu_percent > 90,
                current.memory_percent > 90,
                current.process_memory_mb > 2000,
            ]
        )

        stress_count = sum([cpu_stress, memory_stress, disk_stress])

        if critical_count >= 2:
            return ResourceState.CRITICAL
        elif critical_count >= 1 or stress_count >= 3:
            return ResourceState.OVERLOADED
        elif stress_count >= 2:
            return ResourceState.STRESSED
        else:
            return ResourceState.OPTIMAL

    def get_performance_recommendations(self) -> List[str]:
        """Получение рекомендаций по производительности."""
        current = self.get_current_metrics()
        if not current:
            return []

        recommendations = []

        if current.cpu_percent > 80:
            recommendations.append(
                "Высокое использование CPU. Рекомендуется снизить количество параллельных операций."
            )

        if current.memory_percent > 80:
            recommendations.append(
                "Высокое использование памяти. Рекомендуется очистить кэш или снизить размер буферов."
            )

        if current.process_memory_mb > 1500:
            recommendations.append(
                "Процесс использует много памяти. Рекомендуется перезапустить приложение."
            )

        if current.open_files > 400:
            recommendations.append(
                "Много открытых файлов. Проверьте корректность закрытия файлов."
            )

        # Анализ трендов
        if len(self.metrics_history) >= 10:
            recent_metrics = list(self.metrics_history)[-10:]

            # Тренд использования памяти
            memory_trend = [m.memory_percent for m in recent_metrics]
            if len(memory_trend) >= 5:
                memory_growth = (memory_trend[-1] - memory_trend[0]) / len(memory_trend)
                if memory_growth > 2.0:
                    recommendations.append(
                        "Обнаружена утечка памяти. Рекомендуется диагностика."
                    )

        # Рекомендации по профилю
        state = self.get_resource_state()
        if state in [ResourceState.OVERLOADED, ResourceState.CRITICAL]:
            if self.current_profile.name != "conservative":
                recommendations.append(
                    "Рекомендуется переключиться на консервативный профиль производительности."
                )
        elif state == ResourceState.OPTIMAL:
            if self.current_profile.name == "conservative":
                recommendations.append(
                    "Система работает стабильно. Можно попробовать более агрессивный профиль."
                )

        return recommendations

    def set_performance_profile(self, profile_name: str) -> bool:
        """Установка профиля производительности."""
        if profile_name not in self.profiles:
            logger.error(f"Unknown performance profile: {profile_name}")
            return False

        old_profile = self.current_profile.name
        self.current_profile = self.profiles[profile_name]
        logger.info(f"Performance profile changed from {old_profile} to {profile_name}")
        return True

    def get_performance_summary(self) -> Dict[str, Any]:
        """Получение сводки производительности."""
        current = self.get_current_metrics()
        state = self.get_resource_state()
        active_alerts = self.get_active_alerts()

        # Статистика компонентов
        top_components = sorted(
            self.component_stats.values(), key=lambda x: x.total_duration, reverse=True
        )[:5]

        # Статистика функций
        top_functions = sorted(
            self.function_stats.values(), key=lambda x: x.avg_duration, reverse=True
        )[:5]

        return {
            "timestamp": time.time(),
            "resource_state": state.value,
            "current_profile": self.current_profile.name,
            "active_alerts_count": len(active_alerts),
            "current_metrics": asdict(current) if current else None,
            "recommendations": self.get_performance_recommendations(),
            "top_components": [
                {
                    "name": comp.name,
                    "total_calls": comp.calls_total,
                    "success_rate": comp.success_rate,
                    "avg_duration": comp.avg_duration,
                }
                for comp in top_components
            ],
            "top_functions": [
                {
                    "name": func.name,
                    "total_calls": func.calls_total,
                    "avg_duration": func.avg_duration,
                }
                for func in top_functions
            ],
        }

    async def _save_metrics_history(self):
        """Сохранение истории метрик."""
        try:
            metrics_file = self.data_path / "metrics_history.json"

            data = {
                "timestamp": time.time(),
                "metrics": [asdict(m) for m in self.metrics_history],
                "component_stats": {
                    name: asdict(stats) for name, stats in self.component_stats.items()
                },
                "function_stats": {
                    name: asdict(stats) for name, stats in self.function_stats.items()
                },
            }

            async with aiofiles.open(metrics_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, default=str))

            logger.debug(f"Metrics history saved to {metrics_file}")

        except Exception as e:
            logger.error(f"Failed to save metrics history: {e}")

    async def export_metrics(self, filepath: Path, format: str = "json") -> bool:
        """Экспорт метрик в файл."""
        try:
            summary = self.get_performance_summary()

            if format.lower() == "json":
                async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(summary, indent=2, default=str))
            else:
                logger.error(f"Unsupported export format: {format}")
                return False

            logger.info(f"Metrics exported to {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to export metrics: {e}")
            return False

    def get_peak_memory(self) -> float:
        """Получение пиковой памяти процесса в MB."""
        if not self.metrics_history:
            return 0.0
        return max(m.process_memory_mb for m in self.metrics_history)

    def get_avg_cpu(self) -> float:
        """Получение средней нагрузки CPU в процентах."""
        if not self.metrics_history:
            return 0.0
        return sum(m.cpu_percent for m in self.metrics_history) / len(
            self.metrics_history
        )

    def sample_resources(self):
        """Принудительный сбор текущих ресурсов (синхронный метод)."""
        # Этот метод вызывается из синхронного кода
        # Просто обновляет last_sample_time
        self.last_sample_time = time.time()

    @property
    def last_sample_time(self) -> float:
        """Время последнего сбора ресурсов."""
        if not hasattr(self, "_last_sample_time"):
            self._last_sample_time = time.time()
        return self._last_sample_time

    @last_sample_time.setter
    def last_sample_time(self, value: float):
        """Установка времени последнего сбора ресурсов."""
        self._last_sample_time = value

    @property
    def memory_samples(self) -> List[float]:
        """История образцов памяти."""
        return [m.process_memory_mb for m in self.metrics_history]


# Глобальный экземпляр монитора
_performance_monitor: Optional[PerformanceMonitor] = None


async def get_performance_monitor(performance_profile: str = "balanced") -> PerformanceMonitor:
    """Получение глобального монитора производительности.
    
    Args:
        performance_profile: Профиль производительности (conservative, balanced, aggressive)
    """
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = PerformanceMonitor(performance_profile=performance_profile)
        await _performance_monitor.start()
    return _performance_monitor


async def shutdown_performance_monitor():
    """Завершение работы монитора производительности."""
    global _performance_monitor
    if _performance_monitor:
        await _performance_monitor.stop()
        _performance_monitor = None


# Декораторы для удобного использования
def profile_async(name: str):
    """Декоратор для профилирования асинхронных функций."""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            monitor = await get_performance_monitor()
            return await monitor.profile_function(name)(func)(*args, **kwargs)

        return wrapper

    return decorator


def profile_sync(name: str):
    """Декоратор для профилирования синхронных функций."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            # Для синхронных функций получаем монитор синхронно
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Если цикл работает, создаем задачу
                    return asyncio.create_task(
                        _profile_sync_helper(name, func, *args, **kwargs)
                    )
                else:
                    # Если цикла нет, запускаем его
                    return loop.run_until_complete(
                        _profile_sync_helper(name, func, *args, **kwargs)
                    )
            except RuntimeError:
                # Если нет активного цикла, просто выполняем функцию
                return func(*args, **kwargs)

        return wrapper

    return decorator


async def _profile_sync_helper(name: str, func: Callable, *args, **kwargs):
    """Помощник для профилирования синхронных функций."""
    monitor = await get_performance_monitor()
    return monitor.profile_function(name)(func)(*args, **kwargs)
