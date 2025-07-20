import asyncio
import math
import re
import signal
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional

import aiofiles
import aiohttp
from aiohttp_proxy import ProxyConnector
from rich import print as rprint
from rich.progress import BarColumn, Live, Progress, TextColumn
from telethon.tl.types import Message

from src.cache_manager import CacheManager
from src.config import Config, ExportTarget
from src.exceptions import ConfigError, TelegramConnectionError
from src.media_processor import AIOHTTP_HEADERS, MediaProcessor
from src.note_generator import NoteGenerator
from src.telegram_client import TelegramManager
from src.utils import (
    clear_screen,
    ensure_dir_exists,
    find_telegraph_links,
    logger,
    setup_logging,
)

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
    entity_id_str: str,
    target_name: str,
    entity_export_path: Path,
    entity_media_path: Path,
    last_processed_id,
    config: Config,
    telegram_manager,
    cache_manager,
    media_processor,
    note_generator,
    http_session,
    export_target=None,
    telegram_manager_pass=None,
    progress=None,
    post_task_id=None
) -> Optional[int]:
    """
    Process a group of Telegram messages, download and optimize media in parallel, create notes,
    handle telegra.ph links, and update the cache with processed message information.

    Args:
        messages: List of Telegram messages to process.
        entity_id_str: String ID of the Telegram entity (channel/group/user).
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
        progress: Progress instance for progress bars.
        post_task_id: Progress task ID for this post.

    Returns:
        The ID of the first processed message, or None if processing failed.
    """
    if not messages:
        return None

    first_message = messages[0]

    try:
        entity_id = int(entity_id_str)
        media_paths = []
        semaphore = asyncio.Semaphore(getattr(config, "workers", 8))
        all_media_messages = list(messages)
        comments = []
        telegram_client = telegram_manager.get_client() if hasattr(telegram_manager, "get_client") else None
        if telegram_client is not None:
            channel_id = getattr(first_message, "chat_id", None)
            if channel_id is None and getattr(first_message, "to_id", None):
                channel_id = getattr(first_message.to_id, "channel_id", None)
            if channel_id is not None:
                try:
                    async for comment in telegram_client.iter_messages(channel_id, reply_to=first_message.id, reverse=True):
                        all_media_messages.append(comment)
                        comments.append(comment)
                except Exception as e:
                    if "The message ID used in the peer was invalid" in str(e):
                        pass
                    else:
                        rprint(f"[yellow]Комментариев для поста {first_message.id} получить не удалось: {e}[/yellow]")
        media_messages = []
        for msg in messages:
            if hasattr(msg, "media") and msg.media:
                media_messages.append(msg)
        for comment in comments:
            if hasattr(comment, "media") and comment.media:
                media_messages.append(comment)

        unique_media_messages = list({msg.id: msg for msg in media_messages}.values())

        total_media_size = 0
        for msg in unique_media_messages:
            size = 0
            if hasattr(msg, "media") and msg.media:
                if hasattr(msg.media, 'document') and hasattr(msg.media.document, 'size'):
                    size = msg.media.document.size
                elif hasattr(msg.media, 'photo') and hasattr(msg.media.photo, 'sizes'):
                    photo_sizes = [s.size for s in msg.media.photo.sizes if hasattr(s, 'size') and s.size]
                    if photo_sizes:
                        size = max(photo_sizes)
            total_media_size += size if size else 0

        task_total = total_media_size + 1

        task_id = post_task_id
        if progress is not None and task_id is not None:
            progress.update(
                task_id,
                total=task_total,
                description=f"Пост {first_message.id}"
            )

        async def download_media_task(msg):
            async with semaphore:
                for attempt in range(3):
                    try:
                        result = await media_processor.download_and_optimize_media(
                            msg, entity_id, entity_media_path, progress=progress, task_id=task_id
                        )
                        return result
                    except Exception as e:
                        rprint(f"[red][{entity_id_str}] Ошибка при скачивании медиа (попытка {attempt+1}/3): {e}[/red]")
                        await asyncio.sleep(1)

                rprint(f"[red][{entity_id_str}] Не удалось скачать медиа после 3 попыток для сообщения {msg.id}[/red]")

                size_on_fail = 0
                if hasattr(msg, "media") and msg.media:
                    if hasattr(msg.media, 'document') and hasattr(msg.media.document, 'size'):
                        size_on_fail = msg.media.document.size or 0
                    elif hasattr(msg.media, 'photo') and hasattr(msg.media.photo, 'sizes'):
                        photo_sizes = [s.size for s in msg.media.photo.sizes if hasattr(s, 'size') and s.size]
                        if photo_sizes:
                            size_on_fail = max(photo_sizes)
                if progress is not None and task_id is not None and size_on_fail > 0:
                    progress.update(task_id, advance=size_on_fail)

                return None

        results = []
        try:
            tasks = [download_media_task(msg) for msg in unique_media_messages]
            results = await asyncio.gather(*tasks)
        except Exception as e:
            rprint(f"[red][{entity_id_str}] Ошибка при скачивании группы медиа: {e}[/red]")
            results = []
        for result in results:
            if isinstance(result, list):
                media_paths.extend(result)
            elif result:
                media_paths.append(result)

        note_generator.progress = progress

        try:
            note_path = await note_generator.create_note(
                first_message, media_paths, entity_id, entity_export_path,
                client=telegram_manager.get_client() if hasattr(telegram_manager, "get_client") else None,
                export_comments=True,
                entity_media_path=entity_media_path
            )
        except Exception as e:
            logger.error(f"[{entity_id_str}] Failed to create main note for message {first_message.id}:{e}")
            if progress is not None and task_id is not None:
                progress.update(task_id, advance=1, description=f"Пост {first_message.id}: Ошибка")
            return None
        if not note_path:
            if progress is not None and task_id is not None:
                progress.update(task_id, advance=1, description=f"Пост {first_message.id}: Ошибка")
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
                    entity_id=entity_id_str,
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
                    entity_id=entity_id_str,
                    title=(message.text or "").split('\n', 1)[0].strip(),
                    telegram_url=telegram_url
                )

        if progress is not None and task_id is not None:
            progress.update(task_id, advance=1, description=f"Пост {first_message.id}: Готово")

        return first_message.id

    except Exception as e:
        rprint(f"[bold red][{entity_id_str}] Critical failure in process_message_group for msg {getattr(first_message, 'id', 'unknown')}:[/bold red] {e}")
        return None

async def export_single_target(
    target: ExportTarget, config: Config, telegram_manager: TelegramManager,
    cache_manager: CacheManager, media_processor: MediaProcessor, note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession, progress=None, post_task_id=None
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
        progress: Rich Progress instance for unified progress bar.

    Returns:
        None
    """
    entity_id_str = str(target.id)

    try:
        entity = await telegram_manager.resolve_entity(target.id)
        if not entity:
            rprint(f"[bold red]Could not resolve entity for target ID: {target.id}. Skipping.[/bold red]")
            return

        target.name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))
        target.id = entity.id
        entity_id_str = str(target.id)

        if cache_manager is not None:
            await cache_manager.update_entity_info_async(entity_id_str, target.name, target.type)

        entity_export_path = config.get_export_path_for_entity(entity_id_str)
        entity_media_path = config.get_media_path_for_entity(entity_id_str)

        ensure_dir_exists(entity_export_path)
        ensure_dir_exists(entity_media_path)

        last_processed_id = cache_manager.get_last_processed_message_id(entity_id_str) if (getattr(config, "only_new", False) and cache_manager is not None) else None
        if last_processed_id:
            rprint(f"[bold cyan][{target.name}] Incremental mode. Starting after message ID: {last_processed_id}[/bold cyan]")

        grouped_messages = {}
        semaphore = asyncio.Semaphore(getattr(config, "workers", 8))
        active_tasks = set()
        successful_count = 0
        processed_count = 0
        last_message_time = 0
        GROUP_TIMEOUT = 2.0

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
                        http_session, telegram_manager,
                        progress=progress,
                        post_task_id=post_task_id
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
            if getattr(target, "type", None) == "single_post":
                messages_to_process = getattr(config, "_single_post_messages_to_process", None)
                if messages_to_process:
                    await process_message_group(
                        messages_to_process, entity_id_str, entity_export_path, entity_media_path,
                        media_processor, note_generator, cache_manager, config,
                        http_session, telegram_manager,
                        progress=progress,
                        post_task_id=post_task_id
                    )
                    return
                else:
                    return

            if getattr(target, "message_id", None) is not None:
                msg = await telegram_manager.client.get_messages(entity, ids=target.message_id)
                if msg:
                    grouped_messages[msg.id] = [msg]
                    await _process_group(msg.id)
                    processed_count += 1
                else:
                    logger.error(f"[{target.name}] Message with ID {target.message_id} not found.")
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

                    if processed_count % getattr(config, "cache_save_interval", 10) == 0 and cache_manager is not None:
                        await cache_manager.schedule_background_save()

                for gid in list(grouped_messages.keys()):
                    await _process_group(gid)

            if active_tasks:
                await asyncio.gather(*active_tasks)

        except Exception as e:
            logger.error(f"[{target.name}] Error during message processing loop: {e}", exc_info=(getattr(config, "log_level", "INFO") == 'DEBUG'))
    finally:
        if not (getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None):
            if cache_manager is not None:
                await cache_manager.save_cache()

async def run_export(config: Config):
    """
    Main export routine. Sets up executors, managers, and sessions, and coordinates the
    export process for all configured targets, including post-processing and link replacement.

    Args:
        config: Config object with export settings.

    Returns:
        None
    """
    global process_executor
    telegram_manager = None
    try:
        # ProcessPoolExecutor: только для CPU-bound задач (например, обработка изображений/видео)
        telegram_manager = TelegramManager(config)
        await telegram_manager.connect()

        media_processor = MediaProcessor(config, telegram_manager.get_client())
        note_generator = NoteGenerator(config)

        reply_linker = None
        cache_manager = None

        if getattr(config, "interactive_mode", False):
            await interactive_config_update(config)
            while True:
                config.export_targets.clear()
                await telegram_manager.run_interactive_selection()
                if not config.export_targets:
                    logger.warning("No targets selected. Returning to main menu.")
                    continue

                connector = None
                if getattr(config, "proxy_type", None) and getattr(config, "proxy_addr", None) and getattr(config, "proxy_port", None):
                    proxy_url = f"{config.proxy_type.lower()}://{config.proxy_addr}:{config.proxy_port}"
                    connector = ProxyConnector.from_url(proxy_url)

                async with aiohttp.ClientSession(headers=AIOHTTP_HEADERS, connector=connector) as http_session:
                    export_summaries = []

                    post_task_ids = {}
                    post_filenames = {}

                    # Получаем entity (канал) и список сообщений (постов)
                    target = config.export_targets[0]
                    entity_id_str = str(target.id)
                    local_cache_manager = None
                    if getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None:
                        local_cache_manager = None
                    else:
                        local_cache_manager = CacheManager(config.cache_file)
                        await local_cache_manager.load_cache()
                        config.cache = local_cache_manager.cache
                        config.cache_manager = local_cache_manager

                    entity = await telegram_manager.resolve_entity(target.id)
                    if not entity:
                        rprint(f"[bold red]Could not resolve entity for target ID: {target.id}. Skipping.[/bold red]")
                        continue

                    # Потоковая асинхронная обработка сообщений (без предварительной загрузки всех сообщений)
                    concurrency = getattr(config, "workers", 8)
                    batch_size = concurrency * 2
                    # Получаем количество постов для общего прогресса
                    try:
                        total_count = await telegram_manager.client.get_messages(entity, limit=0)
                        total_posts = total_count.total
                    except Exception:
                        total_posts = None
                    rprint("[cyan]***Export started***[/cyan]")
                    rprint("[magenta]***Downloading posts/media***[/magenta]")

                    # --- Ветвление для экспорта одного поста ---
                    if getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None:
                        # Получаем сам пост
                        post_id = getattr(target, "message_id", None)
                        if post_id is not None:
                            single_post_message = await telegram_manager.client.get_messages(entity, ids=post_id)
                            if not single_post_message:
                                rprint(f"[red]Не удалось найти пост с ID {post_id}[/red]")
                                continue
                            # Собираем все сообщения альбома (grouped media)
                            album_messages = await telegram_manager.collect_album_messages(entity, single_post_message)
                            # Для каждого медиа — отдельный прогресс-бар и отдельная задача
                            with Progress(
                                TextColumn("{task.fields[filename]}", justify="right"),
                                BarColumn(bar_width=None),
                                "[progress.percentage]{task.percentage:>3.1f}%"
                            ) as progress_bar:
                                # Добавляем общий прогресс-бар для альбома/поста
                                overall_task_id = progress_bar.add_task(
                                    "Общий прогресс",
                                    filename="Общий прогресс",
                                    total=len(album_messages),
                                    start=True
                                )
                                tasks = []
                                # Для каждого медиа — отдельный прогресс-бар и отдельная задача
                                async def process_and_update(msg, idx, task_id):
                                    await process_message_group(
                                        messages=[msg],
                                        entity_id_str=entity_id_str,
                                        target_name=f"Медиа #{msg.id}",
                                        entity_export_path=config.get_export_path_for_entity(entity_id_str),
                                        entity_media_path=config.get_media_path_for_entity(entity_id_str),
                                        last_processed_id=None,
                                        config=config,
                                        telegram_manager=telegram_manager,
                                        cache_manager=local_cache_manager,
                                        media_processor=media_processor,
                                        note_generator=note_generator,
                                        http_session=http_session,
                                        export_target=target,
                                        telegram_manager_pass=telegram_manager,
                                        progress=progress_bar,
                                        post_task_id=task_id
                                    )
                                    # После завершения медиа — обновить общий прогресс-бар
                                    progress_bar.update(overall_task_id, advance=1)

                                for idx, msg in enumerate(album_messages):
                                    task_id = progress_bar.add_task(
                                        "download",
                                        filename=f"Медиа #{idx+1}",
                                        total=1,
                                        start=False
                                    )
                                    progress_bar.start_task(task_id)
                                    tasks.append(process_and_update(msg, idx, task_id))
                                await asyncio.gather(*tasks)
                                # Явно завершить общий прогресс-бар на 100%
                                progress_bar.update(overall_task_id, completed=len(album_messages))
                        continue

                    post_task_ids = {}
                    post_filenames = {}

                    concurrency = getattr(config, "batch_size", None) or getattr(config, "workers", 8)
                    batch_size = concurrency * 2
                    prefetch_threshold = math.ceil(batch_size * 0.75)

                    queue = asyncio.Queue(maxsize=batch_size * 2)

                    async def fetch_batches():
                        idx = 0
                        try:
                            async for msg in telegram_manager.fetch_messages(entity, limit=None):
                                await queue.put((msg, idx))
                                idx += 1
                        except Exception:
                            pass

                    async def worker(progress_bar, active_task_ids, overall_task_id=None):
                        while True:
                            try:
                                msg, idx = await queue.get()
                                task_id = progress_bar.add_task(
                                    "download",
                                    filename=f"Пост #{idx+1}",
                                    total=None,
                                    start=False
                                )
                                post_task_ids[str(getattr(msg, "id", idx))] = task_id
                                post_filenames[str(getattr(msg, "id", idx))] = f"Пост #{idx+1}"
                                progress_bar.start_task(task_id)
                                active_task_ids.add(task_id)
                                try:
                                    await process_message_group(
                                        messages=[msg],
                                        entity_id_str=entity_id_str,
                                        target_name=f"Пост #{idx+1}",
                                        entity_export_path=config.get_export_path_for_entity(entity_id_str),
                                        entity_media_path=config.get_media_path_for_entity(entity_id_str),
                                        last_processed_id=None,
                                        config=config,
                                        telegram_manager=telegram_manager,
                                        cache_manager=local_cache_manager,
                                        media_processor=media_processor,
                                        note_generator=note_generator,
                                        http_session=http_session,
                                        export_target=target,
                                        telegram_manager_pass=telegram_manager,
                                        progress=progress_bar,
                                        post_task_id=task_id
                                    )
                                    # Обновляем общий прогресс-бар
                                    if overall_task_id is not None:
                                        progress_bar.update(overall_task_id, advance=1)
                                except Exception:
                                    pass
                                finally:
                                    active_task_ids.discard(task_id)
                                    progress_bar.remove_task(task_id)
                                    post_task_ids.pop(str(getattr(msg, 'id', idx)), None)
                                    queue.task_done()
                            except Exception:
                                pass

                    async def orchestrator():
                        # Сообщения выводятся только один раз выше
                        progress_bar = Progress(
                            TextColumn("{task.fields[filename]}", justify="right"),
                            BarColumn(bar_width=None),
                            "[progress.percentage]{task.percentage:>3.1f}%"
                        )
                        active_task_ids = set()
                        # Добавляем общий прогресс-бар
                        if total_posts:
                            overall_task_id = progress_bar.add_task("Общий прогресс", total=total_posts, filename="Общий прогресс")
                        else:
                            overall_task_id = None
                        with Live(progress_bar, refresh_per_second=20):
                            workers = [asyncio.create_task(worker(progress_bar, active_task_ids, overall_task_id)) for _ in range(config.workers)]
                            fetch_task = asyncio.create_task(fetch_batches())
                            while not queue.empty() or not fetch_task.done():
                                # Показываем только активные прогресс-бары
                                for task in list(progress_bar.tasks):
                                    # Не удаляем общий прогресс-бар
                                    if overall_task_id is not None and task.id == overall_task_id:
                                        continue
                                    if task.id not in active_task_ids:
                                        progress_bar.remove_task(task.id)
                                if queue.qsize() < prefetch_threshold and not fetch_task.done():
                                    pass
                                await asyncio.sleep(0.1)
                            await queue.join()
                            # Явно завершить общий прогресс-бар на 100%
                            if overall_task_id is not None and total_posts:
                                progress_bar.update(overall_task_id, completed=total_posts)
                            for w in workers:
                                w.cancel()
                            await asyncio.gather(*workers, return_exceptions=True)

                    await orchestrator()

                    rprint("[magenta]Downloading posts/media complete.[/magenta]")

                    rprint("[green]***Post-processing***[/green]")
                    for target in config.export_targets:
                        if getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None:
                            continue
                        entity_id_str = str(target.id)
                        export_root = config.get_export_path_for_entity(entity_id_str)

                        if reply_linker is not None:
                            await reply_linker.link_replies(entity_id_str, export_root)
                        if cache_manager is not None:
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
                                async with aiofiles.open(note_file, "r", encoding="utf-8") as f:
                                    content = await f.read()
                                    match = re.search(r"\*Source: (https?://telegra\.ph/[^\*]+)\*", content)
                                    if match:
                                        telegraph_url = match.group(1).strip()
                                if telegraph_url:
                                    telegraph_mapping[telegraph_url] = note_file.stem
                    for target in config.export_targets:
                        entity_id_str = str(target.id)
                        export_root = config.get_export_path_for_entity(entity_id_str)
                        for note_file in Path(export_root).rglob("*.md"):
                            async with aiofiles.open(note_file, "r", encoding="utf-8") as f:
                                content = await f.read()
                            modified = content
                            for url, note_stem in telegraph_mapping.items():
                                modified = re.sub(rf"\[([^\]]+)\]\({re.escape(url)}\)", rf"[[{note_stem}|\1]]", modified)
                                modified = modified.replace(url, f"[[{note_stem}]]")
                            if modified != content:
                                async with aiofiles.open(note_file, "w", encoding="utf-8") as f:
                                    await f.write(modified)

        else:
            if not config.export_targets:
                logger.error("No export targets specified. Exiting.")
                return

            connector = None
            if getattr(config, "proxy_type", None) and getattr(config, "proxy_addr", None) and getattr(config, "proxy_port", None):
                proxy_url = f"{config.proxy_type.lower()}://{config.proxy_addr}:{config.proxy_port}"
                connector = ProxyConnector.from_url(proxy_url)

            async with aiohttp.ClientSession(headers=AIOHTTP_HEADERS, connector=connector) as http_session:
                export_summaries = []

                rprint("[cyan]***Export***[/cyan]")
                for target in config.export_targets:
                    if getattr(target, "type", None) == "single_post" or getattr(target, "message_id", None) is not None:
                        local_cache_manager = None
                    else:
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

                    if reply_linker is not None:
                        await reply_linker.link_replies(entity_id_str, export_root)
                    if cache_manager is not None:
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
                            async with aiofiles.open(note_file, "r", encoding="utf-8") as f:
                                content = await f.read()
                                match = re.search(r"\*Source: (https?://telegra\.ph/[^\*]+)\*", content)
                                if match:
                                    telegraph_url = match.group(1).strip()
                            if telegraph_url:
                                telegraph_mapping[telegraph_url] = note_file.stem
                for target in config.export_targets:
                    entity_id_str = str(target.id)
                    export_root = config.get_export_path_for_entity(entity_id_str)
                    for note_file in Path(export_root).rglob("*.md"):
                        async with aiofiles.open(note_file, "r", encoding="utf-8") as f:
                            content = await f.read()
                        modified = content
                        for url, note_stem in telegraph_mapping.items():
                            modified = re.sub(rf"\[([^\]]+)\]\({re.escape(url)}\)", rf"[[{note_stem}|\1]]", modified)
                            modified = modified.replace(url, f"[[{note_stem}]]")
                        if modified != content:
                            async with aiofiles.open(note_file, "w", encoding="utf-8") as f:
                                await f.write(modified)

    except (ConfigError, TelegramConnectionError) as e:
        logger.critical(f"A critical error occurred: {e}")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
    finally:
        if telegram_manager and getattr(telegram_manager, "client_connected", False):
            await telegram_manager.disconnect()
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
    while True:
        clear_screen()
        rprint("[bold yellow]Advanced Config Options:[/bold yellow]")
        rprint(" [cyan]1.[/cyan] Throttle threshold (KB/s): [green]{}[/green]".format(getattr(config, 'throttle_threshold_kbps', 50)))
        rprint(" [cyan]2.[/cyan] Throttle pause (s): [green]{}[/green]".format(getattr(config, 'throttle_pause_s', 30)))
        rprint(" [cyan]3.[/cyan] Number of workers: [green]{}[/green]".format(getattr(config, 'workers', 8)))
        rprint(" [cyan]4.[/cyan] Number of batch size: [green]{}[/green]".format(getattr(config, 'batch_size', 100)))
        rprint(" [cyan]5.[/cyan] Continue to export")
        rprint(" [cyan]6.[/cyan] Exit")
        choice = input("Choose an option to change (1-6): ").strip()
        if choice == "1":
            config.throttle_threshold_kbps = prompt_int("Throttle threshold (KB/s)", getattr(config, 'throttle_threshold_kbps', 50))
        elif choice == "2":
            config.throttle_pause_s = prompt_int("Throttle pause (s)", getattr(config, 'throttle_pause_s', 30))
        elif choice == "3":
            config.workers = prompt_int("Number of workers", getattr(config, 'workers', 8))
        elif choice == "4":
            config.workers = prompt_int("Number of batch size", getattr(config, 'batch_size', 100))
        elif choice == "5":
            break
        elif choice == '6':
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
        config = Config.from_env()
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
