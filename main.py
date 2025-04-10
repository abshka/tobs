import asyncio
import sys
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path
from typing import Optional, List, Dict

from telethon.tl.types import Message

from src.config import load_config, Config, ExportTarget
from src.utils import setup_logging, logger, run_in_thread_pool
from src.cache_manager import CacheManager
from src.telegram_client import TelegramManager
from src.media_processor import MediaProcessor
from src.note_generator import NoteGenerator
from src.reply_linker import ReplyLinker
from src.exceptions import ExporterError, ConfigError, TelegramConnectionError

thread_executor = None
process_executor = None

async def process_message_group(
    messages: List[Message],
    entity_id: str | int,
    entity_export_path: Path,
    entity_media_path: Path,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    cache_manager: CacheManager,
    config: Config,
) -> Optional[int]:
    """Processes a single message or a group of messages (album) for a specific entity."""
    if not messages:
        return None

    # Use the first message for primary info (text, date, reply_to)
    first_message = messages[0]
    logger.info(f"[Entity: {entity_id}] Processing message group starting with ID: {first_message.id} (Size: {len(messages)}) Date: {first_message.date}")

    all_media_paths = []
    media_tasks = []

    # Collect media from all messages in the group
    for message in messages:
        media_tasks.append(
            asyncio.create_task(
                media_processor.download_and_optimize_media(message, entity_id, entity_media_path)
            )
        )

    try:
        # Gather media paths from all messages
        results = await asyncio.gather(*media_tasks)
        for paths in results:
            if paths:
                all_media_paths.extend(paths)

        # Use the first message for note content generation
        try:
            note_path = await run_in_thread_pool(
                note_generator.create_note_sync,
                first_message, # Use first message for text/metadata
                all_media_paths, # Pass all collected media paths
                entity_id,
                entity_export_path
            )
        except Exception as e:
            logger.error(f"[Entity: {entity_id}] Error creating note for message group starting with {first_message.id}: {e}", exc_info=config.verbose)
            note_path = None

        if note_path:
            note_filename = note_path.name
            reply_to_id = getattr(first_message.reply_to, 'reply_to_msg_id', None) if hasattr(first_message, "reply_to") else None

            # Mark all messages in the group as processed using the same note filename
            for message in messages:
                await cache_manager.add_processed_message_async(
                    message_id=message.id,
                    note_filename=note_filename,
                    reply_to_id=reply_to_id if message.id == first_message.id else None, # Only store reply_to for the first message
                    entity_id=entity_id
                )
            logger.info(f"[Entity: {entity_id}] Successfully processed message group starting with {first_message.id} -> {note_filename}")
            return first_message.id # Return the ID of the first message to represent the group
        else:
            logger.error(f"[Entity: {entity_id}] Skipping cache update for message group starting with {first_message.id} due to note creation failure.")
            return None

    except Exception as e:
        logger.error(f"[Entity: {entity_id}] Failed to process message group starting with {first_message.id}: {e}", exc_info=config.verbose)
        return None

async def export_single_target(
    target: ExportTarget,
    config: Config,
    telegram_manager: TelegramManager,
    cache_manager: CacheManager,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    reply_linker: ReplyLinker
):
    """Exports messages for a single target chat/channel."""
    entity_id_str = str(target.id)
    logger.info(f"--- Starting export for target: {target.name or entity_id_str} ({target.type}) ---")

    try:
        entity = await telegram_manager.resolve_entity(target.id)
        if not entity:
            logger.error(f"Could not resolve entity for target ID: {target.id}. Skipping.")
            return

        target.name = getattr(entity, 'title', getattr(entity, 'username', entity_id_str))
        logger.info(f"Resolved entity: {target.name} (ID: {entity.id})")
        await cache_manager.update_entity_info_async(entity_id_str, target.name, target.type)

        entity_export_path = config.get_export_path_for_entity(target.id)
        entity_media_path = config.get_media_path_for_entity(target.id)
        logger.info(f"Export path: {entity_export_path}")
        logger.info(f"Media path: {entity_media_path}")

        last_processed_id = None
        if config.only_new:
            last_processed_id = cache_manager.get_last_processed_message_id(entity_id_str)
            if last_processed_id:
                logger.info(f"[{target.name}] Running in 'only_new' mode. Fetching messages after ID: {last_processed_id}")
            else:
                 logger.info(f"[{target.name}] Running in 'only_new' mode. No previous messages found in cache.")

        max_concurrent_tasks = getattr(config, 'max_workers', 8)
        semaphore = asyncio.Semaphore(max_concurrent_tasks)
        active_tasks = set()
        processed_count = 0
        successful_count = 0
        cache_save_interval = getattr(config, 'cache_save_interval', 50)

        # Group handling variables
        grouped_messages_buffer: Dict[int, List[Message]] = {}
        GROUP_TIMEOUT = 0.5  # Seconds to wait for more messages in a group
        last_message_time = asyncio.get_event_loop().time()
        processed_group_ids = set()

        async def process_buffered_group(group_id: int):
            nonlocal successful_count, processed_count
            if group_id not in grouped_messages_buffer:
                return

            messages_to_process = grouped_messages_buffer.pop(group_id)
            if not messages_to_process:
                return

            # Check if any message in group is already processed
            for msg in messages_to_process:
                if await cache_manager.is_processed_async(msg.id, entity_id_str):
                    logger.warning(f"[{target.name}] Message {msg.id} (part of group {group_id}) already processed. Skipping group.")
                    return

            async with semaphore:
                task = asyncio.create_task(
                    process_message_group(
                        messages_to_process,
                        entity_id_str,
                        entity_export_path,
                        entity_media_path,
                        media_processor,
                        note_generator,
                        cache_manager,
                        config
                    )
                )
                active_tasks.add(task)
                processed_count += len(messages_to_process)

                def task_done_callback(fut):
                    nonlocal successful_count
                    try:
                        result = fut.result()
                        if result is not None:
                            successful_count += 1  # Count group as one success
                    except Exception as e:
                        logger.error(f"Task for message group processing failed: {e}", exc_info=config.verbose)
                    finally:
                        active_tasks.discard(fut)

                task.add_done_callback(task_done_callback)

        async for message in telegram_manager.fetch_messages(entity=entity, min_id=last_processed_id):
            current_time = asyncio.get_event_loop().time()

            # Process groups that haven't received messages recently
            expired_groups = [
                gid for gid, msgs in grouped_messages_buffer.items()
                if msgs and (current_time - last_message_time > GROUP_TIMEOUT)
            ]
            for group_id in expired_groups:
                if group_id not in processed_group_ids:
                    await process_buffered_group(group_id)
                    processed_group_ids.add(group_id)

            # Process current message
            if hasattr(message, 'grouped_id') and message.grouped_id:
                # Message is part of a media group
                if message.grouped_id not in processed_group_ids:
                    if message.grouped_id not in grouped_messages_buffer:
                        grouped_messages_buffer[message.grouped_id] = []
                    # Only add if not already processed
                    if not await cache_manager.is_processed_async(message.id, entity_id_str):
                        grouped_messages_buffer[message.grouped_id].append(message)
                    else:
                        logger.debug(f"[{target.name}] Message {message.id} (group {message.grouped_id}) already in cache, skipping.")
                else:
                    logger.debug(f"[{target.name}] Group {message.grouped_id} already processed, skipping message {message.id}.")
            else:
                # Process standalone message
                if not await cache_manager.is_processed_async(message.id, entity_id_str):
                    async with semaphore:
                        task = asyncio.create_task(
                            process_message_group(  # Use same function with list of one
                                [message],
                                entity_id_str,
                                entity_export_path,
                                entity_media_path,
                                media_processor,
                                note_generator,
                                cache_manager,
                                config
                            )
                        )
                        active_tasks.add(task)
                        processed_count += 1

                        def task_done_callback(fut):
                            nonlocal successful_count
                            try:
                                result = fut.result()
                                if result is not None:
                                    successful_count += 1
                            except Exception as e:
                                logger.error(f"Task for message processing failed: {e}", exc_info=config.verbose)
                            finally:
                                active_tasks.discard(fut)

                        task.add_done_callback(task_done_callback)
                else:
                    logger.debug(f"[{target.name}] Standalone message {message.id} already processed, skipping.")

            last_message_time = current_time

            # Cache saving logic
            if processed_count > 0 and processed_count % cache_save_interval == 0:
                await cache_manager.schedule_background_save()
                logger.info(f"[{target.name}] Scheduled cache save after processing ~{processed_count} messages.")
                logger.info(f"[{target.name}] Progress: ~{successful_count}/{processed_count} messages processed, {len(active_tasks)} tasks active.")

        # Process any remaining groups in buffer
        logger.info(f"[{target.name}] Finished fetching messages. Processing remaining {len(grouped_messages_buffer)} groups in buffer...")
        remaining_group_ids = list(grouped_messages_buffer.keys())
        for group_id in remaining_group_ids:
            if group_id not in processed_group_ids:
                await process_buffered_group(group_id)

        if active_tasks:
            logger.info(f"[{target.name}] Waiting for {len(active_tasks)} remaining message processing tasks...")
            await asyncio.gather(*active_tasks, return_exceptions=True)

        logger.info(f"[{target.name}] Finished processing messages. Total processed: {successful_count}/{processed_count}.")

        logger.info(f"[{target.name}] Starting reply linking...")
        await reply_linker.link_replies(entity_id_str, entity_export_path)
        logger.info(f"[{target.name}] Reply linking finished.")

        await cache_manager.save_cache()
        logger.info(f"[{target.name}] Final cache saved.")

    except TelegramConnectionError as e:
         logger.error(f"[{target.name}] Telegram connection error during export: {e}")
         await cache_manager.save_cache()
    except ExporterError as e:
        logger.error(f"[{target.name}] Exporter error: {e}")
        await cache_manager.save_cache()
    except Exception as e:
        logger.critical(f"[{target.name}] Unexpected critical error during export: {e}", exc_info=True)
        await cache_manager.save_cache()

    logger.info(f"--- Finished export for target: {target.name or entity_id_str} ---")


async def run_export(config: Config):
    """Main export process orchestration for multiple targets."""
    global thread_executor, process_executor

    cpu_count = multiprocessing.cpu_count() or 1
    process_workers = max(1, min(cpu_count - 1, getattr(config, 'max_process_workers', 4)))
    thread_workers = getattr(config, 'max_workers', min(32, cpu_count * 5))

    telegram_manager = None  # Initialize to avoid unbound reference
    try:
        process_executor = ProcessPoolExecutor(max_workers=process_workers)
        thread_executor = ThreadPoolExecutor(max_workers=thread_workers)
        asyncio.get_event_loop().set_default_executor(thread_executor)

        logger.info(f"Using {process_workers} process workers and {thread_workers} thread workers.")

        cache_manager = CacheManager(config.cache_file)
        await cache_manager.load_cache()

        telegram_manager = TelegramManager(config)
        media_processor = MediaProcessor(config, telegram_manager.get_client())
        note_generator = NoteGenerator(config)
        reply_linker = ReplyLinker(config, cache_manager)

        await telegram_manager.connect()

        if config.interactive_mode:
            await telegram_manager.run_interactive_selection()
            logger.info(f"Targets selected interactively: {[t.id for t in config.export_targets]}")
            if not config.export_targets:
                logger.warning("No targets selected in interactive mode. Exiting.")
                return

        if not config.export_targets:
            logger.error("No export targets specified in config or selected interactively. Exiting.")
            return

        for target in config.export_targets:
             await export_single_target(
                 target,
                 config,
                 telegram_manager,
                 cache_manager,
                 media_processor,
                 note_generator,
                 reply_linker
             )

        logger.info("All target exports finished.")

    except TelegramConnectionError as e:
        logger.critical(f"Telegram connection failed: {e}. Cannot proceed.")
    except ConfigError as e:
        logger.critical(f"Configuration error: {e}. Cannot proceed.")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred in run_export: {e}", exc_info=True)
    finally:
        if thread_executor:
            thread_executor.shutdown(wait=False, cancel_futures=True)
        if process_executor:
            process_executor.shutdown(wait=False, cancel_futures=True)

        if telegram_manager:
            await telegram_manager.disconnect()

        logger.info("Export process finished.")

async def main():
    config = None
    try:
        config = load_config()
        setup_logging(config.verbose)
        logger.info("Configuration loaded successfully.")
    except (ValueError, ConfigError) as e:
        print(f"ERROR: Configuration failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
         print(f"ERROR: Unexpected error during setup: {e}", file=sys.stderr)
         sys.exit(1)

    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        new_soft = min(hard, 16 * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_STACK, (new_soft, hard))
        logger.debug(f"Set stack limit to {new_soft / 1024 / 1024} MB")
    except (ImportError, ValueError, OSError) as e:
         logger.debug(f"Could not adjust stack limit: {e} (Not available on Windows)")

    try:
        await run_export(config)
    except Exception as e:
         logger.critical(f"Unhandled exception during main execution: {e}", exc_info=True)
         sys.exit(1)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)
