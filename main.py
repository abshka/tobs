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
from telethon import errors

from src.config import Config
from src.core_manager import CoreSystemManager
from src.exceptions import ConfigError
from src.export.exporter import TakeoutSessionWrapper, run_export
from src.media.manager import MediaProcessor
from src.note_generator import NoteGenerator
from src.session_gc import run_session_gc
from src.shutdown_manager import shutdown_manager
from src.telegram_client import TelegramManager
from src.telegram_sharded_client import ShardedTelegramManager
from src.ui.interactive import run_interactive_configuration
from src.utils import logger, setup_logging


async def precheck_takeout(config, telegram_manager):
    """
    Pre-check and initialize Takeout session before export.
    Returns True if Takeout is ready or not needed, False if user needs to grant permission.
    """
    if not config.use_takeout:
        return True  # Takeout not needed

    logger.info("🔍 Checking Takeout session status...")

    # 1. Check if we are already in a Takeout session (Reuse Strategy)
    current_client = telegram_manager.client
    existing_id = getattr(
        current_client,
        "takeout_id",
        getattr(current_client, "_takeout_id", None),
    )

    if existing_id:
        logger.info(
            f"♻️ Client is already in Takeout mode (ID: {existing_id}). Ready to export."
        )
        telegram_manager._external_takeout_id = existing_id
        return True

    # 2. Try to init new session
    try:
        logger.info("🚀 Attempting to initiate Telegram Takeout session...")
        rprint(
            "[bold yellow]⚠️  IMPORTANT: Please check your Telegram messages (Service Notifications) to ALLOW the Takeout request.[/bold yellow]"
        )
        rprint(
            "[bold cyan]ℹ️  Telegram will send you a notification asking to allow data export.[/bold cyan]"
        )
        rprint("[bold cyan]ℹ️  You have up to 5 minutes to approve it.[/bold cyan]")
        rprint(
            "[bold green]⏳ System will automatically check for confirmation every 5 seconds...[/bold green]"
        )

        # 🧹 Force-clear stale state blindly
        try:
            telegram_manager.client._takeout_id = None
        except Exception:
            pass

        # Try to initialize Takeout session (now with auto-retry)
        async with TakeoutSessionWrapper(
            telegram_manager.client, config
        ) as takeout_client:
            logger.info("✅ Takeout session established successfully!")
            takeout_id = takeout_client.takeout_id
            if takeout_id:
                logger.info(f"♻️ Takeout ID {takeout_id} ready for export")
            return True

    except errors.TakeoutInitDelayError as e:
        logger.warning(
            "⚠️  Takeout confirmation timeout - user did not approve within 5 minutes."
        )
        rprint("[bold red]❌ Takeout approval timeout![/bold red]")
        rprint(
            "[bold yellow]📱 You did not approve the Takeout request within 5 minutes.[/bold yellow]"
        )
        rprint(
            "[bold yellow]ℹ️  Please check Telegram → Service Notifications for the request.[/bold yellow]"
        )
        rprint(
            "[bold green]🔄 Run the export again and approve it faster.[/bold green]"
        )
        return False

    except Exception as e:
        logger.warning(f"⚠️  Takeout session failed: {e}")
        rprint(f"[bold red]❌ Takeout initialization failed: {e}[/bold red]")
        rprint(
            "[bold yellow]ℹ️  Falling back to Standard API (slower but works without Takeout)[/bold yellow]"
        )
        config.use_takeout = False  # Disable Takeout for this session
        return True


def handle_sigint(signum, frame):
    """Handle SIGINT (Ctrl+C) signal - delegate to ShutdownManager."""
    shutdown_manager.handle_sigint(signum, frame)


def aggregate_statistics(results):
    """
    Aggregate statistics from multiple export results into a single summary.

    Args:
        results: List of ExportStatistics objects

    Returns:
        Aggregated ExportStatistics object
    """
    from src.export.exporter import ExportStatistics

    if not results:
        return ExportStatistics()

    aggregated = ExportStatistics()

    # Use earliest start time and latest end time
    aggregated.start_time = min(s.start_time for s in results)

    # Handle case when no results have end_time set
    end_times = [s.end_time for s in results if s.end_time]
    aggregated.end_time = max(end_times) if end_times else None

    # Sum all counters
    aggregated.messages_processed = sum(s.messages_processed for s in results)
    aggregated.media_downloaded = sum(s.media_downloaded for s in results)
    aggregated.notes_created = sum(s.notes_created for s in results)
    aggregated.errors_encountered = sum(s.errors_encountered for s in results)
    aggregated.cache_hits = sum(s.cache_hits for s in results)
    aggregated.cache_misses = sum(s.cache_misses for s in results)

    # Average CPU and max memory across all exports
    valid_cpu = [s.avg_cpu_percent for s in results if s.avg_cpu_percent > 0]
    valid_mem = [s.peak_memory_mb for s in results if s.peak_memory_mb > 0]

    aggregated.avg_cpu_percent = sum(valid_cpu) / len(valid_cpu) if valid_cpu else 0.0
    aggregated.peak_memory_mb = max(valid_mem) if valid_mem else 0.0

    # Sum operation durations
    aggregated.messages_export_duration = sum(
        s.messages_export_duration for s in results
    )
    aggregated.media_download_duration = sum(s.media_download_duration for s in results)
    aggregated.transcription_duration = sum(s.transcription_duration for s in results)

    # Sum performance profiling fields (TIER A profiling)
    aggregated.time_api_requests = sum(
        s.time_api_requests for s in results if hasattr(s, "time_api_requests")
    )
    aggregated.time_processing = sum(
        s.time_processing for s in results if hasattr(s, "time_processing")
    )
    aggregated.time_file_io = sum(
        s.time_file_io for s in results if hasattr(s, "time_file_io")
    )
    aggregated.api_request_count = sum(
        s.api_request_count for s in results if hasattr(s, "api_request_count")
    )

    return aggregated


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

    # Performance profiling breakdown
    if hasattr(stats, "time_api_requests") and stats.time_api_requests > 0:
        rprint("\n[bold yellow]Детальная производительность:[/bold yellow]")
        total_tracked = (
            stats.time_api_requests + stats.time_processing + stats.time_file_io
        )

        rprint(
            f"  [cyan]⏱️  API запросы:[/cyan] {stats.time_api_requests:.1f}s ({stats.time_api_requests / stats.duration * 100:.1f}%)"
        )
        rprint(
            f"  [cyan]⚙️  Обработка:[/cyan] {stats.time_processing:.1f}s ({stats.time_processing / stats.duration * 100:.1f}%)"
        )
        rprint(
            f"  [cyan]💾 Запись на диск:[/cyan] {stats.time_file_io:.1f}s ({stats.time_file_io / stats.duration * 100:.1f}%)"
        )

        if hasattr(stats, "api_request_count") and stats.api_request_count > 0:
            avg_msg_per_request = stats.messages_processed / stats.api_request_count
            rprint(
                f"  [cyan]📊 API запросов:[/cyan] {stats.api_request_count} (avg {avg_msg_per_request:.1f} msgs/request)"
            )

        if stats.messages_processed > 0:
            rprint(
                f"  [cyan]⚡ Скорость:[/cyan] {stats.messages_processed / stats.duration:.1f} сообщений/сек"
            )

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

    # Apply LogBatcher adapter for Exporter lazy logging (safe, idempotent)
    # LogBatcher adapter removed; Exporter now uses the global_batcher singleton natively.

    # Load configuration from .env
    try:
        config = Config.from_env()
    except ConfigError as e:
        rprint(f"[bold red]Configuration error: {e}[/bold red]")
        rprint("Make sure .env file exists with API_ID and API_HASH")
        sys.exit(1)

    # Initialize TTY detection and output manager (TIER B - B-5)
    from src.ui.output_manager import initialize_output_manager
    from src.ui.tty_detector import initialize_tty_detector

    tty_detector = initialize_tty_detector(mode=config.tty_mode)
    output_manager = initialize_output_manager()

    logger.info(
        f"🎨 TTY mode: {tty_detector.get_mode_name()} (is_tty: {tty_detector.is_tty()})"
    )

    # Run session garbage collection (TIER A - Task 6)
    if config.session_gc_enabled:
        try:
            session_dir = "sessions"  # Default session directory
            active_session = (
                config.session_name.split("/")[-1]
                if "/" in config.session_name
                else config.session_name
            )

            logger.info("🧹 Running session garbage collection...")
            removed, errors = run_session_gc(
                session_dir=session_dir,
                max_age_days=config.session_gc_max_age_days,
                keep_last_n=config.session_gc_keep_last_n,
                active_session_name=active_session,
            )

            if removed > 0:
                rprint(f"[cyan]♻️  Cleaned up {removed} old session files[/cyan]")
        except Exception as e:
            logger.warning(f"Session GC failed (non-critical): {e}")

    # Initialize systems for interactive mode
    core_manager = CoreSystemManager(
        config_path=config.export_path,
        performance_profile=config.performance_profile,
    )
    await core_manager.initialize()

    connection_manager = core_manager.get_connection_manager()
    cache_manager = core_manager.get_cache_manager()

    telegram_manager = TelegramManager(
        config=config,
        connection_manager=connection_manager,
        cache_manager=cache_manager,
    )
    await telegram_manager.connect()

    try:
        success = await run_interactive_configuration(config, telegram_manager)
        if success:
            # User selected "Start Export" - update core manager with new config
            core_manager.update_performance_profile(config.performance_profile)

            # 🚀 CRITICAL FIX: If sharding was enabled via menu, replace telegram_manager
            if config.enable_shard_fetch:
                rprint(
                    "[bold cyan]🚀 Switching to Sharded Telegram Manager...[/bold cyan]"
                )
                # Create new ShardedTelegramManager with existing connection
                old_client = telegram_manager.client
                sharded_manager = ShardedTelegramManager(
                    config=config,
                    connection_manager=connection_manager,
                    cache_manager=cache_manager,
                )
                # Reuse the existing connected client
                sharded_manager.client = old_client
                sharded_manager.client_connected = True
                sharded_manager.telegram_manager = (
                    telegram_manager  # Keep reference to base manager
                )
                telegram_manager = sharded_manager

            # User selected "Start Export" - proceed with export
            rprint("\n[bold green]✓ Starting export...[/bold green]\n")

            # 🚀 PRECHECK TAKEOUT BEFORE EXPORT
            takeout_ready = await precheck_takeout(config, telegram_manager)
            if not takeout_ready:
                rprint(
                    "[bold yellow]ℹ️  Export cancelled. Please approve Takeout request and try again.[/bold yellow]"
                )
                return  # Exit without error

            # Reuse existing connections for export
            cache_manager = core_manager.get_cache_manager()
            connection_manager = core_manager.get_connection_manager()
            performance_monitor = core_manager.get_performance_monitor()

            # Initialize HTTP session with connection pooling
            connector = aiohttp.TCPConnector(
                limit=0,  # Total connection pool size
                limit_per_host=30,  # Connections per host
                ttl_dns_cache=300,  # DNS cache TTL (5 min)
                use_dns_cache=True,
                enable_cleanup_closed=True,
                force_close=False,  # Держим соединения открытыми (Keep-Alive)
            )

            # Security S-5: Split socket timeout (sock_read) and total timeout
            # sock_read=60s: prevents indefinite hanging on slow/stalled sockets
            # total=1800s: allows large file downloads (30 minutes max)
            timeout = aiohttp.ClientTimeout(
                total=1800,  # 30 minutes total
                sock_read=60,  # 60 seconds socket read timeout
                sock_connect=10,  # 10 seconds socket connect timeout
            )

            http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)

            # Register cleanup hooks for graceful shutdown (TIER A - Task 3)
            # These will be called when shutdown_requested is True
            shutdown_manager.register_async_cleanup_hook(
                lambda: telegram_manager.disconnect()
            )
            shutdown_manager.register_async_cleanup_hook(lambda: http_session.close())
            if core_manager:
                # CacheManager has async shutdown() method, not close()
                async def cleanup_cache():
                    cache_mgr = core_manager.get_cache_manager()
                    if cache_mgr:
                        await cache_mgr.shutdown()
                
                shutdown_manager.register_async_cleanup_hook(cleanup_cache)

            # Flush logs on shutdown
            from src.logging.global_batcher import global_batcher

            shutdown_manager.register_cleanup_hook(global_batcher.flush)

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

                # Display single comprehensive summary with aggregated statistics
                if results:
                    aggregated_stats = aggregate_statistics(results)
                    print_comprehensive_summary(
                        aggregated_stats, performance_monitor, core_manager
                    )
                else:
                    rprint("[yellow]No export results to display[/yellow]")

            finally:
                # Graceful shutdown: stop media processor first
                if media_processor:
                    await media_processor.shutdown()
                if note_generator:
                    await note_generator.shutdown()

                # Run graceful cleanup if shutdown was requested
                if shutdown_manager.shutdown_requested:
                    await shutdown_manager.run_graceful_cleanup()
                else:
                    # Normal cleanup path
                    if http_session:
                        await http_session.close()
        else:
            rprint("[bold yellow]Configuration not changed[/bold yellow]")
    finally:
        # Final cleanup - these are safe to call multiple times
        if telegram_manager and not shutdown_manager.shutdown_requested:
            # Only disconnect if not already handled by graceful cleanup
            await telegram_manager.disconnect()
        if core_manager:
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
