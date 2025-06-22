# main.py

import asyncio
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiohttp
from aiohttp_proxy import ProxyConnector
from telethon.tl.types import Message

from src.cache_manager import CacheManager
from src.config import Config, ExportTarget, load_config
from src.exceptions import ConfigError, ExporterError, TelegramConnectionError

# --- ИЗМЕНЕНО: Импортируем константу напрямую ---
from src.media_processor import AIOHTTP_HEADERS, MediaProcessor
from src.note_generator import NoteGenerator
from src.reply_linker import ReplyLinker
from src.telegram_client import TelegramManager
from src.utils import find_telegraph_links, logger, setup_logging

# ... (остальной код файла остается таким же, как я присылал в прошлый раз)
# ... (я просто скопирую его сюда для полноты)

thread_executor: Optional[ThreadPoolExecutor] = None
process_executor: Optional[ProcessPoolExecutor] = None

async def process_message_group(
    messages: List[Message],
    entity_id: Union[str, int],
    entity_export_path: Path,
    entity_media_path: Path,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    cache_manager: CacheManager,
    config: Config,
    http_session: aiohttp.ClientSession
) -> Optional[int]:
    if not messages:
        return None

    first_message = messages[0]
    msg_date = getattr(first_message, 'date', 'No date')
    logger.info(f"[{entity_id}] Processing message group ID: {first_message.id} ({len(messages)} messages) Date: {msg_date}")

    try:
        media_paths = []
        if config.media_download:
            media_tasks = [
                media_processor.download_and_optimize_media(msg, entity_id, entity_media_path)
                for msg in messages
            ]
            for result in await asyncio.gather(*media_tasks):
                if isinstance(result, list):
                    media_paths.extend(result)

        note_path = await note_generator.create_note(
            first_message, media_paths, entity_id, entity_export_path
        )
        if not note_path:
            logger.error(f"[{entity_id}] Failed to create main note for message {first_message.id}")
            return None

        telegraph_links = find_telegraph_links(first_message.text)
        if telegraph_links:
            logger.info(f"Found {len(telegraph_links)} Telegra.ph links to process in message {first_message.id}.")
            original_content = await note_generator.read_note_content(note_path)
            modified_content = original_content

            for link in telegraph_links:
                article_note_path = await note_generator.create_note_from_telegraph_url(
                session=http_session,
                url=link,
                notes_export_path=entity_export_path, # Где сохранять .md файл статьи
                media_export_path=entity_media_path, # Где сохранять картинки из статьи
                media_processor=media_processor
                )
                if article_note_path:
                    # Используем Path.stem, чтобы получить имя файла без .md для ссылки
                    local_link = f"[[{article_note_path.stem}]]"
                    modified_content = modified_content.replace(link, local_link)

            if modified_content != original_content:
                await note_generator.write_note_content(note_path, modified_content)
                logger.info(f"Updated note {note_path.name} with local Telegra.ph links.")

        note_filename = note_path.name
        reply_to_id = getattr(first_message.reply_to, 'reply_to_msg_id', None)

        for message in messages:
            channel_id = getattr(message.peer_id, 'channel_id', None)
            telegram_url = f"https://t.me/c/{channel_id}/{message.id}" if channel_id else None

            await cache_manager.add_processed_message_async(
                message_id=message.id,
                note_filename=note_filename,
                reply_to_id=reply_to_id if message.id == first_message.id else None,
                entity_id=entity_id,
                title=(message.text or "").split('\n', 1)[0].strip(),
                telegram_url=telegram_url
            )

        logger.info(f"[{entity_id}] Successfully processed message group {first_message.id} -> {note_filename}")
        return first_message.id

    except Exception as e:
        logger.error(f"[{entity_id}] Critical failure in process_message_group for msg {first_message.id}: {e}", exc_info=config.verbose)
        return None

async def export_single_target(
    target: ExportTarget, config: Config, telegram_manager: TelegramManager,
    cache_manager: CacheManager, media_processor: MediaProcessor, note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession
):
    entity_id_str = str(target.id)
    logger.info(f"--- Starting export for target: {target.name or entity_id_str} ---")

    try:
        entity = await telegram_manager.resolve_entity(target.id)
        if not entity:
            logger.error(f"Could not resolve entity for target ID: {target.id}. Skipping.")
            return

        target.name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))
        target.id = entity.id
        entity_id_str = str(target.id)

        logger.info(f"Resolved entity: {target.name} (ID: {entity_id_str})")
        await cache_manager.update_entity_info_async(entity_id_str, target.name, target.type)

        entity_export_path = config.get_export_path_for_entity(entity_id_str)
        entity_media_path = config.get_media_path_for_entity(entity_id_str)
        logger.info(f"Export path: {entity_export_path}")
        logger.info(f"Media path: {entity_media_path}")

        last_processed_id = cache_manager.get_last_processed_message_id(entity_id_str) if config.only_new else None
        if last_processed_id:
            logger.info(f"[{target.name}] Incremental mode. Starting after message ID: {last_processed_id}")

        await process_entity_messages(
            entity, entity_id_str, target.name, entity_export_path, entity_media_path,
            last_processed_id, config, telegram_manager, cache_manager, media_processor, note_generator,
            http_session
        )

    except (ExporterError, TelegramConnectionError) as e:
        logger.error(f"[{target.name}] Export failed: {e}")
    except Exception as e:
        logger.critical(f"[{target.name}] Critical error during export: {e}", exc_info=True)
    finally:
        await cache_manager.save_cache()
        logger.info(f"--- Finished export for target: {target.name} ---")

async def process_entity_messages(
    entity: Any, entity_id_str: str, target_name: str, entity_export_path: Path,
    entity_media_path: Path, last_processed_id: Optional[int], config: Config,
    telegram_manager: TelegramManager, cache_manager: CacheManager,
    media_processor: MediaProcessor, note_generator: NoteGenerator,
    http_session: aiohttp.ClientSession
):
    semaphore = asyncio.Semaphore(config.max_workers)
    active_tasks = set()
    processed_count, successful_count = 0, 0
    grouped_messages: Dict[int, List[Message]] = {}
    GROUP_TIMEOUT = 0.5
    last_message_time = asyncio.get_event_loop().time()

    async def _process_group(group_id: int):
        nonlocal successful_count
        messages = grouped_messages.pop(group_id, [])
        if not messages: return

        async with semaphore:
            task = asyncio.create_task(
                process_message_group(
                    messages, entity_id_str, entity_export_path, entity_media_path,
                    media_processor, note_generator, cache_manager, config,
                    http_session
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

            if processed_count % config.cache_save_interval == 0:
                await cache_manager.schedule_background_save()
                logger.info(f"[{target_name}] Progress: ~{processed_count} messages fetched.")

        for gid in list(grouped_messages.keys()):
            await _process_group(gid)

        if active_tasks:
            await asyncio.gather(*active_tasks)

        logger.info(f"[{target_name}] Processing complete. {successful_count} notes created from {processed_count} messages.")

    except Exception as e:
        logger.error(f"[{target_name}] Error during message processing loop: {e}", exc_info=config.verbose)

async def run_export(config: Config):
    global thread_executor, process_executor
    telegram_manager = None
    try:
        cpu_count = multiprocessing.cpu_count() or 1
        process_workers = max(1, min(cpu_count, config.max_process_workers))
        thread_workers = config.max_workers
        process_executor = ProcessPoolExecutor(max_workers=process_workers)
        thread_executor = ThreadPoolExecutor(max_workers=thread_workers)
        asyncio.get_event_loop().set_default_executor(thread_executor)
        logger.info(f"Using {process_workers} process workers and {thread_workers} thread workers.")

        cache_manager = CacheManager(config.cache_file)
        await cache_manager.load_cache()
        config.cache = cache_manager.cache

        telegram_manager = TelegramManager(config)
        await telegram_manager.connect()

        media_processor = MediaProcessor(config, telegram_manager.get_client())
        note_generator = NoteGenerator(config)
        reply_linker = ReplyLinker(config, cache_manager)

        if config.interactive_mode:
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
            logger.info(f"Using proxy for aiohttp requests: {proxy_url}")

        async with aiohttp.ClientSession(headers=AIOHTTP_HEADERS, connector=connector) as http_session:
            logger.info("--- STAGE 1: EXPORTING ALL TARGETS ---")
            for target in config.export_targets:
                await export_single_target(
                    target, config, telegram_manager, cache_manager,
                    media_processor, note_generator, http_session
                )
            logger.info("--- STAGE 1 COMPLETE ---")

            logger.info("--- STAGE 2: POST-PROCESSING ---")
            for target in config.export_targets:
                entity_id_str = str(target.id)
                export_root = config.get_export_path_for_entity(entity_id_str)

                logger.info(f"Post-processing replies for '{target.name}'...")
                await reply_linker.link_replies(entity_id_str, export_root)

                logger.info(f"Post-processing internal links for '{target.name}'...")
                await note_generator.postprocess_all_notes(export_root, entity_id_str, cache_manager.cache)
            logger.info("--- STAGE 2 COMPLETE ---")

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
        logger.info("Export process finished.")

async def main():
    try:
        config = load_config()
        setup_logging(config.verbose)
        logger.info("Configuration loaded.")
        await run_export(config)
    except (ConfigError, ValueError) as e:
        print(f"ERROR: Configuration failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        try:
            logger.critical(f"A fatal error occurred in main: {e}", exc_info=True)
        except NameError:
            print(f"FATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
    except Exception as e:
        print(f"\nFATAL UNHANDLED ERROR: {e}", file=sys.stderr)
