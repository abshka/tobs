import asyncio
import sys
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from typing import List, Optional
from src.config import load_config, Config
from src.utils import setup_logging, logger
from src.cache_manager import CacheManager
from src.telegram_client import TelegramManager
from src.media_processor import MediaProcessor
from src.note_generator import NoteGenerator
from src.reply_linker import ReplyLinker
from src.exceptions import ExporterError, ConfigError, TelegramConnectionError
import functools
import os

# Helper function to run CPU-bound tasks in process pool
def run_in_process_pool(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(
        process_pool_executor,
        functools.partial(func, *args, **kwargs)
    )

# Global process pool executor for CPU-bound tasks
process_pool_executor = None

async def process_message(message, media_processor, note_generator, cache_manager, config, thread_executor, process_executor):
    """Process a single message with maximized parallelism for both I/O and CPU-bound tasks."""
    logger.info(f"Processing message ID: {message.id} Date: {message.date}")

    # 1. Process Media - I/O bound task with potential CPU work for preprocessing
    media_links_task = asyncio.create_task(process_media(message, media_processor, config))

    # Start other tasks in parallel while media processing happens
    other_tasks = []

    # 2. Pre-fetch any data needed for note creation in parallel
    # Assuming there might be some preparatory work that can be done independently
    if hasattr(note_generator, 'prepare_note_data'):
        prepare_task = asyncio.create_task(note_generator.prepare_note_data(message))
        other_tasks.append(prepare_task)

    # Wait for media processing to complete
    media_links = await media_links_task

    # Wait for any other parallel prep tasks
    if other_tasks:
        await asyncio.gather(*other_tasks)

    # 2. Create Note - Can be CPU-bound for complex formatting/rendering
    # Offload to process pool if it's CPU intensive
    if getattr(config, 'use_process_pool_for_notes', False):
        loop = asyncio.get_event_loop()
        note_path = await loop.run_in_executor(
            process_executor,
            functools.partial(note_generator.create_note_sync, message, media_links)
        )
    else:
        note_path = await note_generator.create_note(message, media_links)

    # 3. Update Cache - Quick operation, just do it directly
    if note_path:
        note_filename = note_path.name
        reply_to_id = getattr(message.reply_to, 'reply_to_msg_id', None) if hasattr(message, "reply_to") else None
        cache_manager.add_processed_message(message.id, note_filename, reply_to_id)

        # 4. Trigger any post-processing tasks in parallel that don't affect result
        if hasattr(note_generator, 'post_process_note'):
            asyncio.create_task(note_generator.post_process_note(note_path))
    else:
        logger.error(f"Skipping cache update for message {message.id} due to note creation failure.")

    return message.id

async def process_media(message, media_processor, config):
    """Process media files with proper error handling."""
    media_links = []
    if config.media_download:
        try:
            # Process media using async method for parallel processing
            media_links = await media_processor.process_media_async(message, config.obsidian_path)
        except Exception as e:
            logger.error(f"Failed to process media for message {message.id}: {e}", exc_info=config.verbose)
            # Add placeholder even on failure
            media_links.append(("[media processing error]", None))
    return media_links

async def run_export(config: Config):
    """Main export process orchestration with maximum parallelism."""
    global process_pool_executor

    # Initialize process pool for CPU-bound tasks
    cpu_count = multiprocessing.cpu_count()
    process_workers = max(1, min(cpu_count - 1, 4))  # Use up to N-1 cores, max 4
    process_pool_executor = ProcessPoolExecutor(max_workers=process_workers)

    # Create thread pool for I/O-bound tasks
    thread_workers = getattr(config, 'max_workers', min(32, cpu_count*4))  # Much higher thread count for I/O work
    thread_executor = ThreadPoolExecutor(max_workers=thread_workers)

    logger.info(f"Created process pool with {process_workers} workers for CPU-bound tasks")
    logger.info(f"Created thread pool with {thread_workers} workers for I/O-bound tasks")

    # Initialize components with shared semaphores to prevent resource exhaustion
    max_concurrent_downloads = getattr(config, 'max_concurrent_downloads', 20)
    media_semaphore = asyncio.Semaphore(max_concurrent_downloads)

    cache_manager = CacheManager(config.cache_file)
    await cache_manager.load_cache()

    telegram_manager = TelegramManager(config)
    try:
        # Start connecting to Telegram
        connect_task = asyncio.create_task(telegram_manager.connect())

        # While connection is being established, perform other setup tasks in parallel
        setup_tasks = []

        # Load any additional resources asynchronously if needed
        if hasattr(config, 'preload_resources') and config.preload_resources:
            # This is a placeholder for potential resource preloading
            setup_tasks.append(asyncio.create_task(preload_resources(config)))

        # Wait for Telegram connection to complete
        await connect_task

        # Wait for any other setup tasks to complete
        if setup_tasks:
            await asyncio.gather(*setup_tasks)

    except Exception as e:
        logger.critical(f"Failed to initialize Telegram connection: {e}. Exiting.")
        raise TelegramConnectionError("Telegram connection failed") from e

    media_processor = MediaProcessor(config, telegram_manager.get_client(), semaphore=media_semaphore)
    note_generator = NoteGenerator(config)
    reply_linker = ReplyLinker(config, cache_manager)

    last_processed_id = None
    if config.only_new:
        last_processed_id = cache_manager.get_last_processed_message_id()
        logger.info(f"Running in 'only_new' mode. Will fetch messages after ID: {last_processed_id}")

    try:
        # Get the maximum number of concurrent workers
        max_workers = getattr(config, 'max_concurrent_processes', 8)
        # Create a semaphore to limit the number of concurrent tasks
        semaphore = asyncio.Semaphore(max_workers)

        # Create a queue of tasks that are being processed
        active_tasks = set()
        processed_count = 0

        # Use this to periodically save cache
        last_cache_save = 0
        cache_save_interval = getattr(config, 'cache_save_interval', 50)  # Save every 50 messages by default

        logger.info(f"Starting continuous processing with {max_workers} concurrent workers")

        # Process messages as they come in, continuously assigning new tasks as workers become available
        async for message in telegram_manager.fetch_messages(min_id=last_processed_id):
            # Double check against cache even if min_id is used, in case of partial runs
            if not cache_manager.is_processed(message.id):
                # Create a new task for processing this message
                async with semaphore:
                    task = asyncio.create_task(
                        process_message(message, media_processor, note_generator,
                                       cache_manager, config, thread_executor, process_pool_executor)
                    )

                    # Add callback to remove the task from active_tasks when complete
                    task.add_done_callback(lambda t: active_tasks.discard(t))
                    active_tasks.add(task)

                processed_count += 1

                # Periodically save cache
                if processed_count - last_cache_save >= cache_save_interval:
                    await cache_manager.save_cache()
                    last_cache_save = processed_count
                    logger.info(f"Saved cache after processing {processed_count} messages")

                    # Log progress
                    completed = processed_count - len(active_tasks)
                    logger.info(f"Progress: {completed} messages completed, {len(active_tasks)} messages in progress")
            else:
                logger.trace(f"Message {message.id} already in cache, skipping processing.")

        # Wait for all remaining tasks to complete
        if active_tasks:
            logger.info(f"Waiting for {len(active_tasks)} remaining tasks to complete")
            await asyncio.gather(*active_tasks)

        logger.info(f"Processed total of {processed_count} messages.")

        # 4. Link Replies (after all notes are created)
        await reply_linker.link_replies()

        # 5. Final Cache Save
        await cache_manager.save_cache()

    except TelegramConnectionError as e:
         logger.error(f"Telegram API or connection error during processing: {e}")
         # Decide whether to save partial cache or not
         await cache_manager.save_cache() # Save progress made so far
    except ExporterError as e:
        logger.error(f"An exporter error occurred: {e}")
        await cache_manager.save_cache() # Save progress
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
        # Attempt to save cache even on unexpected errors
        try:
             await cache_manager.save_cache()
        except Exception as cache_e:
             logger.error(f"Failed to save cache during critical error handling: {cache_e}")
    finally:
        thread_executor.shutdown(wait=False)
        process_pool_executor.shutdown(wait=False)
        await telegram_manager.disconnect()
        logger.info("Export process finished.")

async def preload_resources(config):
    """Preload any resources needed for processing."""
    # Placeholder for potential resource preloading
    await asyncio.sleep(0.1)  # Simulate some async work

async def main():
    try:
        # Load config in main thread
        config = load_config()

        # Setup logging
        setup_logging(config.verbose)

        # Run CPU-intensive initialization tasks in parallel if any exist
        init_tasks = []

        # Wait for any initialization tasks
        if init_tasks:
            await asyncio.gather(*init_tasks)

    except (ValueError, ConfigError) as e:
        # Logger might not be set up yet if config fails early
        print(f"ERROR: Configuration failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
         print(f"ERROR: Unexpected error during setup: {e}", file=sys.stderr)
         sys.exit(1)

    try:
        # Run the export function directly instead of wrapping it in a Task
        await run_export(config)
    except TelegramConnectionError:
         # Already logged in run_export or connect
         sys.exit(1)
    except Exception:
         # Catchall for unexpected errors during run_export not handled internally
         # logger.critical(f"Unhandled exception in main execution: {e}", exc_info=True)
         sys.exit(1)

if __name__ == "__main__":
    try:
        # Set the event loop policy to address 'get_default_executor' issue
        # The default loop doesn't have this attribute in some Python versions/platforms
        if sys.platform != "win32":
            # Set larger default thread pool size for the event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        else:
            # On Windows, use the WindowsProactorEventLoopPolicy which should have the required functionality
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        # Increase default thread stack size if needed for complex operations
        import threading
        threading.stack_size(2*1024*1024)  # 2MB stack size

        # Use asyncio.run which properly manages the event loop lifecycle
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error during script execution: {e}", file=sys.stderr)
        sys.exit(1)
