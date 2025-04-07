import asyncio
import sys
from src.config import load_config, Config
from src.utils import setup_logging, logger
from src.cache_manager import CacheManager
from src.telegram_client import TelegramManager
from src.media_processor import MediaProcessor
from src.note_generator import NoteGenerator
from src.reply_linker import ReplyLinker
from src.exceptions import ExporterError, ConfigError, TelegramConnectionError

async def run_export(config: Config):
    """Main export process orchestration."""
    cache_manager = CacheManager(config.cache_file)
    await cache_manager.load_cache()

    telegram_manager = TelegramManager(config)
    try:
        await telegram_manager.connect()
    except Exception as e:
        logger.critical(f"Failed to initialize Telegram connection: {e}. Exiting.")
        raise TelegramConnectionError("Telegram connection failed") from e

    media_processor = MediaProcessor(config, telegram_manager.get_client())
    note_generator = NoteGenerator(config)
    reply_linker = ReplyLinker(config, cache_manager)

    last_processed_id = None
    if config.only_new:
        last_processed_id = cache_manager.get_last_processed_message_id()
        logger.info(f"Running in 'only_new' mode. Will fetch messages after ID: {last_processed_id}")

    messages_to_process = []
    try:
        async for message in telegram_manager.fetch_messages(min_id=last_processed_id):
            # Double check against cache even if min_id is used, in case of partial runs
            if not cache_manager.is_processed(message.id):
                messages_to_process.append(message)
            else:
                logger.trace(f"Message {message.id} already in cache, skipping processing.")

        # Ensure messages are sorted by date (oldest first) - fetch_messages with reverse=True should handle this
        # messages_to_process.sort(key=lambda m: m.date) # Uncomment if fetch order isn't guaranteed

        logger.info(f"Found {len(messages_to_process)} new messages to process.")

        for message in messages_to_process:
            logger.info(f"Processing message ID: {message.id} Date: {message.date}")

            # 1. Process Media
            media_links = []
            if config.media_download:
                try:
                    media_links = await media_processor.process_media(message, config.obsidian_path) # Pass base path for relative link calc
                except Exception as e:
                    logger.error(f"Failed to process media for message {message.id}: {e}", exc_info=config.verbose)
                    # Add placeholder even on failure
                    media_links.append(("[media processing error]", None))


            # 2. Create Note
            note_path = await note_generator.create_note(message, media_links)

            # 3. Update Cache
            if note_path:
                # Store relative path or just filename in cache? Filename seems sufficient if structure is fixed.
                note_filename = note_path.name
                reply_to_id = getattr(message.reply_to, 'reply_to_msg_id', None)
                cache_manager.add_processed_message(message.id, note_filename, reply_to_id)
            else:
                logger.error(f"Skipping cache update for message {message.id} due to note creation failure.")

            # Save cache periodically? Or just at the end? Saving at the end is simpler.
            # Consider saving every N messages for very long runs.

        logger.info("Finished processing individual messages.")

        # 4. Link Replies (after all notes are potentially created)
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
        await telegram_manager.disconnect()
        logger.info("Export process finished.")


async def main():
    try:
        config = load_config()
        setup_logging(config.verbose)
    except (ValueError, ConfigError) as e:
        # Logger might not be set up yet if config fails early
        print(f"ERROR: Configuration failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
         print(f"ERROR: Unexpected error during setup: {e}", file=sys.stderr)
         sys.exit(1)


    try:
        await run_export(config)
    except TelegramConnectionError:
         # Already logged in run_export or connect
         sys.exit(1)
    except Exception as e:
         # Catchall for unexpected errors during run_export not handled internally
         logger.critical(f"Unhandled exception in main execution: {e}", exc_info=True)
         sys.exit(1)


if __name__ == "__main__":
    # On Windows, default asyncio event loop policy might cause issues with subprocesses (like ffmpeg)
    # if sys.platform == "win32":
    #      asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # Consider if needed based on testing. Often Selector loop is fine.

    asyncio.run(main())
