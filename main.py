import asyncio
import multiprocessing
import re
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiohttp
from aiohttp_proxy import ProxyConnector
from rich import print as rprint
from telethon.tl.types import Message

from src.cache_manager import CacheManager
from src.config import Config, ExportTarget, load_config
from src.exceptions import ConfigError, ExporterError, TelegramConnectionError
from src.media_processor import AIOHTTP_HEADERS, MediaProcessor
from src.note_generator import NoteGenerator
from src.telegram_client import TelegramManager
from src.utils import find_telegraph_links, logger, setup_logging

thread_executor: Optional[ThreadPoolExecutor] = None
process_executor: Optional[ProcessPoolExecutor] = None

def handle_sigint(signum, frame):
    """
    Handle SIGINT (Ctrl+C) signal by printing a message and exiting the program.

    Args:
        signum: Signal number.
        frame: Current stack frame.

    Returns:
        None
    """
    rprint("\n[bold yellow]Received interrupt signal. Cleaning up and exiting...[/bold yellow]")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_sigint)

async def process_message_group(
    messages: List[Message],
    entity_id: Union[str, int],
    entity_export_path: Path,
    entity_media_path: Path,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    cache_manager: CacheManager,
    config: Config,
    http_session: aiohttp.ClientSession,
    telegram_manager
) -> Optional[int]:
    """
    Process a group of Telegram messages, download and optimize media, create notes,
    handle telegra.ph links, and update the cache with processed message information.

    Args:
        messages: List of Telegram messages to process.
        entity_id: ID of the Telegram entity (channel/group/user).
        entity_export_path: Path to export notes for this entity.
        entity_media_path: Path to export media for this entity.
        media_processor: MediaProcessor instance for handling media downloads.
        note_generator: NoteGenerator instance for creating notes.
        cache_manager: CacheManager instance for tracking processed messages.
        config: Config object with export settings.
        http_session: aiohttp ClientSession for HTTP requests.
        telegram_manager: TelegramManager instance.

    Returns:
        The ID of the first processed message, or None if processing failed.
    """
    if not messages:
        return None

    first_message = messages[0]

    try:
        media_paths = []
        if config.media_download:
            for msg in messages:
                filename = None
                if hasattr(msg, "file") and msg.file and hasattr(msg.file, "name") and msg.file.name:
                    filename = msg.file.name
                elif hasattr(msg, "media") and hasattr(msg.media, "document") and hasattr(msg.media.document, "attributes"):
                    for attr in msg.media.document.attributes:
                        if hasattr(attr, "file_name"):
                            filename = attr.file_name
                            break
                if not filename:
                    filename = f"media_from_msg_{msg.id}"
                result = await media_processor.download_and_optimize_media(msg, entity_id, entity_media_path)
                if isinstance(result, list):
                    media_paths.extend(result)

        note_path = await note_generator.create_note(
            first_message, media_paths, entity_id, entity_export_path,
            client=telegram_manager.get_client() if hasattr(telegram_manager, "get_client") else None,
            export_comments=True,
            entity_media_path=entity_media_path
        )
        if not note_path:
            logger.error(f"[{entity_id}] Failed to create main note for message {first_message.id}")
            return None

        telegraph_links = find_telegraph_links(first_message.text)
        if telegraph_links:
            original_content = await note_generator.read_note_content(note_path)
            modified_content = original_content

            telegraph_mapping = {}

            for link in telegraph_links:
                article_note_path = await note_generator.create_note_from_telegraph_url(
                    session=http_session,
                    url=link,
                    notes_export_path=entity_export_path,
                    media_export_path=entity_media_path,
                    media_processor=media_processor,
                    cache=cache_manager.cache,
                    entity_id=str(entity_id),
                    telegraph_mapping=telegraph_mapping
                )
                if article_note_path:
                    local_link = f"[[{article_note_path.stem}]]"
                    modified_content = modified_content.replace(link, local_link)

            def telegraph_replacer(match):
                """
                Replace a telegra.ph URL in the note content with a local note link if available.

                Args:
                    match: Regex match object for a telegra.ph URL.

                Returns:
                    The local note link if found, otherwise the original URL.
                """
                url = match.group(0)
                note_stem = telegraph_mapping.get(url)
                return f"[[{note_stem}]]" if note_stem else url

            for link, note_stem in telegraph_mapping.items():
                modified_content = modified_content.replace(link, f"[[{note_stem}]]")

            if modified_content != original_content:
                await note_generator.write_note_content(note_path, modified_content)

        note_filename = note_path.name
        reply_to_id = getattr(first_message.reply_to, 'reply_to_msg_id', None)

        for message in messages:
            channel_id = getattr(message.peer_id, 'channel_id', None)
            telegram_url = f"https://t.me/c/{channel_id}/{message.id}" if channel_id else None

            if cache_manager is not None:
                await cache_manager.add_processed_message_async(
                    message_id=message.id,
                    note_filename=note_filename,
                    reply_to_id=reply_to_id if message.id == first_message.id else None,
                    entity_id=entity_id,
                    title=(message.text or "").split('\n', 1)[0].strip(),
                    telegram_url=telegram_url
                )

        return first_message.id

    except Exception as e:
        logger.error(f"[{entity_id}] Critical failure in process_message_group for msg {first_message.id}: {e}", exc_info=(config.log_level == 'DEBUG'))
        return None

async def export_single_target(
    target: ExportTarget, config: Config, telegram_manager: TelegramManager,
    cache_manager: CacheManager, media_processor: MediaProcessor, note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession
):
    """
    Export all messages and media for a single export target (Telegram entity).

    Args:
        target: ExportTarget object representing the Telegram entity to export.
        config: Config object with export settings.
        telegram_manager: TelegramManager instance for Telegram API access.
        cache_manager: CacheManager instance for tracking processed messages.
        media_processor: MediaProcessor instance for handling media downloads.
        note_generator: NoteGenerator instance for creating notes.
        http_session: aiohttp ClientSession for HTTP requests.

    Returns:
        None
    """
    entity_id_str = str(target.id)

    try:
        entity = await telegram_manager.resolve_entity(target.id)
        if not entity:
            logger.error(f"Could not resolve entity for target ID: {target.id}. Skipping.")
            return

        target.name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))
        target.id = entity.id
        entity_id_str = str(target.id)

        if cache_manager is not None:
            await cache_manager.update_entity_info_async(entity_id_str, target.name, target.type)

        entity_export_path = config.get_export_path_for_entity(entity_id_str)
        entity_media_path = config.get_media_path_for_entity(entity_id_str)

        from src.utils import ensure_dir_exists
        ensure_dir_exists(entity_export_path)
        ensure_dir_exists(entity_media_path)

        last_processed_id = cache_manager.get_last_processed_message_id(entity_id_str) if (config.only_new and cache_manager is not None) else None
        if last_processed_id:
            rprint(f"[{target.name}] Incremental mode. Starting after message ID: {last_processed_id}")

        await process_entity_messages(
            entity, entity_id_str, target.name, entity_export_path, entity_media_path,
            last_processed_id, config, telegram_manager, cache_manager, media_processor, note_generator,
            http_session, target, telegram_manager
        )

    except (ExporterError, TelegramConnectionError) as e:
        logger.error(f"[{target.name}] Export failed: {e}")
    except Exception as e:
        logger.critical(f"[{target.name}] Critical error during export: {e}", exc_info=True)
    finally:
        if not (getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None):
            if cache_manager is not None:
                await cache_manager.save_cache()

async def process_entity_messages(
    entity: Any, entity_id_str: str, target_name: str, entity_export_path: Path,
    entity_media_path: Path, last_processed_id: Optional[int], config: Config,
    telegram_manager: TelegramManager, cache_manager: CacheManager,
    media_processor: MediaProcessor, note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession,
    export_target: Any = None,
    telegram_manager_pass=None
):
    """
    Fetch and process all messages for a given Telegram entity, grouping messages as needed,
    and handling concurrency and cache updates.

    Args:
        entity: Telegram entity object (channel/group/user).
        entity_id_str: String ID of the entity.
        target_name: Name of the export target.
        entity_export_path: Path to export notes for this entity.
        entity_media_path: Path to export media for this entity.
        last_processed_id: ID of the last processed message for incremental export, or None.
        config: Config object with export settings.
        telegram_manager: TelegramManager instance for Telegram API access.
        cache_manager: CacheManager instance for tracking processed messages.
        media_processor: MediaProcessor instance for handling media downloads.
        note_generator: NoteGenerator instance for creating notes.
        http_session: aiohttp ClientSession for HTTP requests.
        export_target: Export target object, can be None.
        telegram_manager_pass: Optional TelegramManager instance.

    Returns:
        None
    """
    semaphore = asyncio.Semaphore(config.max_workers)
    active_tasks = set()
    processed_count, successful_count = 0, 0
    grouped_messages: Dict[int, List[Message]] = {}
    GROUP_TIMEOUT = 0.5
    last_message_time = asyncio.get_event_loop().time()

    async def _process_group(group_id: int):
        """
        Process a group of messages identified by group_id, using concurrency control.

        Args:
            group_id: The ID of the message group to process.

        Returns:
            None
        """
        nonlocal successful_count
        messages = grouped_messages.pop(group_id, [])
        if not messages:
            return

        async with semaphore:
            task = asyncio.create_task(
                process_message_group(
                    messages, entity_id_str, entity_export_path, entity_media_path,
                    media_processor, note_generator, cache_manager, config,
                    http_session, telegram_manager_pass or telegram_manager
                )
            )
            active_tasks.add(task)

            def task_done_callback(fut: asyncio.Task):
                nonlocal successful_count
                if fut.exception():
                    logger.error(f"Task for group {group_id} failed: {fut.exception()}")
                elif fut.result() is not None:
                    successful_count += 1
                active_tasks.discard(fut)

            task.add_done_callback(task_done_callback)

    try:
        if export_target and getattr(export_target, "message_id", None) is not None:
            msg = await telegram_manager.client.get_messages(entity, ids=export_target.message_id)
            if msg:
                grouped_messages[msg.id] = [msg]
                await _process_group(msg.id)
                processed_count += 1
            else:
                logger.error(f"[{target_name}] Message with ID {export_target.message_id} not found.")
        else:
            async for message in telegram_manager.fetch_messages(entity=entity, min_id=last_processed_id):
                processed_count += 1
                current_time = asyncio.get_event_loop().time()

                if current_time - last_message_time > GROUP_TIMEOUT:
                    for gid in list(grouped_messages.keys()):
                        await _process_group(gid)

                group_id = getattr(message, 'grouped_id', None)
                if group_id:
                    grouped_messages.setdefault(group_id, []).append(message)
                else:
                    if grouped_messages:
                        for gid in list(grouped_messages.keys()):
                            await _process_group(gid)
                    grouped_messages[message.id] = [message]
                    await _process_group(message.id)
                last_message_time = current_time

                if processed_count % config.cache_save_interval == 0 and cache_manager is not None:
                    await cache_manager.schedule_background_save()

            for gid in list(grouped_messages.keys()):
                await _process_group(gid)

        if active_tasks:
            await asyncio.gather(*active_tasks)

    except Exception as e:
        logger.error(f"[{target_name}] Error during message processing loop: {e}", exc_info=(config.log_level == 'DEBUG'))

async def run_export(config: Config):
    """
    Main export routine. Sets up executors, managers, and sessions, and coordinates the
    export process for all configured targets, including post-processing and link replacement.

    Args:
        config: Config object with export settings.

    Returns:
        None
    """
    global thread_executor, process_executor
    telegram_manager = None
    try:
        cpu_count = multiprocessing.cpu_count() or 1
        process_workers = max(1, min(cpu_count, config.max_process_workers))
        thread_workers = config.max_workers
        process_executor = ProcessPoolExecutor(max_workers=process_workers)
        thread_executor = ThreadPoolExecutor(max_workers=thread_workers)
        asyncio.get_event_loop().set_default_executor(thread_executor)

        telegram_manager = TelegramManager(config)
        await telegram_manager.connect()

        media_processor = MediaProcessor(config, telegram_manager.get_client())
        note_generator = NoteGenerator(config)

        reply_linker = None
        cache_manager = None

        if config.interactive_mode:
            await interactive_config_update(config)
            await telegram_manager.run_interactive_selection()
            if not config.export_targets:
                logger.warning("No targets selected. Exiting.")
                return

        if not config.export_targets:
            logger.error("No export targets specified. Exiting.")
            return

        connector = None
        if config.proxy_type and config.proxy_addr and config.proxy_port:
            proxy_url = f"{config.proxy_type.lower()}://{config.proxy_addr}:{config.proxy_port}"
            connector = ProxyConnector.from_url(proxy_url)

        async with aiohttp.ClientSession(headers=AIOHTTP_HEADERS, connector=connector) as http_session:
            export_summaries = []

            rprint("[cyan]***Export***[/cyan]")
            for target in config.export_targets:
                if getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None:
                    local_cache_manager = None
                else:
                    from src.cache_manager import CacheManager
                    local_cache_manager = CacheManager(config.cache_file)
                    await local_cache_manager.load_cache()
                    config.cache = local_cache_manager.cache
                    config.cache_manager = local_cache_manager
                await export_single_target(
                    target, config, telegram_manager, local_cache_manager,
                    media_processor, note_generator, http_session
                )
                entity_id_str = str(target.id)
                export_root = config.get_export_path_for_entity(entity_id_str)
                summary = {
                    "name": target.name,
                    "id": entity_id_str,
                    "export_path": str(export_root),
                }
                export_summaries.append(summary)

            rprint("[magenta]***Downloading posts/media***[/magenta]")
            rprint("[magenta]Downloading posts/media complete.[/magenta]")

            rprint("[green]***Post-processing***[/green]")
            for target in config.export_targets:
                if getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None:
                    continue
                entity_id_str = str(target.id)
                export_root = config.get_export_path_for_entity(entity_id_str)

                await reply_linker.link_replies(entity_id_str, export_root)
                await note_generator.postprocess_all_notes(export_root, entity_id_str, cache_manager.cache)
            rprint("[green]Post-processing complete.[/green]")


            telegraph_mapping = {}
            for target in config.export_targets:
                entity_id_str = str(target.id)
                export_root = config.get_export_path_for_entity(entity_id_str)
                telegraph_dir = Path(export_root) / 'telegra_ph'
                if telegraph_dir.exists():
                    for note_file in telegraph_dir.glob("*.md"):
                        telegraph_url = None
                        with open(note_file, "r", encoding="utf-8") as f:
                            content = f.read()
                            match = re.search(r"\*Source: (https?://telegra\.ph/[^\*]+)\*", content)
                            if match:
                                telegraph_url = match.group(1).strip()
                        if telegraph_url:
                            telegraph_mapping[telegraph_url] = note_file.stem
            for target in config.export_targets:
                entity_id_str = str(target.id)
                export_root = config.get_export_path_for_entity(entity_id_str)
                for note_file in Path(export_root).rglob("*.md"):
                    with open(note_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    modified = content
                    for url, note_stem in telegraph_mapping.items():
                        modified = re.sub(rf"\[([^\]]+)\]\({re.escape(url)}\)", rf"[[{note_stem}|\1]]", modified)
                        modified = modified.replace(url, f"[[{note_stem}]]")
                    if modified != content:
                        with open(note_file, "w", encoding="utf-8") as f:
                            f.write(modified)

    except (ConfigError, TelegramConnectionError) as e:
        logger.critical(f"A critical error occurred: {e}")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
    finally:
        if telegram_manager and telegram_manager.client_connected:
            await telegram_manager.disconnect()
        if thread_executor:
            thread_executor.shutdown(wait=False)
        if process_executor:
            process_executor.shutdown(wait=False)

def prompt_int(prompt, default):
    """
    Prompt user for integer input with a default value.

    Args:
        prompt: The prompt string to display.
        default: The default integer value.

    Returns:
        int: The user input as integer, or default if invalid.
    """
    try:
        rprint(f"[bold]{prompt}[/bold] [dim][{default}][/dim]", end=" ")
        val = input().strip()
        return int(val) if val else default
    except Exception:
        rprint("[red]Invalid input, using default.[/red]")
        return default

def prompt_float(prompt, default):
    """
    Prompt user for float input with a default value.

    Args:
        prompt: The prompt string to display.
        default: The default float value.

    Returns:
        float: The user input as float, or default if invalid.
    """
    try:
        rprint(f"[bold]{prompt}[/bold] [dim][{default}][/dim]", end=" ")
        val = input().strip()
        return float(val) if val else default
    except Exception:
        rprint("[red]Invalid input, using default.[/red]")
        return default

async def interactive_config_update(config):
    """
    Interactive menu for advanced config options before export.

    Args:
        config: Config object to update.

    Returns:
        None
    """
    from src.utils import clear_screen
    while True:
        logger.error("TEST ERROR TO LOG FILE ONLY")
        clear_screen()
        rprint("[bold yellow]Advanced Config Options:[/bold yellow]")
        rprint(" [cyan]1.[/cyan] Throttle threshold (KB/s): [green]{}[/green]".format(getattr(config, 'throttle_threshold_kbps', 50)))
        rprint(" [cyan]2.[/cyan] Throttle pause (s): [green]{}[/green]".format(getattr(config, 'throttle_pause_s', 30)))
        rprint(" [cyan]3.[/cyan] Max workers (threads): [green]{}[/green]".format(getattr(config, 'max_workers', 8)))
        rprint(" [cyan]4.[/cyan] Max process workers: [green]{}[/green]".format(getattr(config, 'max_process_workers', 4)))
        rprint(" [cyan]5.[/cyan] Concurrent downloads: [green]{}[/green]".format(getattr(config, 'concurrent_downloads', 10)))
        rprint(" [cyan]6.[/cyan] Continue to export")
        rprint(" [cyan]7.[/cyan] Exit")
        choice = input("Choose an option to change (1-7): ").strip()
        if choice == "1":
            config.throttle_threshold_kbps = prompt_int("Throttle threshold (KB/s)", getattr(config, 'throttle_threshold_kbps', 50))
        elif choice == "2":
            config.throttle_pause_s = prompt_int("Throttle pause (s)", getattr(config, 'throttle_pause_s', 30))
        elif choice == "3":
            config.max_workers = prompt_int("Max workers (threads)", getattr(config, 'max_workers', 8))
        elif choice == "4":
            config.max_process_workers = prompt_int("Max process workers", getattr(config, 'max_process_workers', 4))
        elif choice == "5":
            config.concurrent_downloads = prompt_int("Concurrent downloads", getattr(config, 'concurrent_downloads', 10))
        elif choice == "6":
            break
        elif choice == '7':
            rprint("[yellow]Exiting...[/yellow]")
            sys.exit(0)
        else:
            rprint("[red]Invalid choice. Please select 1-6.[/red]")

async def main():
    """
    Main async entry point for the exporter. Loads configuration, sets up logging,
    and runs the export process. Handles startup errors and keyboard interrupts.

    Args:
        None

    Returns:
        None
    """
    try:
        config = load_config()
        setup_logging(config.log_level)
        logger.info("Configuration loaded.")
        await run_export(config)
    except (ConfigError, ValueError) as e:
        rprint(f"[red]Startup error: {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        rprint("\n[bold yellow]Received interrupt signal. Cleaning up and exiting...[/bold yellow]")
        sys.exit(0)
    except Exception as e:
        try:
            logger.critical(f"A fatal error occurred in main: {e}", exc_info=True)
        except NameError:
            rprint(f"[red]FATAL ERROR: {e}[/red]", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        rprint("[yellow]Process interrupted by user (KeyboardInterrupt).[/yellow]")
    except Exception as e:
        rprint(f"\nFATAL UNHANDLED ERROR: {e}", file=sys.stderr)
        rprint("Security error while unpacking a received message: Server replied with a wrong session ID (see FAQ for details)")
        rprint("If you see repeated 'wrong session ID' errors, try the following:")
        rprint("- Restart the export with a fresh session.")
        rprint("- Ensure only one client is connected with the same session at a time.")
        rprint("- Check for updates to Telethon.")
