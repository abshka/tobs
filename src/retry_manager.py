import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

from src.utils import logger
class BackoffStrategy(Enum):
    """Стратегии backoff для retry логики."""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    ADAPTIVE = "adaptive"
@dataclass
class RetryConfig:
    """Конфигурация для retry операций."""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    jitter: bool = True
    jitter_range: float = 0.1
    backoff_multiplier: float = 2.0

    # Adaptive strategy parameters
    success_threshold: float = 0.8  # Если успешность выше - уменьшаем delays
    failure_threshold: float = 0.3  # Если успешность ниже - увеличиваем delays
@dataclass
class OperationStats:
    """Статистика операций для adaptive backoff."""
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    avg_response_time: float = 0.0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    def update_success(self, response_time: float = 0.0):
        """Обновить статистику при успешной операции."""
        self.total_attempts += 1
        self.successful_attempts += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.last_success_time = time.time()

        # Обновляем среднее время ответа
        if self.avg_response_time == 0:
            self.avg_response_time = response_time
        else:
            self.avg_response_time = (self.avg_response_time * 0.8) + (response_time * 0.2)

    def update_failure(self):
        """Обновить статистику при неудачной операции."""
        self.total_attempts += 1
        self.failed_attempts += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure_time = time.time()

    @property
    def success_rate(self) -> float:
        """Коэффициент успешности операций."""
        if self.total_attempts == 0:
            return 1.0
        return self.successful_attempts / self.total_attempts
class SmartRetryManager:
    """Умный менеджер retry операций с adaptive backoff."""

    def __init__(self, default_config: Optional[RetryConfig] = None):
        self.default_config = default_config or RetryConfig()
        self.operation_stats: Dict[str, OperationStats] = {}
        self._adaptive_multipliers: Dict[str, float] = {}

    def get_stats(self, operation_name: str) -> OperationStats:
        """Получить статистику для операции."""
        if operation_name not in self.operation_stats:
            self.operation_stats[operation_name] = OperationStats()
        return self.operation_stats[operation_name]

    def _add_jitter(self, delay: float, jitter_range: float) -> float:
        """Добавить jitter к задержке."""
        if jitter_range <= 0:
            return delay

        jitter = random.uniform(-jitter_range, jitter_range)
        return max(0.1, delay * (1 + jitter))

    def _calculate_adaptive_multiplier(self, stats: OperationStats, config: RetryConfig) -> float:
        """Вычислить adaptive multiplier на основе статистики."""
        success_rate = stats.success_rate

        if success_rate >= config.success_threshold:
            # Высокая успешность - уменьшаем задержки
            return max(0.5, 1.0 - (success_rate - config.success_threshold) * 2)
        elif success_rate <= config.failure_threshold:
            # Низкая успешность - увеличиваем задержки
            multiplier = 1.0 + (config.failure_threshold - success_rate) * 3

            # Дополнительное увеличение при consecutive failures
            if stats.consecutive_failures > 3:
                multiplier *= 1.5

            return min(5.0, multiplier)
        else:
            # Средняя успешность - стандартные задержки
            return 1.0

    def calculate_delay(self, attempt: int, operation_name: str,
                       config: Optional[RetryConfig] = None) -> float:
        """Вычислить задержку для конкретной попытки."""
        config = config or self.default_config
        stats = self.get_stats(operation_name)

        # Базовая задержка в зависимости от стратегии
        if config.strategy == BackoffStrategy.FIXED:
            delay = config.base_delay
        elif config.strategy == BackoffStrategy.LINEAR:
            delay = config.base_delay * attempt
        elif config.strategy == BackoffStrategy.EXPONENTIAL:
            delay = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
        elif config.strategy == BackoffStrategy.ADAPTIVE:
            # Adaptive стратегия учитывает статистику
            base_exponential = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
            adaptive_multiplier = self._calculate_adaptive_multiplier(stats, config)
            delay = base_exponential * adaptive_multiplier
        else:
            delay = config.base_delay

        # Ограничиваем максимальной задержкой
        delay = min(delay, config.max_delay)

        # Добавляем jitter
        if config.jitter:
            delay = self._add_jitter(delay, config.jitter_range)

        return delay

    async def retry_async(self,
                         operation: Callable[..., Any],
                         operation_name: str,
                         config: Optional[RetryConfig] = None,
                         *args, **kwargs) -> Any:
        """
        Выполнить асинхронную операцию с retry логикой.

        Args:
            operation: Асинхронная функция для выполнения
            operation_name: Имя операции для статистики
            config: Конфигурация retry (опционально)
            *args, **kwargs: Аргументы для передачи в operation

        Returns:
            Результат выполнения operation

        Raises:
            Exception: Последнее исключение если все попытки неудачны
        """
        config = config or self.default_config
        stats = self.get_stats(operation_name)
        last_exception = None

        for attempt in range(1, config.max_attempts + 1):
            start_time = time.time()

            try:
                result = await operation(*args, **kwargs)

                # Успешная операция
                response_time = time.time() - start_time
                stats.update_success(response_time)

                if attempt > 1:
                    logger.info(f"[{operation_name}] Success on attempt {attempt}/{config.max_attempts}")

                return result

            except Exception as e:
                last_exception = e
                stats.update_failure()

                if attempt == config.max_attempts:
                    logger.error(f"[{operation_name}] Failed after {config.max_attempts} attempts: {e}")
                    break

                # Вычисляем задержку перед следующей попыткой
                delay = self.calculate_delay(attempt, operation_name, config)

                logger.warning(f"[{operation_name}] Attempt {attempt}/{config.max_attempts} failed: {e}. "
                             f"Retrying in {delay:.2f}s (strategy: {config.strategy.value})")

                await asyncio.sleep(delay)

        # Все попытки неудачны
        raise last_exception

    def get_operation_summary(self, operation_name: str) -> Dict[str, Any]:
        """Получить сводку по операции."""
        if operation_name not in self.operation_stats:
            return {"error": "No stats available"}

        stats = self.operation_stats[operation_name]
        return {
            "operation": operation_name,
            "total_attempts": stats.total_attempts,
            "success_rate": f"{stats.success_rate:.1%}",
            "avg_response_time": f"{stats.avg_response_time:.3f}s",
            "consecutive_failures": stats.consecutive_failures,
            "consecutive_successes": stats.consecutive_successes,
            "last_success": time.strftime("%H:%M:%S", time.localtime(stats.last_success_time)) if stats.last_success_time else "Never",
            "last_failure": time.strftime("%H:%M:%S", time.localtime(stats.last_failure_time)) if stats.last_failure_time else "Never"
        }

    def reset_stats(self, operation_name: Optional[str] = None):
        """Сбросить статистику операций."""
        if operation_name:
            self.operation_stats.pop(operation_name, None)
            self._adaptive_multipliers.pop(operation_name, None)
        else:
            self.operation_stats.clear()
            self._adaptive_multipliers.clear()
class ThrottleManager:
    """Менеджер для обнаружения и управления throttling."""

    def __init__(self,
                 speed_threshold_kbps: float = 50.0,
                 detection_window: int = 5,
                 cooldown_multiplier: float = 2.0):
        self.speed_threshold = speed_threshold_kbps
        self.detection_window = detection_window
        self.cooldown_multiplier = cooldown_multiplier

        self._speed_history: Dict[str, list] = {}
        self._throttle_detected: Dict[str, bool] = {}
        self._last_throttle_time: Dict[str, float] = {}

    def record_speed(self, operation_name: str, speed_kbps: float):
        """Записать скорость операции."""
        if operation_name not in self._speed_history:
            self._speed_history[operation_name] = []

        history = self._speed_history[operation_name]
        history.append(speed_kbps)

        # Ограничиваем размер истории
        if len(history) > self.detection_window:
            history.pop(0)

    def is_throttled(self, operation_name: str) -> bool:
        """Проверить, обнаружен ли throttling."""
        if operation_name not in self._speed_history:
            return False

        history = self._speed_history[operation_name]
        if len(history) < self.detection_window:
            return False

        # Проверяем, что все последние измерения ниже порога
        recent_speeds = history[-self.detection_window:]
        avg_speed = sum(recent_speeds) / len(recent_speeds)

        is_throttled = avg_speed < self.speed_threshold

        if is_throttled and not self._throttle_detected.get(operation_name, False):
            self._throttle_detected[operation_name] = True
            self._last_throttle_time[operation_name] = time.time()
            logger.warning(f"[{operation_name}] Throttling detected! Avg speed: {avg_speed:.1f} KB/s")
        elif not is_throttled and self._throttle_detected.get(operation_name, False):
            self._throttle_detected[operation_name] = False
            logger.info(f"[{operation_name}] Throttling resolved. Current speed: {avg_speed:.1f} KB/s")

        return is_throttled

    async def adaptive_delay(self, operation_name: str) -> float:
        """Вычислить adaptive задержку на основе throttling."""
        if not self.is_throttled(operation_name):
            return 0.0

        # Время с последнего обнаружения throttling
        last_throttle = self._last_throttle_time.get(operation_name, time.time())
        time_since_throttle = time.time() - last_throttle

        # Увеличиваем задержку со временем
        base_delay = min(30.0, time_since_throttle * 0.5)

        # Добавляем jitter
        jitter = random.uniform(0.8, 1.2)
        delay = base_delay * jitter

        if delay > 0:
            logger.info(f"[{operation_name}] Applying throttle delay: {delay:.1f}s")
            await asyncio.sleep(delay)

        return delay
# Глобальные экземпляры для использования в проекте
retry_manager = SmartRetryManager()
throttle_manager = ThrottleManager()

# Предустановленные конфигурации
MEDIA_DOWNLOAD_CONFIG = RetryConfig(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    strategy=BackoffStrategy.EXPONENTIAL,
    jitter=True,
    backoff_multiplier=2.0
)

TELEGRAM_API_CONFIG = RetryConfig(
    max_attempts=5,
    base_delay=2.0,
    max_delay=60.0,
    strategy=BackoffStrategy.ADAPTIVE,
    jitter=True,
    backoff_multiplier=1.5
)

FILE_IO_CONFIG = RetryConfig(
    max_attempts=3,
    base_delay=0.5,
    max_delay=5.0,
    strategy=BackoffStrategy.LINEAR,
    jitter=False
)
