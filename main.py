import asyncio
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from telethon.tl.types import Message

from src.cache_manager import CacheManager
from src.config import Config, ExportTarget, load_config
from src.exceptions import ConfigError, ExporterError, TelegramConnectionError
from src.media_processor import MediaProcessor
from src.note_generator import NoteGenerator
from src.reply_linker import ReplyLinker
from src.telegram_client import TelegramManager
from src.utils import logger, run_in_thread_pool, setup_logging

# Global executors (initialized in run_export)
thread_executor = None
process_executor = None


async def process_message_group(
    messages: List[Message],
    entity_id: Union[str, int],
    entity_export_path: Path,
    entity_media_path: Path,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    cache_manager: CacheManager,
    config: Config
) -> Optional[int]:
    """Process a message or group of messages (album) for a specific entity."""
    if not messages:
        return None

    # Use first message for primary metadata
    first_message = messages[0]
    msg_date = getattr(first_message, 'date', None)
    logger.info(f"[{entity_id}] Processing message group ID: {first_message.id} ({len(messages)} messages) Date: {msg_date}")

    try:
        # Download and optimize media from all messages in the group
        media_paths = []
        media_tasks = [
            asyncio.create_task(
                media_processor.download_and_optimize_media(
                    message, entity_id, entity_media_path
                )
            )
            for message in messages
        ]

        # Gather all media paths
        for result in await asyncio.gather(*media_tasks, return_exceptions=True):
            if isinstance(result, Exception):
                logger.error(f"[{entity_id}] Media processing error: {result}")
            elif isinstance(result, list):  # Make sure result is a list before extending
                media_paths.extend(result)

        # Create note using the first message's text and all media
        try:
            note_path = await run_in_thread_pool(
                note_generator.create_note_sync,
                first_message,
                media_paths,
                entity_id,
                entity_export_path
            )
        except Exception as e:
            logger.error(f"[{entity_id}] Error creating note for message {first_message.id}: {e}",
                        exc_info=config.verbose)
            return None

        # If note creation succeeded, mark all messages as processed
        if note_path:
            note_filename = note_path.name

            # Get reply info from first message
            reply_to_id = None
            if hasattr(first_message, "reply_to"):
                reply_to_id = getattr(first_message.reply_to, 'reply_to_msg_id', None)

            # Mark all messages as processed
            for message in messages:
                # Only store reply_to for the first message to avoid redundancy
                msg_reply_id = reply_to_id if message.id == first_message.id else None

                await cache_manager.add_processed_message_async(
                    message_id=message.id,
                    note_filename=note_filename,
                    reply_to_id=msg_reply_id,
                    entity_id=entity_id
                )

            logger.info(f"[{entity_id}] Processed message group {first_message.id} -> {note_filename}")
            return first_message.id  # Return ID of first message to represent group
        else:
            logger.error(f"[{entity_id}] Failed to create note for message {first_message.id}")
            return None

    except Exception as e:
        logger.error(f"[{entity_id}] Failed to process message group {first_message.id}: {e}",
                    exc_info=config.verbose)
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
    """Export messages for a single target (channel, chat, or user)."""
    entity_id_str = str(target.id)
    logger.info(f"--- Starting export for target: {target.name or entity_id_str} ({target.type}) ---")

    try:
        # Resolve entity and prepare paths
        entity = await telegram_manager.resolve_entity(target.id)
        if not entity:
            logger.error(f"Could not resolve entity for target ID: {target.id}. Skipping.")
            return

        # Update target info in cache
        target.name = getattr(entity, 'title', getattr(entity, 'username', entity_id_str))
        logger.info(f"Resolved entity: {target.name} (ID: {entity.id})")
        await cache_manager.update_entity_info_async(entity_id_str, target.name, target.type)

        # Set up export paths
        entity_export_path = config.get_export_path_for_entity(target.id)
        entity_media_path = config.get_media_path_for_entity(target.id)
        logger.info(f"Export path: {entity_export_path}")
        logger.info(f"Media path: {entity_media_path}")

        # Determine starting message ID (for incremental mode)
        last_processed_id = None
        if config.only_new:
            last_processed_id = cache_manager.get_last_processed_message_id(entity_id_str)
            if last_processed_id:
                logger.info(f"[{target.name}] Running in incremental mode. Starting after message ID: {last_processed_id}")
            else:
                logger.info(f"[{target.name}] Running in incremental mode, but no previous messages found.")

        # Process messages
        await process_entity_messages(
            entity=entity,
            entity_id_str=entity_id_str,
            target_name=target.name,
            entity_export_path=entity_export_path,
            entity_media_path=entity_media_path,
            last_processed_id=last_processed_id,
            config=config,
            telegram_manager=telegram_manager,
            cache_manager=cache_manager,
            media_processor=media_processor,
            note_generator=note_generator
        )

        # Process reply links
        logger.info(f"[{target.name}] Starting reply linking...")
        await reply_linker.link_replies(entity_id_str, entity_export_path)
        logger.info(f"[{target.name}] Reply linking finished.")

        # Save cache
        await cache_manager.save_cache()
        logger.info(f"[{target.name}] Cache saved.")

    except TelegramConnectionError as e:
        logger.error(f"[{target.name}] Telegram connection error: {e}")
        await cache_manager.save_cache()
    except ExporterError as e:
        logger.error(f"[{target.name}] Exporter error: {e}")
        await cache_manager.save_cache()
    except Exception as e:
        logger.critical(f"[{target.name}] Critical error: {e}", exc_info=True)
        await cache_manager.save_cache()

    logger.info(f"--- Finished export for target: {target.name or entity_id_str} ---")


async def process_entity_messages(
    entity: Any,
    entity_id_str: str,
    target_name: str,
    entity_export_path: Path,
    entity_media_path: Path,
    last_processed_id: Optional[int],
    config: Config,
    telegram_manager: TelegramManager,
    cache_manager: CacheManager,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator
):
    """Process all messages for an entity with efficient message grouping."""
    # Task management
    max_concurrent_tasks = getattr(config, 'max_workers', 8)
    semaphore = asyncio.Semaphore(max_concurrent_tasks)
    active_tasks = set()

    # Statistics tracking
    processed_count = 0
    successful_count = 0
    cache_save_interval = getattr(config, 'cache_save_interval', 50)

    # Message grouping
    grouped_messages: Dict[int, List[Message]] = {}
    GROUP_TIMEOUT = 0.5  # Seconds to wait for more messages in a group
    last_message_time = asyncio.get_event_loop().time()
    processed_group_ids = set()

    async def process_buffered_group(group_id: int):
        """Process a group of messages."""
        nonlocal successful_count, processed_count

        if group_id not in grouped_messages:
            return

        messages_to_process = grouped_messages.pop(group_id)
        if not messages_to_process:
            return

        # Check if any message in group is already processed
        for msg in messages_to_process:
            if await cache_manager.is_processed_async(msg.id, entity_id_str):
                logger.warning(f"[{target_name}] Message {msg.id} in group {group_id} already processed. Skipping group.")
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
                    logger.error(f"Message group processing task failed: {e}", exc_info=config.verbose)
                finally:
                    active_tasks.discard(fut)

            task.add_done_callback(task_done_callback)

    # Fetch and process messages
    try:
        async for message in telegram_manager.fetch_messages(entity=entity, min_id=last_processed_id):
            current_time = asyncio.get_event_loop().time()

            # Process groups that haven't received messages recently
            expired_groups = [
                gid for gid, msgs in grouped_messages.items()
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
                    if message.grouped_id not in grouped_messages:
                        grouped_messages[message.grouped_id] = []
                    # Only add if not already processed
                    if not await cache_manager.is_processed_async(message.id, entity_id_str):
                        grouped_messages[message.grouped_id].append(message)
            else:
                # Process standalone message
                if not await cache_manager.is_processed_async(message.id, entity_id_str):
                    async with semaphore:
                        task = asyncio.create_task(
                            process_message_group(
                                [message],  # Single message list
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
                                logger.error(f"Message processing task failed: {e}", exc_info=config.verbose)
                            finally:
                                active_tasks.discard(fut)

                        task.add_done_callback(task_done_callback)

            last_message_time = current_time

            # Cache saving logic
            if processed_count > 0 and processed_count % cache_save_interval == 0:
                await cache_manager.schedule_background_save()
                logger.info(f"[{target_name}] Progress: ~{successful_count}/{processed_count} messages processed.")

        # Process any remaining groups in buffer
        logger.info(f"[{target_name}] Finished fetching messages. Processing remaining {len(grouped_messages)} groups.")
        remaining_group_ids = list(grouped_messages.keys())
        for group_id in remaining_group_ids:
            if group_id not in processed_group_ids:
                await process_buffered_group(group_id)

        # Wait for any remaining tasks
        if active_tasks:
            logger.info(f"[{target_name}] Waiting for {len(active_tasks)} tasks to complete...")
            await asyncio.gather(*active_tasks, return_exceptions=True)

        logger.info(f"[{target_name}] Processing complete. {successful_count}/{processed_count} messages processed.")

    except Exception as e:
        logger.error(f"[{target_name}] Error processing messages: {e}", exc_info=config.verbose)
        raise


async def run_export(config: Config):
    """Orchestrate the export process for multiple targets."""
    global thread_executor, process_executor

    # Set up thread and process pools
    cpu_count = multiprocessing.cpu_count() or 1
    process_workers = max(1, min(cpu_count - 1, getattr(config, 'max_process_workers', 4)))
    thread_workers = getattr(config, 'max_workers', min(32, cpu_count * 5))

    telegram_manager = None
    try:
        # Initialize executors
        process_executor = ProcessPoolExecutor(max_workers=process_workers)
        thread_executor = ThreadPoolExecutor(max_workers=thread_workers)
        asyncio.get_event_loop().set_default_executor(thread_executor)
        logger.info(f"Using {process_workers} process workers and {thread_workers} thread workers.")

        # Initialize components
        cache_manager = CacheManager(config.cache_file)
        await cache_manager.load_cache()

        telegram_manager = TelegramManager(config)
        media_processor = MediaProcessor(config, telegram_manager.get_client())
        note_generator = NoteGenerator(config)
        reply_linker = ReplyLinker(config, cache_manager)

        # Connect to Telegram
        await telegram_manager.connect()

        # Handle interactive mode
        if config.interactive_mode:
            await telegram_manager.run_interactive_selection()
            if not config.export_targets:
                logger.warning("No targets selected in interactive mode. Exiting.")
                return

        # Verify we have targets to export
        if not config.export_targets:
            logger.error("No export targets specified in config or selected interactively. Exiting.")
            return

        # Process each target
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
        logger.critical(f"Unexpected error: {e}", exc_info=True)
    finally:
        # Clean up resources
        if thread_executor:
            thread_executor.shutdown(wait=False, cancel_futures=True)
        if process_executor:
            process_executor.shutdown(wait=False, cancel_futures=True)

        # Disconnect from Telegram - properly await the disconnect operation
        if telegram_manager and telegram_manager.client_connected:
            try:
                await telegram_manager.disconnect()
            except Exception as e:
                logger.error(f"Error during disconnection: {e}")

        logger.info("Export process finished.")


async def main():
    """Program entry point."""
    try:
        # Load configuration
        config = load_config()
        setup_logging(config.verbose)
        logger.info("Configuration loaded successfully.")

        # Increase stack size on Unix systems
        try:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
            new_soft = min(hard, 16 * 1024 * 1024)  # 16 MB stack
            resource.setrlimit(resource.RLIMIT_STACK, (new_soft, hard))
        except (ImportError, AttributeError):
            # Not on Unix or resource module not available
            pass

        # Run the export process
        await run_export(config)

    except (ValueError, ConfigError) as e:
        print(f"ERROR: Configuration failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Configure WindowsProactorEventLoopPolicy on Windows
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
