"""
Менеджер соединений с retry, timeout, concurrency и throttling.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from telethon.errors import FloodWaitError, RPCError, SlowModeWaitError
from telethon.errors import TimeoutError as TelegramTimeoutError


class BackoffStrategy(Enum):
    """Стратегии backoff для retry логики."""

    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    ADAPTIVE = "adaptive"


class PoolType(Enum):
    """Типы пулов задач."""

    DOWNLOAD = "download"
    IO = "io"
    PROCESSING = "processing"
    FFMPEG = "ffmpeg"
    API = "api"


@dataclass
class ConnectionConfig:
    """Конфигурация соединения."""

    # Retry настройки
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    jitter: bool = True
    jitter_range: float = 0.1
    backoff_multiplier: float = 2.0

    # Timeout настройки
    base_timeout: float = 300.0
    large_file_timeout: float = 3600.0
    huge_file_timeout: float = 7200.0

    # Throttling настройки
    speed_threshold_kbps: float = 50.0
    detection_window: int = 5
    cooldown_multiplier: float = 2.0

    # Concurrency настройки
    max_concurrent: int = 5
    auto_scale: bool = True
    scale_threshold: float = 0.8


@dataclass
class OperationStats:
    """Статистика операций."""

    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    avg_response_time: float = 0.0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    speed_history: List[float] = field(default_factory=list)
    stall_count: int = 0
    timeout_count: int = 0

    def update_success(self, response_time: float = 0.0):
        """Обновление при успешной операции."""
        self.total_attempts += 1
        self.successful_attempts += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.last_success_time = time.time()

        if self.avg_response_time == 0:
            self.avg_response_time = response_time
        else:
            self.avg_response_time = (self.avg_response_time * 0.8) + (
                response_time * 0.2
            )

    def update_failure(self):
        """Обновление при неудачной операции."""
        self.total_attempts += 1
        self.failed_attempts += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure_time = time.time()

    @property
    def success_rate(self) -> float:
        """Коэффициент успешности."""
        if self.total_attempts == 0:
            return 1.0
        return self.successful_attempts / self.total_attempts

    def record_speed(self, speed_kbps: float, window_size: int = 5):
        """Запись скорости передачи."""
        self.speed_history.append(speed_kbps)
        if len(self.speed_history) > window_size:
            self.speed_history.pop(0)

    @property
    def avg_speed_kbps(self) -> float:
        """Средняя скорость передачи."""
        return (
            sum(self.speed_history) / len(self.speed_history)
            if self.speed_history
            else 0.0
        )


@dataclass
class DownloadProgress:
    """Прогресс загрузки."""

    total_bytes: int = 0
    downloaded_bytes: int = 0
    start_time: float = 0.0
    last_progress_time: float = 0.0

    @property
    def progress_percent(self) -> float:
        """Процент выполнения."""
        if self.total_bytes == 0:
            return 0.0
        return (self.downloaded_bytes / self.total_bytes) * 100

    @property
    def current_speed_kbps(self) -> float:
        """Текущая скорость в KB/s."""
        elapsed = time.time() - self.start_time
        if elapsed <= 0:
            return 0.0
        return (self.downloaded_bytes / elapsed) / 1024

    @property
    def eta_seconds(self) -> float:
        """Оставшееся время в секундах."""
        speed = self.current_speed_kbps
        if speed <= 0:
            return float("inf")
        remaining_kb = (self.total_bytes - self.downloaded_bytes) / 1024
        return remaining_kb / speed


class AdaptiveTaskPool:
    """Адаптивный пул задач с автомасштабированием."""

    def __init__(
        self, pool_type: PoolType, max_workers: int = 5, auto_scale: bool = True
    ):
        self.pool_type = pool_type
        self.max_workers = max_workers
        self.auto_scale = auto_scale
        self.semaphore = asyncio.Semaphore(max_workers)

        # Статистика
        self.active_tasks = 0
        self.queued_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.avg_task_time = 0.0

        # Мониторинг производительности
        self.performance_history: List[float] = []
        self.last_scale_time = time.time()
        self.scale_cooldown = 30.0  # Секунд между изменениями масштаба

    async def submit(self, coro: Callable, *args, **kwargs) -> Any:
        """Выполнение задачи в пуле."""
        self.queued_tasks += 1

        async with self.semaphore:
            self.queued_tasks -= 1
            self.active_tasks += 1

            start_time = time.time()
            try:
                result = await coro(*args, **kwargs)
                self.completed_tasks += 1

                # Обновляем статистику
                task_time = time.time() - start_time
                if self.avg_task_time == 0:
                    self.avg_task_time = task_time
                else:
                    self.avg_task_time = (self.avg_task_time * 0.8) + (task_time * 0.2)

                self.performance_history.append(task_time)
                if len(self.performance_history) > 20:
                    self.performance_history.pop(0)

                # Автомасштабирование
                if self.auto_scale:
                    await self._consider_scaling()

                return result

            except Exception:
                self.failed_tasks += 1
                raise
            finally:
                self.active_tasks -= 1

    async def _consider_scaling(self):
        """Рассмотрение изменения масштаба пула."""
        if time.time() - self.last_scale_time < self.scale_cooldown:
            return

        # Анализ загрузки
        total_capacity = self.semaphore._value + self.active_tasks
        utilization = self.active_tasks / total_capacity if total_capacity > 0 else 0

        # Анализ очереди
        queue_pressure = self.queued_tasks / max(self.active_tasks, 1)

        # Анализ производительности
        performance_degraded = False
        if len(self.performance_history) >= 5:
            recent_avg = sum(self.performance_history[-5:]) / 5
            historical_avg = sum(self.performance_history) / len(
                self.performance_history
            )
            if historical_avg > 0:
                performance_ratio = recent_avg / historical_avg
                performance_degraded = (
                    performance_ratio > 1.2
                )  # Производительность ухудшилась на 20%

        # Решение о масштабировании
        if (
            utilization > 0.8
            and queue_pressure > 2
            and self.max_workers < 20
            and not performance_degraded
        ):
            # Увеличиваем пул
            new_capacity = min(self.max_workers + 2, 20)
            self._resize_pool(new_capacity)
            logger.info(
                f"Scaled up {self.pool_type.value} pool to {new_capacity} workers"
            )

        elif utilization < 0.3 and queue_pressure < 0.5 and self.max_workers > 2:
            # Уменьшаем пул
            new_capacity = max(self.max_workers - 1, 2)
            self._resize_pool(new_capacity)
            logger.info(
                f"Scaled down {self.pool_type.value} pool to {new_capacity} workers"
            )

        self.last_scale_time = time.time()

    def _resize_pool(self, new_size: int):
        """Изменение размера пула."""
        old_size = self.max_workers
        self.max_workers = new_size

        # Создаем новый semaphore с новым лимитом
        old_semaphore = self.semaphore
        self.semaphore = asyncio.Semaphore(new_size)

        # Пытаемся сохранить текущее количество активных задач
        try:
            active_tasks = old_size - old_semaphore._value
            for _ in range(min(active_tasks, new_size)):
                self.semaphore._value -= 1
        except AttributeError:
            # Если не удалось получить текущее состояние, просто используем новый semaphore
            pass

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики пула."""
        total_tasks = self.completed_tasks + self.failed_tasks
        success_rate = (self.completed_tasks / total_tasks) if total_tasks > 0 else 1.0

        return {
            "pool_type": self.pool_type.value,
            "max_workers": self.max_workers,
            "active_tasks": self.active_tasks,
            "queued_tasks": self.queued_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "success_rate": success_rate,
            "avg_task_time": self.avg_task_time,
            "utilization": self.active_tasks / self.max_workers,
        }


class ConnectionManager:
    """Менеджер соединений с retry, timeout, throttling и concurrency."""

    def __init__(self, default_config: Optional[ConnectionConfig] = None):
        self.default_config = default_config or ConnectionConfig()

        # Статистика операций
        self.operation_stats: Dict[str, OperationStats] = {}
        self.download_progress: Dict[str, DownloadProgress] = {}

        # Пулы задач
        self.pools: Dict[PoolType, AdaptiveTaskPool] = {
            PoolType.DOWNLOAD: AdaptiveTaskPool(PoolType.DOWNLOAD, 5),
            PoolType.IO: AdaptiveTaskPool(PoolType.IO, 10),
            PoolType.PROCESSING: AdaptiveTaskPool(PoolType.PROCESSING, 4),
            PoolType.FFMPEG: AdaptiveTaskPool(PoolType.FFMPEG, 2),
            PoolType.API: AdaptiveTaskPool(
                PoolType.API, 10
            ),  # Increased from 3 to 10
        }

        # Состояние
        self._shutdown = False
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def download_semaphore(self):
        """Семафор для загрузок (для обратной совместимости)."""
        return self.pools[PoolType.DOWNLOAD].semaphore

    @property
    def io_semaphore(self):
        """Семафор для IO операций (для обратной совместимости)."""
        return self.pools[PoolType.IO].semaphore

    async def start(self):
        """Запуск менеджера соединений."""
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("Connection manager started")

    async def _monitoring_loop(self):
        """Мониторинг состояния соединений."""
        while not self._shutdown:
            try:
                await asyncio.sleep(60)  # Мониторинг каждую минуту
                await self._cleanup_old_stats()
                await self._log_performance_summary()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")

    async def _cleanup_old_stats(self):
        """Очистка старой статистики."""
        current_time = time.time()
        cutoff_time = current_time - 3600  # Удаляем статистику старше часа

        keys_to_remove = []
        for key, stats in self.operation_stats.items():
            if (
                stats.last_success_time < cutoff_time
                and stats.last_failure_time < cutoff_time
            ):
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self.operation_stats[key]

        if keys_to_remove:
            logger.debug(f"Cleaned up {len(keys_to_remove)} old operation stats")

    async def _log_performance_summary(self):
        """Логирование сводки производительности."""
        total_operations = sum(
            stats.total_attempts for stats in self.operation_stats.values()
        )
        if total_operations == 0:
            return

        total_success = sum(
            stats.successful_attempts for stats in self.operation_stats.values()
        )
        overall_success_rate = total_success / total_operations

        logger.info(
            f"Connection manager stats: {total_operations} operations, "
            f"{overall_success_rate:.2%} success rate"
        )

    def get_stats(self, operation_name: str) -> OperationStats:
        """Получение статистики операции."""
        if operation_name not in self.operation_stats:
            self.operation_stats[operation_name] = OperationStats()
        return self.operation_stats[operation_name]

    def _add_jitter(self, delay: float, jitter_range: float) -> float:
        """Добавление jitter к задержке."""
        if jitter_range <= 0:
            return delay
        jitter = random.uniform(-jitter_range, jitter_range)
        return max(0.1, delay * (1 + jitter))

    def _calculate_adaptive_multiplier(
        self, stats: OperationStats, config: ConnectionConfig
    ) -> float:
        """Вычисление adaptive multiplier."""
        success_rate = stats.success_rate

        if success_rate >= 0.8:
            return max(0.5, 1.0 - (success_rate - 0.8) * 2)
        elif success_rate <= 0.3:
            multiplier = 1.0 + (0.3 - success_rate) * 3
            if stats.consecutive_failures > 3:
                multiplier *= 1.5
            return min(5.0, multiplier)
        else:
            return 1.0

    def calculate_delay(
        self,
        attempt: int,
        operation_name: str,
        config: Optional[ConnectionConfig] = None,
    ) -> float:
        """Вычисление задержки для retry."""
        config = config or self.default_config
        stats = self.get_stats(operation_name)

        # Базовая задержка
        if config.strategy == BackoffStrategy.FIXED:
            delay = config.base_delay
        elif config.strategy == BackoffStrategy.LINEAR:
            delay = config.base_delay * attempt
        elif config.strategy == BackoffStrategy.EXPONENTIAL:
            delay = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
        elif config.strategy == BackoffStrategy.ADAPTIVE:
            base_exponential = config.base_delay * (
                config.backoff_multiplier ** (attempt - 1)
            )
            adaptive_multiplier = self._calculate_adaptive_multiplier(stats, config)
            delay = base_exponential * adaptive_multiplier
        else:
            delay = config.base_delay

        # Ограничения и jitter
        delay = min(delay, config.max_delay)
        if config.jitter:
            delay = self._add_jitter(delay, config.jitter_range)

        return delay

    def calculate_timeout(
        self,
        file_size: int,
        operation_name: str,
        config: Optional[ConnectionConfig] = None,
    ) -> float:
        """Вычисление адаптивного таймаута."""
        config = config or self.default_config
        stats = self.get_stats(operation_name)

        file_size_mb = file_size / (1024 * 1024)

        # Базовый таймаут
        if file_size_mb > 1000:
            base_timeout = config.huge_file_timeout
        elif file_size_mb > 500:
            base_timeout = config.large_file_timeout
        else:
            base_timeout = config.base_timeout

        # Адаптация на основе истории
        if stats.timeout_count > 0:
            multiplier = 1.0 + (stats.timeout_count * 0.5)
            base_timeout *= min(multiplier, 3.0)

        # Оценка на основе размера и консервативной скорости
        conservative_speed_kbps = 1000  # 1MB/s
        estimated_time = (file_size / 1024) / conservative_speed_kbps
        size_based_timeout = estimated_time * 2

        # Берем максимум
        adaptive_timeout = max(base_timeout, size_based_timeout)

        # Ограничения
        return float(max(min(adaptive_timeout, 14400), 180))  # От 3 минут до 4 часов

    def is_throttled(
        self, operation_name: str, config: Optional[ConnectionConfig] = None
    ) -> bool:
        """Проверка throttling."""
        config = config or self.default_config
        stats = self.get_stats(operation_name)

        if len(stats.speed_history) < config.detection_window:
            return False

        recent_speeds = stats.speed_history[-config.detection_window :]
        avg_speed = sum(recent_speeds) / len(recent_speeds)

        return avg_speed < config.speed_threshold_kbps

    async def calculate_throttle_delay(
        self, operation_name: str, config: Optional[ConnectionConfig] = None
    ) -> float:
        """Вычисление задержки для throttling."""
        if not self.is_throttled(operation_name, config):
            return 0.0

        config = config or self.default_config
        stats = self.get_stats(operation_name)

        # Базовая задержка увеличивается со временем
        base_delay = min(30.0, stats.consecutive_failures * 2.0)
        jitter = random.uniform(0.8, 1.2)
        delay = base_delay * jitter

        if delay > 0:
            logger.info(f"Applying throttle delay for {operation_name}: {delay:.1f}s")

        return delay

    async def handle_telegram_error(
        self, error: Exception, operation_name: str, attempt: int
    ) -> float:
        """Обработка Telegram-специфичных ошибок."""
        stats = self.get_stats(operation_name)

        if isinstance(error, FloodWaitError):
            wait_time = error.seconds
            logger.warning(f"FloodWait for {operation_name}: waiting {wait_time}s")
            return float(wait_time)

        elif isinstance(error, SlowModeWaitError):
            wait_time = error.seconds
            logger.warning(f"SlowMode wait for {operation_name}: waiting {wait_time}s")
            return float(wait_time)

        elif isinstance(error, TelegramTimeoutError) or "TimeoutError" in str(error):
            stats.timeout_count += 1
            base_delay = 10.0 + (attempt * 5.0)

            if stats.timeout_count > 1:
                multiplier = min(stats.timeout_count, 5)
                base_delay *= multiplier

            logger.warning(
                f"Timeout error for {operation_name} (attempt {attempt}): {error}"
            )
            return min(base_delay, 300)

        elif "GetFileRequest" in str(error):
            delay = 5.0 + (attempt * 2.0)
            logger.warning(f"GetFileRequest error for {operation_name}: {error}")
            return min(delay, 60)

        elif isinstance(error, RPCError):
            delay = 3.0 + (attempt * 1.5)
            logger.warning(f"RPC error for {operation_name}: {error}")
            return min(delay, 30)

        else:
            delay = 2.0 + attempt
            logger.warning(f"Unknown error for {operation_name}: {error}")
            return min(delay, 60)

    async def execute_with_retry(
        self,
        operation: Callable,
        operation_name: str,
        pool_type: PoolType = PoolType.API,
        config: Optional[ConnectionConfig] = None,
        timeout_override: Optional[float] = None,
        file_size: Optional[int] = None,
        *args,
        **kwargs,
    ) -> Any:
        """Выполнение операции с retry, timeout и throttling."""
        config = config or self.default_config
        stats = self.get_stats(operation_name)

        # Определяем таймаут
        if timeout_override:
            timeout = timeout_override
        elif file_size:
            timeout = self.calculate_timeout(file_size, operation_name, config)
        else:
            timeout = config.base_timeout

        pool = self.pools[pool_type]
        last_exception = None

        for attempt in range(1, config.max_attempts + 1):
            # Проверяем throttling
            throttle_delay = await self.calculate_throttle_delay(operation_name, config)
            if throttle_delay > 0:
                await asyncio.sleep(throttle_delay)

            start_time = time.time()

            try:
                # Выполняем операцию в соответствующем пуле с таймаутом
                result = await asyncio.wait_for(
                    pool.submit(operation, *args, **kwargs), timeout=timeout
                )

                # Успешное выполнение
                response_time = time.time() - start_time
                stats.update_success(response_time)

                if attempt > 1:
                    logger.info(
                        f"[{operation_name}] Success on attempt {attempt}/{config.max_attempts}"
                    )

                return result

            except Exception as e:
                last_exception = e
                stats.update_failure()

                if attempt == config.max_attempts:
                    logger.error(
                        f"[{operation_name}] Failed after {config.max_attempts} attempts: {e}"
                    )
                    break

                # Обрабатываем специфичные ошибки Telegram
                if "telegram" in operation_name.lower() or isinstance(
                    e,
                    (FloodWaitError, SlowModeWaitError, TelegramTimeoutError, RPCError),
                ):
                    delay = await self.handle_telegram_error(e, operation_name, attempt)
                else:
                    delay = self.calculate_delay(attempt, operation_name, config)

                logger.warning(
                    f"[{operation_name}] Attempt {attempt}/{config.max_attempts} failed: {e}. "
                    f"Retrying in {delay:.2f}s"
                )

                await asyncio.sleep(delay)

        if last_exception is None:
            raise RuntimeError(
                f"[{operation_name}] Operation failed without capturing exception"
            )
        raise last_exception

    def init_download_progress(
        self, operation_name: str, file_size: int
    ) -> DownloadProgress:
        """Инициализация прогресса загрузки."""
        progress = DownloadProgress(
            total_bytes=file_size,
            start_time=time.time(),
            last_progress_time=time.time(),
        )
        self.download_progress[operation_name] = progress
        return progress

    def update_download_progress(
        self, operation_name: str, downloaded: int
    ) -> Optional[float]:
        """Обновление прогресса загрузки."""
        progress = self.download_progress.get(operation_name)
        if not progress:
            return None

        stats = self.get_stats(operation_name)
        current_time = time.time()

        if downloaded > progress.downloaded_bytes:
            time_diff = current_time - progress.last_progress_time
            if time_diff > 0:
                bytes_diff = downloaded - progress.downloaded_bytes
                current_speed = (bytes_diff / time_diff) / 1024  # KB/s

                stats.record_speed(current_speed, self.default_config.detection_window)

                progress.downloaded_bytes = downloaded
                progress.last_progress_time = current_time
                stats.stall_count = 0

                return current_speed

        # Проверяем stall
        if current_time - progress.last_progress_time > 60.0:  # 60 секунд без прогресса
            stats.stall_count += 1
            progress.last_progress_time = current_time
            logger.warning(
                f"Download stall detected for {operation_name} (stall #{stats.stall_count})"
            )

        return stats.avg_speed_kbps

    def finish_download_progress(self, operation_name: str, success: bool):
        """Завершение отслеживания прогресса."""
        progress = self.download_progress.get(operation_name)
        stats = self.get_stats(operation_name)

        if progress:
            total_time = time.time() - progress.start_time
            file_size_mb = progress.total_bytes / (1024 * 1024)

            if success:
                logger.info(
                    f"Download completed for {operation_name}: "
                    f"{file_size_mb:.1f}MB in {total_time:.0f}s "
                    f"(avg: {stats.avg_speed_kbps:.1f} KB/s)"
                )
            else:
                logger.error(
                    f"Download failed for {operation_name} after {total_time:.0f}s"
                )

            del self.download_progress[operation_name]

    def get_pool_stats(self) -> Dict[str, Dict[str, Any]]:
        """Получение статистики всех пулов."""
        return {
            pool_type.value: pool.get_stats() for pool_type, pool in self.pools.items()
        }

    def get_operation_summary(self, operation_name: str) -> Dict[str, Any]:
        """Получение сводки операции."""
        if operation_name not in self.operation_stats:
            return {"error": "No stats available"}

        stats = self.operation_stats[operation_name]
        return {
            "operation": operation_name,
            "total_attempts": stats.total_attempts,
            "success_rate": f"{stats.success_rate:.1%}",
            "avg_response_time": f"{stats.avg_response_time:.3f}s",
            "avg_speed_kbps": f"{stats.avg_speed_kbps:.1f}",
            "consecutive_failures": stats.consecutive_failures,
            "stall_count": stats.stall_count,
            "timeout_count": stats.timeout_count,
        }

    async def shutdown(self):
        """Завершение работы менеджера."""
        self._shutdown = True

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        logger.info("Connection manager shutdown complete")


# Глобальный экземпляр
_connection_manager: Optional[ConnectionManager] = None


async def get_connection_manager() -> ConnectionManager:
    """Получение глобального менеджера соединений."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
        await _connection_manager.start()
    return _connection_manager


async def shutdown_connection_manager():
    """Завершение работы менеджера соединений."""
    global _connection_manager
    if _connection_manager:
        await _connection_manager.shutdown()
        _connection_manager = None


# Предустановленные конфигурации
MEDIA_DOWNLOAD_CONFIG = ConnectionConfig(
    max_attempts=5,
    base_delay=2.0,
    max_delay=60.0,
    strategy=BackoffStrategy.EXPONENTIAL,
    base_timeout=300.0,
    large_file_timeout=1800.0,
    huge_file_timeout=3600.0,
    max_concurrent=3,
)

LARGE_FILE_CONFIG = ConnectionConfig(
    max_attempts=10,
    base_delay=5.0,
    max_delay=120.0,
    strategy=BackoffStrategy.ADAPTIVE,
    base_timeout=1800.0,
    large_file_timeout=3600.0,
    huge_file_timeout=7200.0,
    max_concurrent=2,
)

API_REQUEST_CONFIG = ConnectionConfig(
    max_attempts=8,
    base_delay=3.0,
    max_delay=180.0,
    strategy=BackoffStrategy.ADAPTIVE,
    base_timeout=60.0,
    max_concurrent=5,
)

FILE_IO_CONFIG = ConnectionConfig(
    max_attempts=5,
    base_delay=1.0,
    max_delay=10.0,
    strategy=BackoffStrategy.LINEAR,
    base_timeout=30.0,
    max_concurrent=10,
)
