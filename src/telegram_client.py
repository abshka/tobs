import asyncio
import concurrent.futures
from typing import Optional, AsyncGenerator, List, Dict, Any
from telethon import TelegramClient
from telethon.tl.types import Message
from telethon.errors import (
    FloodWaitError, ChannelPrivateError, UserDeactivatedBanError,
    AuthKeyError, RpcCallFailError, ChannelInvalidError, SessionPasswordNeededError
)
from src.config import Config
from src.utils import logger
import os

class TelegramManager:
    def __init__(self, config: Config):
        self.config = config
        # Use StringSession if PHONE_NUMBER is not provided (e.g., for bots or pre-authorized sessions)
        # session = StringSession(config.session_string) if config.session_string else config.session_name
        # For user accounts, file session is usually easier to start with:
        self.client = TelegramClient(
            config.session_name,
            config.api_id,
            config.api_hash,
            connection_retries=10,  # More connection retries
            retry_delay=1  # Shorter retry delay for faster recovery
        )
        self.target_entity = None
        # Thread pool for parallel downloads - using more workers for better parallelism
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.max_workers if hasattr(config, 'max_workers') else min(32, (os.cpu_count() or 4) * 5)
        )
        # Process pool for CPU-intensive operations
        self.process_pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=min(8, (os.cpu_count() or 2))
        )
        # Task semaphore to limit concurrent API operations and avoid flood wait
        self.semaphore = asyncio.Semaphore(10)  # Limit concurrent operations
        # Cache for resolved entities to avoid redundant API calls
        self.entity_cache = {}

    async def connect(self):
        """Connects and authenticates the Telegram client."""
        logger.info("Connecting to Telegram...")
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                logger.info("First time login or session expired.")
                if self.config.phone_number:
                    await self.client.send_code_request(self.config.phone_number)
                    code = input("Enter the code you received: ")
                    try:
                         # Try signing in with phone and code
                         await self.client.sign_in(self.config.phone_number, code)
                    except SessionPasswordNeededError:
                         # If 2FA is enabled, prompt for the password
                         logger.info("Two-step verification is enabled.")
                         password = input("Enter your two-step verification password: ")
                         try:
                             await self.client.sign_in(password=password)
                         except Exception as e:
                             logger.error(f"Failed to sign in with password: {e}")
                             raise # Re-raise after logging password failure
                    except Exception as e:
                         # Catch other potential errors during code sign-in
                         logger.error(f"Failed to sign in with code: {e}")
                         raise # Re-raise after logging code failure
                else:
                    # Handle bot token login if applicable, or raise error if no auth method possible
                    # await self.client.start(bot_token=config.bot_token)
                    raise ConnectionError("Cannot authorize: Phone number not provided in .env and session is invalid.")

            logger.info("Telegram client connected and authorized.")

            # Using a background task to resolve the entity for minimal blocking
            self.target_entity = await asyncio.create_task(self._resolve_entity(self.config.telegram_channel))
            logger.info(f"Target channel resolved: {getattr(self.target_entity, 'title', getattr(self.target_entity, 'id', 'unknown'))}")

        except (AuthKeyError, UserDeactivatedBanError) as e:
             logger.error(f"Authentication error: {e}. Session might be invalid or account banned. Delete session file ('{self.config.session_name}.session') and try again.")
             raise
        except ConnectionError as e:
             logger.error(f"Connection error: {e}. Check network connection.")
             raise
        except Exception as e:
            logger.error(f"Failed to connect or authorize Telegram client: {e}", exc_info=self.config.verbose)
            raise # Propagate error to stop execution

    async def _resolve_entity(self, entity_identifier):
        """Resolve entity with caching for better performance"""
        if entity_identifier in self.entity_cache:
            return self.entity_cache[entity_identifier]

        async with self.semaphore:
            entity = await self.client.get_entity(entity_identifier)
            self.entity_cache[entity_identifier] = entity
            return entity

    async def disconnect(self):
        """Disconnects the Telegram client."""
        if self.client and self.client.is_connected():
            logger.info("Disconnecting Telegram client...")
            # Create tasks for cleanup operations
            disconnect_task = asyncio.create_task(self.client.disconnect())

            # Shutdown thread pools gracefully
            await asyncio.get_event_loop().run_in_executor(None, lambda: self.thread_pool.shutdown(wait=False))
            await asyncio.get_event_loop().run_in_executor(None, lambda: self.process_pool.shutdown(wait=False))

            # Wait for client disconnect
            await disconnect_task
            logger.info("Telegram client disconnected.")

    async def fetch_messages(self, min_id: Optional[int] = None) -> AsyncGenerator[Message, None]:
        """Fetches messages from the target channel, handling pagination and errors."""
        if not self.target_entity:
            logger.error("Target entity not resolved. Cannot fetch messages.")
            return

        logger.info(f"Fetching messages from channel {self.config.telegram_channel}...")
        if min_id:
            logger.info(f"Fetching only messages with ID greater than {min_id}")

        total_fetched = 0
        retry_count = 0
        max_retries = 3

        while retry_count <= max_retries:
            try:
                # Use reverse=True to get messages from oldest to newest
                # Note: Telethon's batch_size parameter was removed/changed.
                # Control is mainly through wait_time now. limit=None fetches all.
                async with self.semaphore:
                    async for message in self.client.iter_messages(
                        entity=self.target_entity,
                        limit=100000,  # Large limit instead of None for type compatibility
                        offset_id=0, # Start from the beginning
                        reverse=True, # Fetch oldest first
                        min_id=min_id or 0, # Start from min_id if specified
                        wait_time=self.config.request_delay # Use configured delay between internal requests
                    ):
                        # Basic filtering (ignore service messages, etc.) - adjust as needed
                        if not message or message.action:
                            continue
                        total_fetched += 1
                        if total_fetched % 100 == 0: # Log progress periodically
                            logger.info(f"Fetched {total_fetched} messages so far (ID: {message.id})...")
                        yield message

                logger.info(f"Finished fetching messages. Total fetched: {total_fetched}")
                break  # Exit loop on successful completion

            except FloodWaitError as e:
                logger.warning(f"Flood wait encountered. Waiting for {e.seconds} seconds.")
                await asyncio.sleep(e.seconds + 5) # Wait extra time
                retry_count += 1
                if retry_count <= max_retries:
                    logger.info(f"Retrying fetch (attempt {retry_count}/{max_retries})...")
                else:
                    logger.error("Max retries exceeded for flood wait.")
                    raise
            except (ChannelPrivateError, ChannelInvalidError) as e:
                logger.error(f"Cannot access channel {self.config.telegram_channel}: {e}. Check permissions or channel ID/username.")
                raise
            except RpcCallFailError as e:
                logger.error(f"Telegram API RPC call failed: {e}. This might be temporary.")
                retry_count += 1
                if retry_count <= max_retries:
                    await asyncio.sleep(5 * retry_count)  # Exponential backoff
                    logger.info(f"Retrying after RPC failure (attempt {retry_count}/{max_retries})...")
                else:
                    raise
            except Exception as e:
                logger.error(f"An unexpected error occurred during message fetching: {e}", exc_info=self.config.verbose)
                raise

    async def fetch_messages_batch(self, min_id: Optional[int] = None, batch_size: int = 100) -> AsyncGenerator[List[Message], None]:
        """Fetch messages in batches for parallel processing with optimized batching strategy"""
        messages = []
        batch_start_time = asyncio.get_event_loop().time()

        async for message in self.fetch_messages(min_id=min_id):
            messages.append(message)

            # Yield batch if we've reached batch_size or time threshold (adaptive batching)
            current_time = asyncio.get_event_loop().time()
            time_elapsed = current_time - batch_start_time

            if len(messages) >= batch_size or (len(messages) > 0 and time_elapsed > 2.0):
                yield messages
                messages = []
                batch_start_time = current_time

                # Small sleep to allow other tasks to run
                await asyncio.sleep(0.01)

        if messages:  # Don't forget the last batch
            yield messages

    async def download_media_parallel(self, messages, download_folder):
        """Download media from multiple messages in parallel with optimized concurrency"""
        if not messages:
            return []

        # Determine optimal concurrent downloads based on message count
        # but limit to avoid API flooding
        concurrent_limit = min(len(messages), 20)
        semaphore = asyncio.Semaphore(concurrent_limit)

        async def download_with_limit(message):
            if not message.media:
                return None

            async with semaphore:
                return await self._download_single_media(message, download_folder)

        # Create tasks for all messages with media at once
        tasks = [
            asyncio.create_task(download_with_limit(message))
            for message in messages if message.media
        ]

        if not tasks:
            return []

        # Process downloads in batches to avoid memory issues with very large sets
        results = []
        for i in range(0, len(tasks), 50):
            batch = tasks[i:i+50]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)

            # Process exceptions
            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Download error in batch {i//50}: {result}")
                    batch_results[j] = None

            results.extend(batch_results)

            # Small delay between batches
            if i + 50 < len(tasks):
                await asyncio.sleep(0.5)

        return [r for r in results if r is not None]

    async def _download_single_media(self, message, download_folder):
        """Download media from a single message using a thread pool with better error handling"""
        try:
            # Check if media exists and create appropriate filename
            filename = f"{download_folder}/{message.id}_{int(message.date.timestamp())}"

            # Run the potentially blocking download in a thread pool
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(
                self.thread_pool,
                lambda: self.client.download_media(message.media, filename)
            )

            if not path:
                logger.warning(f"No media downloaded for message {message.id}")
                return None

            return {
                'message_id': message.id,
                'path': path,
                'date': message.date.isoformat(),
                'success': True
            }

        except asyncio.CancelledError:
            logger.warning(f"Download for message {message.id} was cancelled")
            raise
        except Exception as e:
            logger.error(f"Failed to download media from message {message.id}: {e}")
            return {
                'message_id': message.id,
                'error': str(e),
                'success': False
            }

    async def process_media_metadata(self, media_paths: List[str]) -> List[Dict[str, Any]]:
        """Process media metadata in parallel using process pool for CPU-intensive operations"""
        if not media_paths:
            return []

        loop = asyncio.get_event_loop()
        # Delegate CPU-intensive metadata extraction to process pool
        results = await asyncio.gather(*[
            loop.run_in_executor(self.process_pool, self._extract_metadata, path)
            for path in media_paths if path
        ])

        return [r for r in results if r]

    def _extract_metadata(self, media_path: str) -> Dict[str, Any]:
        """Extract metadata from a media file (CPU-intensive operation for process pool)"""
        try:
            # Placeholder for actual metadata extraction logic
            # In a real implementation, this would analyze the file
            return {
                'path': media_path,
                'size': os.path.getsize(media_path) if os.path.exists(media_path) else 0,
                'type': os.path.splitext(media_path)[1] if media_path else None
            }
        except Exception as e:
            logger.error(f"Failed to extract metadata for {media_path}: {e}")
            return None

    # Expose the client instance for direct use if needed (e.g., media download)
    def get_client(self) -> TelegramClient:
        return self.client
