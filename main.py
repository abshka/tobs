#!/usr/bin/env python3
"""
TOBS - Telegram Exporter to Markdown
Main entry point for the application.
"""

import asyncio
import signal
import sys

# Attempt to use uvloop for performance improvement
try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

import aiohttp
from rich import print as rprint

from src.config import Config
from src.core_manager import CoreSystemManager
from src.exceptions import ConfigError
from src.export.exporter import run_export
from src.media.manager import MediaProcessor
from src.note_generator import NoteGenerator
from src.telegram_client import TelegramManager
from src.telegram_sharded_client import ShardedTelegramManager
from src.ui.interactive import run_interactive_configuration
from src.utils import logger, setup_logging


def handle_sigint(signum, frame):
    """Handle SIGINT (Ctrl+C) signal."""
    rprint("\n[bold yellow]Received interrupt signal. Cleaning up...[/bold yellow]")
    sys.exit(0)


def print_comprehensive_summary(stats, performance_monitor, core_manager):
    """Print comprehensive export summary in old main.py format."""
    # Export summary
    rprint("\n[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint("[bold green]          СВОДКА ЭКСПОРТА[/bold green]")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint(f"[cyan]Всего сообщений:[/cyan] {stats.messages_processed}")
    rprint(f"[cyan]Всего медиафайлов:[/cyan] {stats.media_downloaded}")
    rprint(f"[cyan]Ошибок:[/cyan] {stats.errors_encountered}")
    rprint(f"[cyan]Общее время:[/cyan] {stats.duration:.1f}s")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]\n")

    # Time in minutes
    duration_minutes = stats.duration / 60
    rprint(f"Экспорт завершен за {duration_minutes:.1f} минут")

    # Resource usage
    if hasattr(stats, "peak_memory_mb") and stats.peak_memory_mb > 0:
        rprint(f"Пиковое использование памяти: {stats.peak_memory_mb:.1f}MB")
    elif performance_monitor:
        metrics = performance_monitor.get_current_metrics()
        if metrics:
            rprint(f"Пиковое использование памяти: {metrics.process_memory_mb:.1f}MB")

    if hasattr(stats, "avg_cpu_percent") and stats.avg_cpu_percent > 0:
        rprint(f"Среднее использование CPU: {stats.avg_cpu_percent:.1f}%")
    elif performance_monitor:
        metrics = performance_monitor.get_current_metrics()
        if metrics:
            rprint(f"Среднее использование CPU: {metrics.process_cpu_percent:.1f}%")

    # Core systems report
    if core_manager:
        cache_manager = core_manager.get_cache_manager()
        performance_monitor_obj = core_manager.get_performance_monitor()

        rprint("\nОтчет основных систем:")

        # Cache statistics
        if cache_manager and hasattr(cache_manager, "get_stats"):
            cache_stats = cache_manager.get_stats()
            hit_rate = cache_stats.hit_rate * 100
            cache_size_mb = cache_stats.total_size_mb
            rprint(f"✅ Попаданий в кэш: {hit_rate:.1f}%")
            rprint(f"✅ Размер кэша: {cache_size_mb:.1f}MB")

        # Compression statistics
        compression_saves = (
            cache_stats.compression_saves
            if cache_manager and hasattr(cache_manager, "get_stats")
            else 0
        )
        rprint(f"✅ Экономия от сжатия: {compression_saves}")
        rprint(f"✅ Всего операций: {stats.messages_processed}")

        # Success rate
        total_ops = stats.messages_processed
        success_rate = (
            ((total_ops - stats.errors_encountered) / total_ops * 100)
            if total_ops > 0
            else 100.0
        )
        rprint(f"✅ Уровень успеха: {success_rate:.1f}%")

        # Resource state and profile
        if performance_monitor_obj:
            metrics = performance_monitor_obj.get_current_metrics()
            if metrics:
                # Determine resource state
                if metrics.process_memory_mb > 3000 or metrics.process_cpu_percent > 80:
                    state = "перегружен"
                elif (
                    metrics.process_memory_mb > 2000 or metrics.process_cpu_percent > 60
                ):
                    state = "высокий"
                else:
                    state = "нормальный"
                rprint(f"✅ Состояние ресурсов: {state}")

            # Performance profile
            profile = (
                core_manager.performance_profile
                if hasattr(core_manager, "performance_profile")
                else "balanced"
            )
            rprint(f"✅ Профиль производительности: {profile}")

            # Active alerts
            active_alerts = performance_monitor_obj.get_active_alerts()
            rprint(f"⚠️  Активных предупреждений: {len(active_alerts)}")

        # Performance recommendations
        if performance_monitor_obj:
            active_alerts = performance_monitor_obj.get_active_alerts()
            if active_alerts:
                rprint("\nРекомендации по производительности:\n")
                for i, alert in enumerate(active_alerts[:3], 1):  # Показать топ 3
                    if "memory" in alert.metric_name.lower():
                        rprint(
                            f"{i}. Процесс использует много памяти. Рекомендуется перезапустить приложение."
                        )
                    elif "cpu" in alert.metric_name.lower():
                        rprint(
                            f"{i}. Высокая нагрузка на CPU. Рекомендуется снизить количество воркеров."
                        )
                    if state == "overloaded":
                        rprint(
                            f"{i + 1}. Рекомендуется переключиться на консервативный профиль производительности."
                        )
                        break

        # System uptime
        if performance_monitor_obj:
            metrics = performance_monitor_obj.get_current_metrics()
            if metrics:
                uptime = metrics.timestamp - (
                    stats.start_time
                    if hasattr(stats, "start_time")
                    else metrics.timestamp - stats.duration
                )
                uptime_minutes = uptime / 60
                rprint(f"\n✅ Время работы системы: {uptime_minutes:.1f} минут")

    rprint("\n[bold green]Экспорт TOBS завершен успешно![/bold green]\n")


async def async_main():
    """Async main entry point."""
    # Initialize variables for cleanup
    core_manager = None
    telegram_manager = None
    http_session = None
    media_processor = None
    note_generator = None

    # Setup signal handlers
    signal.signal(signal.SIGINT, handle_sigint)

    # Setup logging (default to INFO)
    setup_logging("INFO")

    # Load configuration from .env
    try:
        config = Config.from_env()
    except ConfigError as e:
        rprint(f"[bold red]Configuration error: {e}[/bold red]")
        rprint("Make sure .env file exists with API_ID and API_HASH")
        sys.exit(1)

    # Initialize systems for interactive mode
    core_manager = CoreSystemManager(
        config_path=config.export_path,
        performance_profile=config.performance_profile,
    )
    await core_manager.initialize()

    connection_manager = core_manager.get_connection_manager()

    telegram_manager = TelegramManager(
        config=config, connection_manager=connection_manager
    )
    await telegram_manager.connect()

    try:
        success = await run_interactive_configuration(config, telegram_manager)
        if success:
            # User selected "Start Export" - update core manager with new config
            core_manager.update_performance_profile(config.performance_profile)

            # 🚀 CRITICAL FIX: If sharding was enabled via menu, replace telegram_manager
            if config.enable_shard_fetch:
                rprint("[bold cyan]🚀 Switching to Sharded Telegram Manager...[/bold cyan]")
                # Create new ShardedTelegramManager with existing connection
                old_client = telegram_manager.client
                sharded_manager = ShardedTelegramManager(
                    config=config, connection_manager=connection_manager
                )
                # Reuse the existing connected client
                sharded_manager.client = old_client
                sharded_manager.client_connected = True
                sharded_manager.telegram_manager = telegram_manager  # Keep reference to base manager
                telegram_manager = sharded_manager
                
                # DEBUG: Verify the switch worked
                logger.info(f"✅ Switched to ShardedTelegramManager with {config.shard_count} workers")
                logger.info(f"🔍 telegram_manager type: {type(telegram_manager)}")
                logger.info(f"🔍 telegram_manager.__class__.__name__: {telegram_manager.__class__.__name__}")
                logger.info(f"🔍 Has fetch_messages: {hasattr(telegram_manager, 'fetch_messages')}")
                logger.info(f"🔍 fetch_messages method: {telegram_manager.fetch_messages}")

            # User selected "Start Export" - proceed with export
            rprint("\n[bold green]✓ Starting export...[/bold green]\n")

            # Reuse existing connections for export
            cache_manager = core_manager.get_cache_manager()
            connection_manager = core_manager.get_connection_manager()
            performance_monitor = core_manager.get_performance_monitor()

            # Initialize HTTP session with connection pooling
            connector = aiohttp.TCPConnector(
                limit=100,  # Total connection pool size
                limit_per_host=30,  # Connections per host
                ttl_dns_cache=300,  # DNS cache TTL (5 min)
            )
            http_session = aiohttp.ClientSession(connector=connector)

            try:
                # Initialize Media Processor
                rprint("[bold cyan]Initializing media processor...[/bold cyan]")
                media_processor = MediaProcessor(
                    config=config,
                    client=telegram_manager.client,
                    cache_manager=cache_manager,
                    connection_manager=connection_manager,
                    max_workers=config.performance.workers,
                    worker_clients=getattr(telegram_manager, "worker_clients", []),
                )
                await media_processor.start()

                # Initialize Note Generator
                note_generator = NoteGenerator(config=config)

                # Run export using the high-level orchestrator (supports Takeout)
                rprint("[bold cyan]Starting export process...[/bold cyan]")

                results = await run_export(
                    config=config,
                    telegram_manager=telegram_manager,
                    cache_manager=cache_manager,
                    media_processor=media_processor,
                    note_generator=note_generator,
                    http_session=http_session,
                    performance_monitor=performance_monitor,
                )

                # Display comprehensive summary for each target
                for stats in results:
                    print_comprehensive_summary(
                        stats, performance_monitor, core_manager
                    )

            finally:
                if note_generator:
                    await note_generator.shutdown()
                if http_session:
                    await http_session.close()
        else:
            rprint("[bold yellow]Configuration not changed[/bold yellow]")
    finally:
        await telegram_manager.disconnect()
        await core_manager.shutdown()


def main():
    """Main entry point."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        rprint("\n[bold yellow]Export cancelled by user[/bold yellow]")
        sys.exit(0)
    except Exception as e:
        rprint(f"[bold red]Fatal error: {e}[/bold red]")
        logger.exception("Fatal error in main")
        sys.exit(1)


if __name__ == "__main__":
    main()
