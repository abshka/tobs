#!/usr/bin/env python3
"""
TOBS - Telegram Exporter to Markdown
Main entry point for the application.
"""

import asyncio
import signal
import sys
from pathlib import Path

# Try to use uvloop for better performance
try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

import aiohttp
from rich import print as rprint

from src.config import Config, ExportTarget
from src.core_manager import CoreSystemManager
from src.exceptions import ConfigError, TelegramConnectionError
from src.export.exporter import Exporter, run_export
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
    """Print comprehensive export summary matching old main.py format."""
    # Export Summary
    rprint("\n[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint("[bold green]          EXPORT SUMMARY[/bold green]")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]")
    rprint(f"[cyan]Total Messages:[/cyan] {stats.messages_processed}")
    rprint(f"[cyan]Total Media Files:[/cyan] {stats.media_downloaded}")
    rprint(f"[cyan]Errors:[/cyan] {stats.errors_encountered}")
    rprint(f"[cyan]Total Duration:[/cyan] {stats.duration:.1f}s")
    rprint("[bold green]═══════════════════════════════════════════════[/bold green]\n")

    # Duration in minutes
    duration_minutes = stats.duration / 60
    rprint(f"Export completed in {duration_minutes:.1f} minutes")

    # Resource usage
    if hasattr(stats, "peak_memory_mb") and stats.peak_memory_mb > 0:
        rprint(f"Peak memory usage: {stats.peak_memory_mb:.1f}MB")
    elif performance_monitor:
        metrics = performance_monitor.get_current_metrics()
        if metrics:
            rprint(f"Peak memory usage: {metrics.process_memory_mb:.1f}MB")

    if hasattr(stats, "avg_cpu_percent") and stats.avg_cpu_percent > 0:
        rprint(f"Average CPU usage: {stats.avg_cpu_percent:.1f}%")
    elif performance_monitor:
        metrics = performance_monitor.get_current_metrics()
        if metrics:
            rprint(f"Average CPU usage: {metrics.process_cpu_percent:.1f}%")

    # Core System Report
    if core_manager:
        cache_manager = core_manager.get_cache_manager()
        performance_monitor_obj = core_manager.get_performance_monitor()

        rprint("\nCore System Report:")

        # Cache stats
        if cache_manager and hasattr(cache_manager, "get_stats"):
            cache_stats = cache_manager.get_stats()
            hit_rate = cache_stats.hit_rate * 100
            cache_size_mb = cache_stats.total_size_mb
            rprint(f"✅ Cache Hit Rate: {hit_rate:.1f}%")
            rprint(f"✅ Cache Size: {cache_size_mb:.1f}MB")

        # Compression stats
        compression_saves = (
            cache_stats.compression_saves
            if cache_manager and hasattr(cache_manager, "get_stats")
            else 0
        )
        rprint(f"✅ Compression Saves: {compression_saves}")
        rprint(f"✅ Total Operations: {stats.messages_processed}")

        # Success rate
        total_ops = stats.messages_processed
        success_rate = (
            ((total_ops - stats.errors_encountered) / total_ops * 100)
            if total_ops > 0
            else 100.0
        )
        rprint(f"✅ Success Rate: {success_rate:.1f}%")

        # Resource state and profile
        if performance_monitor_obj:
            metrics = performance_monitor_obj.get_current_metrics()
            if metrics:
                # Determine resource state
                if metrics.process_memory_mb > 3000 or metrics.process_cpu_percent > 80:
                    state = "overloaded"
                elif (
                    metrics.process_memory_mb > 2000 or metrics.process_cpu_percent > 60
                ):
                    state = "high"
                else:
                    state = "normal"
                rprint(f"✅ Resource State: {state}")

            # Performance profile
            profile = (
                core_manager.performance_profile
                if hasattr(core_manager, "performance_profile")
                else "balanced"
            )
            rprint(f"✅ Performance Profile: {profile}")

            # Active alerts
            active_alerts = performance_monitor_obj.get_active_alerts()
            rprint(f"⚠️  Active Alerts: {len(active_alerts)}")

        # Performance Recommendations
        if performance_monitor_obj:
            active_alerts = performance_monitor_obj.get_active_alerts()
            if active_alerts:
                rprint("\nPerformance Recommendations:\n")
                for i, alert in enumerate(active_alerts[:3], 1):  # Show top 3
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
                rprint(f"\n✅ System Uptime: {uptime_minutes:.1f} minutes")

    rprint("\n[bold green]TOBS export completed successfully![/bold green]\n")


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

    telegram_manager = ShardedTelegramManager(
        config=config, connection_manager=connection_manager
    )
    await telegram_manager.connect()

    try:
        success = await run_interactive_configuration(config, telegram_manager)
        if success:
            # User selected "Start Export" - update core manager with new config
            core_manager.update_performance_profile(config.performance_profile)

            # User selected "Start Export" - proceed with export
            rprint("\n[bold green]✓ Starting export...[/bold green]\n")

            # Reuse existing connections for export
            cache_manager = core_manager.get_cache_manager()
            connection_manager = core_manager.get_connection_manager()
            performance_monitor = core_manager.get_performance_monitor()

            # Initialize HTTP session with optimized connection pooling
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
