"""
Менеджер core систем.
Управляет жизненным циклом всех основных компонентов системы.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .core import (
    AlertLevel,
    CacheManager,
    ConnectionManager,
    PerformanceMonitor,
    ResourceState,
    get_cache_manager,
    get_connection_manager,
    get_performance_monitor,
    shutdown_cache_manager,
    shutdown_connection_manager,
    shutdown_performance_monitor,
)

logger = logging.getLogger(__name__)


class CoreSystemManager:
    """Менеджер всех core систем."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        performance_profile: str = "balanced",
        health_check_interval: float = 0.1,
    ):
        # config_path сохраняется для совместимости с API, но не используется
        self.config_path = config_path
        self.performance_profile = performance_profile

        # Менеджеры
        self._cache_manager: Optional[CacheManager] = None
        self._connection_manager: Optional[ConnectionManager] = None
        self._performance_monitor: Optional[PerformanceMonitor] = None

        # Состояние
        self._initialized = False
        self._shutdown = False

        # TaskGroup для управления background tasks (Phase 3 Task A.1)
        self._task_group: Optional[asyncio.TaskGroup] = None
        self._task_group_runner: Optional[asyncio.Task] = None

        self._health_check_interval = health_check_interval
        self._sleep = asyncio.sleep  # Store reference to avoid global mocking

        # Статистика
        self._start_time = 0.0
        self._total_operations = 0
        self._successful_operations = 0
        self._failed_operations = 0

        # Конфигурация адаптации
        self.adaptation_enabled = True
        self.last_adaptation_time = 0.0
        self.adaptation_cooldown = 60.0  # Минимум 60 секунд между адаптациями

    async def initialize(self) -> bool:
        """
        Инициализация всех core систем.

        Returns:
            True если инициализация прошла успешно
        """
        if self._initialized:
            logger.warning("Core systems already initialized")
            return True

        try:
            self._start_time = time.time()

            logger.info("Initializing core systems...")

            # Инициализируем компоненты в правильном порядке
            # 1. Сначала монитор производительности
            self._performance_monitor = await get_performance_monitor(
                performance_profile=self.performance_profile
            )
            logger.info("Performance monitor initialized")

            # 2. Затем кэш-менеджер
            self._cache_manager = await get_cache_manager()
            logger.info("Cache manager initialized")

            # 3. Наконец менеджер соединений
            self._connection_manager = await get_connection_manager()
            logger.info("Connection manager initialized")

            # Настраиваем интеграцию между компонентами
            await self._setup_integration()

            # Запускаем background tasks в TaskGroup (Phase 3 Task A.1)
            self._task_group_runner = asyncio.create_task(self._run_background_tasks())
            logger.debug("Background task group started")

            self._initialized = True

            logger.info(
                f"Core systems initialized successfully in {time.time() - self._start_time:.2f}s"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize core systems: {e}")
            await self._cleanup_on_failure()
            return False

    async def _run_background_tasks(self):
        """
        Запуск и управление background tasks в TaskGroup (Phase 3 Task A.1).

        TaskGroup автоматически:
        - Управляет жизненным циклом всех задач
        - Агрегирует исключения из всех задач
        - Отменяет все оставшиеся задачи при выходе из контекста
        """
        # Run initial health check synchronously
        try:
            await self._perform_health_check()
        except Exception as e:
            self._failed_operations += 1
            logger.error(f"Initial health check failed: {e}")

        try:
            async with asyncio.TaskGroup() as tg:
                self._task_group = tg
                logger.debug("TaskGroup context entered")

                # Создаём health check task в TaskGroup
                tg.create_task(self._health_check_loop())
                logger.debug("Health check task created in TaskGroup")

                # TaskGroup будет ждать все задачи до завершения
                # При выходе из контекста, все задачи будут отменены (если не завершены)
        except* Exception as exc_group:
            # except* ловит все исключения из TaskGroup
            # exc_group содержит все исключения
            for exc in exc_group.exceptions:
                logger.error(
                    f"Background task failed: {type(exc).__name__}: {exc}", exc_info=exc
                )
                self._failed_operations += 1
        finally:
            self._task_group = None
            logger.debug("TaskGroup context exited and cleaned up")

    async def _setup_integration(self):
        """Настройка интеграции между компонентами."""
        # Добавляем коллбеки для мониторинга
        if self._performance_monitor:
            self._performance_monitor.add_metric_callback(self._on_performance_metric)
            self._performance_monitor.add_alert_callback(self._on_performance_alert)

        logger.debug("Component integration configured")

    def _on_performance_metric(self, metrics):
        """Обработчик метрик производительности."""
        # Можем логировать критические метрики
        if metrics.cpu_percent > 90 or metrics.memory_percent > 90:
            logger.warning(
                f"High resource usage: CPU {metrics.cpu_percent:.1f}%, "
                f"Memory {metrics.memory_percent:.1f}%"
            )

    async def _on_performance_alert(self, alert):
        """Обработчик алертов производительности."""
        if alert.level == AlertLevel.CRITICAL:
            logger.critical(f"Critical performance alert: {alert.message}")

            # При критических алертах можем автоматически адаптироваться
            if self.adaptation_enabled:
                await self._emergency_adaptation()

    async def _emergency_adaptation(self):
        """Экстренная адаптация при критических проблемах."""
        if not self._performance_monitor:
            return

        current_time = time.time()
        if current_time - self.last_adaptation_time < self.adaptation_cooldown:
            return  # Слишком рано для новой адаптации

        state = self._performance_monitor.get_resource_state()

        if state == ResourceState.CRITICAL:
            logger.warning("Applying emergency performance adaptations")

            # Переключаемся на консервативный профиль
            self._performance_monitor.set_performance_profile("conservative")

            # Уменьшаем нагрузку в connection manager
            if self._connection_manager:
                # Можем добавить методы для снижения нагрузки
                pass

            # Очищаем кэш если нужно
            if self._cache_manager:
                cache_stats = self._cache_manager.get_stats()
                if cache_stats.total_size_mb > 500:  # Если кэш больше 500MB
                    await self._cache_manager.clear()
                    logger.info("Cache cleared due to memory pressure")

            self.last_adaptation_time = current_time

    async def _health_check_loop(self):
        """Периодическая проверка здоровья системы."""
        logger.debug("Health check loop started")
        loop_iteration = 0

        while not self._shutdown:
            loop_iteration += 1
            try:
                # Run health check first, then sleep
                try:
                    await self._perform_health_check()
                except Exception as e:
                    # Track failed health check attempts and log
                    self._failed_operations += 1
                    logger.error(f"Health check raised exception: {e}")
                await self._sleep(self._health_check_interval)
            except asyncio.CancelledError:
                logger.debug(
                    f"Health check loop cancelled after {loop_iteration} iterations"
                )
                break
            except Exception as e:
                logger.error(
                    f"Error in health check loop iteration {loop_iteration}: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                # Continue loop despite error - don't let one failure kill the loop
                self._failed_operations += 1

    async def _perform_health_check(self):
        """Выполнение проверки здоровья системы."""
        issues = []

        # Проверяем кэш-менеджер
        if self._cache_manager:
            try:
                # Простая проверка - попытка записи и чтения
                await self._cache_manager.set(
                    "health_check", {"timestamp": time.time()}
                )
                result = await self._cache_manager.get("health_check")
                if not result:
                    issues.append("Cache manager: read/write test failed")
            except Exception as e:
                issues.append(f"Cache manager error: {e}")

        # Проверяем connection manager
        if self._connection_manager:
            try:
                stats = self._connection_manager.get_pool_stats()
                # Проверяем что пулы отвечают
                if not stats:
                    issues.append("Connection manager: no pool stats available")
            except Exception as e:
                issues.append(f"Connection manager error: {e}")

        # Проверяем performance monitor
        if self._performance_monitor:
            try:
                current_metrics = self._performance_monitor.get_current_metrics()
                if not current_metrics:
                    issues.append("Performance monitor: no current metrics")
            except Exception as e:
                issues.append(f"Performance monitor error: {e}")

        if issues:
            logger.warning(f"Health check found issues: {'; '.join(issues)}")
        else:
            logger.debug("Health check passed")

    async def shutdown(self):
        """Корректное завершение работы всех систем."""
        if not self._initialized or self._shutdown:
            return

        logger.info("Shutting down core systems...")
        self._shutdown = True

        # Останавливаем background tasks runner (Phase 3 Task A.1)
        # TaskGroup автоматически отменит все задачи при выходе
        if self._task_group_runner:
            if not self._task_group_runner.done():
                logger.debug("Cancelling background task group runner...")
                self._task_group_runner.cancel()
                try:
                    await self._task_group_runner
                except asyncio.CancelledError:
                    logger.debug("Background task group runner cancelled successfully")
            else:
                logger.debug("Background task group runner already done")

        # Останавливаем компоненты в обратном порядке
        try:
            await shutdown_connection_manager()
            logger.info("Connection manager shut down")
        except Exception as e:
            logger.error(f"Error shutting down connection manager: {e}")

        try:
            await shutdown_cache_manager()
            logger.info("Cache manager shut down")
        except Exception as e:
            logger.error(f"Error shutting down cache manager: {e}")

        try:
            await shutdown_performance_monitor()
            logger.info("Performance monitor shut down")
        except Exception as e:
            logger.error(f"Error shutting down performance monitor: {e}")

        self._initialized = False
        logger.info("Core systems shutdown complete")

    async def _cleanup_on_failure(self):
        """Очистка при неудачной инициализации."""
        try:
            await self.shutdown()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_comprehensive_status(self) -> Dict[str, Any]:
        """Получение полного статуса всех систем."""
        if not self._initialized:
            return {"status": "not_initialized", "uptime": 0, "components": {}}

        uptime = time.time() - self._start_time

        status: Dict[str, Any] = {
            "status": "running",
            "uptime": uptime,
            "total_operations": self._total_operations,
            "successful_operations": self._successful_operations,
            "failed_operations": self._failed_operations,
            "success_rate": (
                self._successful_operations / self._total_operations
                if self._total_operations > 0
                else 1.0
            ),
            "components": {},
        }

        # Статус кэш-менеджера
        if self._cache_manager:
            try:
                cache_stats = self._cache_manager.get_stats()
                status["components"]["cache"] = {
                    "status": "active",
                    "strategy": self._cache_manager.strategy.value,
                    "total_size_mb": cache_stats.total_size_mb,
                    "hit_rate": cache_stats.hit_rate,
                    "compression_saves": cache_stats.compression_saves,
                }
            except Exception as e:
                status["components"]["cache"] = {"status": "error", "error": str(e)}

        # Статус connection manager
        if self._connection_manager:
            try:
                pool_stats = self._connection_manager.get_pool_stats()
                status["components"]["connection"] = {
                    "status": "active",
                    "pools": pool_stats,
                }
            except Exception as e:
                status["components"]["connection"] = {
                    "status": "error",
                    "error": str(e),
                }

        # Статус performance monitor
        if self._performance_monitor:
            try:
                perf_summary = self._performance_monitor.get_performance_summary()
                status["components"]["performance"] = {
                    "status": "active",
                    "resource_state": perf_summary["resource_state"],
                    "current_profile": perf_summary["current_profile"],
                    "active_alerts": perf_summary["active_alerts_count"],
                    "recommendations": len(perf_summary["recommendations"]),
                }
            except Exception as e:
                status["components"]["performance"] = {
                    "status": "error",
                    "error": str(e),
                }

        return status

    async def get_performance_recommendations(self) -> list[str]:
        """Получение рекомендаций по производительности."""
        if not self._performance_monitor:
            return ["Performance monitor not available"]

        return self._performance_monitor.get_performance_recommendations()

    def record_operation(self, success: bool):
        """Запись результата операции."""
        self._total_operations += 1
        if success:
            self._successful_operations += 1
        else:
            self._failed_operations += 1

    # Методы для получения менеджеров
    def get_cache_manager(self) -> Optional[CacheManager]:
        """Получение кэш-менеджера."""
        return self._cache_manager

    def get_connection_manager(self) -> Optional[ConnectionManager]:
        """Получение менеджера соединений."""
        return self._connection_manager

    def get_performance_monitor(self) -> Optional[PerformanceMonitor]:
        """Получение монитора производительности."""
        return self._performance_monitor

    # Настройки адаптации
    def enable_adaptation(self, enabled: bool = True):
        """Включение/выключение автоматической адаптации."""
        self.adaptation_enabled = enabled
        logger.info(f"Automatic adaptation {'enabled' if enabled else 'disabled'}")

    def set_adaptation_cooldown(self, seconds: float):
        """Установка периода cooldown для адаптации."""
        self.adaptation_cooldown = max(30.0, seconds)  # Минимум 30 секунд

    async def force_cache_cleanup(self):
        """Принудительная очистка кэша."""
        if self._cache_manager:
            await self._cache_manager.clear()
            logger.info("Cache forcibly cleared")

    def update_performance_profile(self, profile: str):
        """Обновить профиль производительности."""
        if profile not in ["conservative", "balanced", "aggressive"]:
            logger.warning(f"Unknown performance profile '{profile}', keeping current")
            return

        old_profile = self.performance_profile
        self.performance_profile = profile

        # Обновляем performance monitor если он инициализирован
        if self._performance_monitor:
            success = self._performance_monitor.set_performance_profile(profile)
            if success:
                logger.info(
                    f"Performance profile updated from {old_profile} to {profile}"
                )
            else:
                logger.warning(
                    f"Failed to update performance monitor profile to {profile}"
                )

        logger.info(f"Core manager performance profile set to: {profile}")

    def is_initialized(self) -> bool:
        """Проверка инициализации системы."""
        return self._initialized

    def get_uptime(self) -> float:
        """Получение времени работы системы."""
        return time.time() - self._start_time if self._initialized else 0.0


# Глобальный экземпляр
_core_manager: Optional[CoreSystemManager] = None


async def initialize_core_systems(
    config_path: Optional[Path] = None,
    performance_profile: str = "balanced",
) -> CoreSystemManager:
    """
    Инициализация core систем.

    Args:
        config_path: Путь к конфигурационным файлам
        performance_profile: Профиль производительности (conservative, balanced, aggressive)

    Returns:
        Экземпляр CoreSystemManager
    """
    global _core_manager

    if _core_manager is not None and _core_manager.is_initialized():
        logger.warning("Core systems already initialized")
        return _core_manager

    _core_manager = CoreSystemManager(
        config_path, performance_profile=performance_profile
    )
    success = await _core_manager.initialize()

    if not success:
        raise RuntimeError("Failed to initialize core systems")

    return _core_manager


async def shutdown_core_systems():
    """Завершение работы core систем."""
    global _core_manager
    if _core_manager:
        await _core_manager.shutdown()
        _core_manager = None


def get_core_manager() -> Optional[CoreSystemManager]:
    """Получение глобального экземпляра CoreSystemManager."""
    return _core_manager


def is_core_initialized() -> bool:
    """Проверка инициализации core систем."""
    return _core_manager is not None and _core_manager.is_initialized()
