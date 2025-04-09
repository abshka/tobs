import asyncio
import sys
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path
from typing import Optional

from src.config import load_config, Config, ExportTarget
from src.utils import setup_logging, logger, run_in_thread_pool
from src.cache_manager import CacheManager
from src.telegram_client import TelegramManager
from src.media_processor import MediaProcessor
from src.note_generator import NoteGenerator
from src.reply_linker import ReplyLinker
from src.exceptions import ExporterError, ConfigError, TelegramConnectionError
import functools

# Global executors (consider passing them down if preferred)
thread_executor = None
process_executor = None

async def process_message(
    message,
    entity_id: str | int,
    entity_export_path: Path,
    entity_media_path: Path,
    media_processor: MediaProcessor,
    note_generator: NoteGenerator,
    cache_manager: CacheManager,
    config: Config,
) -> Optional[int]:
    """Processes a single message for a specific entity."""
    logger.info(f"[Entity: {entity_id}] Processing message ID: {message.id} Date: {message.date}")

    try:
        # 1. Process Media (I/O bound + potential CPU)
        # Returns list of absolute paths to processed media files
        media_paths_task = asyncio.create_task(
            media_processor.download_and_optimize_media(message, entity_id, entity_media_path)
        )

        # --- Tasks that can run in parallel with media download ---
        # Example: Pre-fetch related data if needed (currently none)

        # Wait for media processing
        processed_media_paths = await media_paths_task

        # 2. Create Note (I/O bound + potential CPU for formatting)
        # Note Generator now calculates relative paths internally
        # Use run_in_executor for potential CPU-bound parts (sanitization, complex template)
        note_path = await run_in_thread_pool(
             note_generator.create_note_sync, # Use sync wrapper for executor
             message,
             processed_media_paths,
             entity_id,
             entity_export_path # Pass entity's base export path
        )
        # note_path = await note_generator.create_note(message, processed_media_paths, entity_id, entity_export_path) # If create_note is fully async

        # 3. Update Cache (Quick, mostly in-memory + eventual async save)
        if note_path:
            note_filename = note_path.name
            reply_to_id = getattr(message.reply_to, 'reply_to_msg_id', None) if hasattr(message, "reply_to") else None

            # Add message to cache associated with its entity
            await cache_manager.add_processed_message_async(
                message_id=message.id,
                note_filename=note_filename,
                reply_to_id=reply_to_id,
                entity_id=entity_id # Crucial for multi-target
            )
            return message.id
        else:
            logger.error(f"[Entity: {entity_id}] Skipping cache update for message {message.id} due to note creation failure.")
            return None

    except Exception as e:
        logger.error(f"[Entity: {entity_id}] Failed to process message {message.id}: {e}", exc_info=config.verbose)
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
    entity_id_str = str(target.id) # Use string representation consistently
    logger.info(f"--- Starting export for target: {target.name or entity_id_str} ({target.type}) ---")

    try:
        # Resolve entity via TelegramManager
        entity = await telegram_manager.resolve_entity(target.id)
        if not entity:
            logger.error(f"Could not resolve entity for target ID: {target.id}. Skipping.")
            return

        # Update target name if resolved
        target.name = getattr(entity, 'title', getattr(entity, 'username', entity_id_str))
        logger.info(f"Resolved entity: {target.name} (ID: {entity.id})")
        # Optionally update cache with resolved entity info
        await cache_manager.update_entity_info_async(entity_id_str, target.name, target.type)

        # Get paths specific to this entity
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

        # Limit concurrent processing tasks per entity
        max_concurrent_tasks = getattr(config, 'max_workers', 8)
        semaphore = asyncio.Semaphore(max_concurrent_tasks)
        active_tasks = set()
        processed_count = 0
        successful_count = 0
        cache_save_interval = getattr(config, 'cache_save_interval', 50)

        async for message in telegram_manager.fetch_messages(entity=entity, min_id=last_processed_id):
            if not await cache_manager.is_processed_async(message.id, entity_id_str):
                async with semaphore:
                    # Ensure process_message is awaitable or wrapped correctly
                    task = asyncio.create_task(
                        process_message(
                            message,
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

                    # Optional: Callback to handle result and remove task
                    def task_done_callback(fut):
                        try:
                            result = fut.result()
                            if result is not None:
                                non_local_vars['successful_count'] += 1
                        except Exception as e:
                            logger.error(f"Task for message processing failed: {e}", exc_info=config.verbose)
                        finally:
                            active_tasks.discard(fut)

                    non_local_vars = {'successful_count': successful_count} # Workaround for closure
                    task.add_done_callback(task_done_callback)
                    successful_count = non_local_vars['successful_count'] # Update local count

                processed_count += 1

                # Periodically save cache (async save is non-blocking)
                if processed_count % cache_save_interval == 0:
                    await cache_manager.schedule_background_save()
                    logger.info(f"[{target.name}] Scheduled cache save after processing ~{processed_count} messages.")

                    # Log progress
                    logger.info(f"[{target.name}] Progress: ~{successful_count}/{processed_count} messages processed, {len(active_tasks)} tasks active.")
            else:
                logger.trace(f"[{target.name}] Message {message.id} already in cache, skipping.")

        # Wait for all processing tasks for this entity to complete
        if active_tasks:
            logger.info(f"[{target.name}] Waiting for {len(active_tasks)} remaining message processing tasks...")
            await asyncio.gather(*active_tasks, return_exceptions=True) # Handle potential errors during gather

        logger.info(f"[{target.name}] Finished processing messages. Total processed: {successful_count}/{processed_count}.")

        # Link replies for this specific entity
        logger.info(f"[{target.name}] Starting reply linking...")
        await reply_linker.link_replies(entity_id_str, entity_export_path)
        logger.info(f"[{target.name}] Reply linking finished.")

        # Final cache save for this entity's processing cycle
        await cache_manager.save_cache()
        logger.info(f"[{target.name}] Final cache saved.")

    except TelegramConnectionError as e:
         logger.error(f"[{target.name}] Telegram connection error during export: {e}")
         # Decide if partial cache save is useful
         await cache_manager.save_cache()
    except ExporterError as e:
        logger.error(f"[{target.name}] Exporter error: {e}")
        await cache_manager.save_cache()
    except Exception as e:
        logger.critical(f"[{target.name}] Unexpected critical error during export: {e}", exc_info=True)
        await cache_manager.save_cache() # Attempt to save progress

    logger.info(f"--- Finished export for target: {target.name or entity_id_str} ---")


async def run_export(config: Config):
    """Main export process orchestration for multiple targets."""
    global thread_executor, process_executor

    # Initialize executors
    cpu_count = multiprocessing.cpu_count() or 1
    # Limit process workers more strictly to avoid resource exhaustion
    process_workers = max(1, min(cpu_count - 1, getattr(config, 'max_process_workers', 4)))
    # Allow more threads for I/O
    thread_workers = getattr(config, 'max_workers', min(32, cpu_count * 5))

    try:
        # Use context managers for ProcessPoolExecutor if possible, otherwise manage manually
        process_executor = ProcessPoolExecutor(max_workers=process_workers)
        thread_executor = ThreadPoolExecutor(max_workers=thread_workers)
        asyncio.get_event_loop().set_default_executor(thread_executor) # Set default executor for loop.run_in_executor(None, ...)

        logger.info(f"Using {process_workers} process workers and {thread_workers} thread workers.")

        # Initialize components
        # Cache manager handles all entities
        cache_manager = CacheManager(config.cache_file)
        await cache_manager.load_cache()

        telegram_manager = TelegramManager(config)
        media_processor = MediaProcessor(config, telegram_manager.get_client()) # Pass client instance
        note_generator = NoteGenerator(config) # Consider passing executors if needed
        reply_linker = ReplyLinker(config, cache_manager) # Pass cache manager

        # Connect to Telegram
        await telegram_manager.connect()

        # Handle interactive target selection if needed
        if config.interactive_mode:
            await telegram_manager.run_interactive_selection()
            # Config might have been updated with targets, log them
            logger.info(f"Targets selected interactively: {[t.id for t in config.export_targets]}")
            if not config.export_targets:
                logger.warning("No targets selected in interactive mode. Exiting.")
                return

        if not config.export_targets:
            logger.error("No export targets specified in config or selected interactively. Exiting.")
            return

        # Process each target sequentially (can be parallelized further if needed)
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
        # Ensure resources are cleaned up
        if thread_executor:
            thread_executor.shutdown(wait=False, cancel_futures=True) # Python 3.9+ cancel_futures
        if process_executor:
            process_executor.shutdown(wait=False, cancel_futures=True) # Python 3.9+ cancel_futures

        if 'telegram_manager' in locals() and telegram_manager:
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

    # Set higher stack size if needed (useful for deep recursion or complex libs)
    try:
        import resource
        # Set soft limit to hard limit, up to a max (e.g., 16MB)
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        new_soft = min(hard, 16 * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_STACK, (new_soft, hard))
        logger.debug(f"Set stack limit to {new_soft / 1024 / 1024} MB")
    except (ImportError, ValueError, OSError) as e:
         logger.debug(f"Could not adjust stack limit: {e} (Not available on Windows)")


    try:
        await run_export(config)
    except Exception as e:
         # Catchall for unexpected errors during run_export not handled internally
         logger.critical(f"Unhandled exception during main execution: {e}", exc_info=True)
         sys.exit(1)

if __name__ == "__main__":
    # Required for Windows asyncio with ProcessPoolExecutor
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")
        sys.exit(0)
    except Exception as e:
        # Final catch-all for errors during asyncio.run() or loop setup
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        # traceback.print_exc() # Uncomment for detailed traceback
        sys.exit(1)
