import asyncio
from typing import Optional, List, AsyncGenerator
from telethon import TelegramClient
from telethon.tl.types import Message
from telethon.errors import (
    FloodWaitError, ChannelPrivateError, UserDeactivatedBanError,
    AuthKeyError, RpcCallFailError, ChannelInvalidError, SessionPasswordNeededError
)
from telethon.sessions import StringSession # Or use file session
from src.config import Config
from src.utils import logger

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
            # system_version="4.16.30-vxCUSTOM", # Optional: Mimic official client
            # device_model="Desktop",            # Optional
            # app_version="4.8.1 x64"            # Optional
        )
        self.target_entity = None

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
            # Resolve target entity
            self.target_entity = await self.client.get_entity(self.config.telegram_channel)
            logger.info(f"Target channel resolved: {getattr(self.target_entity, 'title', self.target_entity.id)}")

        except (AuthKeyError, UserDeactivatedBanError) as e:
             logger.error(f"Authentication error: {e}. Session might be invalid or account banned. Delete session file ('{self.config.session_name}.session') and try again.")
             raise
        except ConnectionError as e:
             logger.error(f"Connection error: {e}. Check network connection.")
             raise
        except Exception as e:
            logger.error(f"Failed to connect or authorize Telegram client: {e}", exc_info=self.config.verbose)
            raise # Propagate error to stop execution

    async def disconnect(self):
        """Disconnects the Telegram client."""
        if self.client and self.client.is_connected():
            logger.info("Disconnecting Telegram client...")
            await self.client.disconnect()
            logger.info("Telegram client disconnected.")

    async def fetch_messages(self, min_id: Optional[int] = None) -> AsyncGenerator[Message, None]:
        """Fetches messages from the target channel, handling pagination and errors."""
        if not self.target_entity:
            logger.error("Target entity not resolved. Cannot fetch messages.")
            return

        logger.info(f"Fetching messages from channel {self.config.telegram_channel}...")
        if min_id:
            logger.info(f"Fetching only messages with ID greater than {min_id}")

        # Fetch messages in reverse order (newest first) by default.
        # If we need oldest first for processing, we can fetch all and reverse,
        # or fetch in chunks and process oldest chunk first.
        # Let's fetch all relevant messages first, then sort and process.
        # Using iter_messages with reverse=True fetches oldest first.

        total_fetched = 0
        try:
            # Use reverse=True to get messages from oldest to newest
            # Note: Telethon's batch_size parameter was removed/changed.
            # Control is mainly through wait_time now. limit=None fetches all.
            async for message in self.client.iter_messages(
                self.target_entity,
                limit=None, # Fetch all messages matching criteria
                offset_id=0, # Start from the beginning
                reverse=True, # Fetch oldest first
                min_id=min_id or 0, # Start from min_id if specified
                wait_time=self.config.request_delay # Use configured delay between internal requests
                # batch_size=self.config.message_batch_size # Deprecated/Removed in recent versions
            ):
                # Basic filtering (ignore service messages, etc.) - adjust as needed
                if not message or message.action:
                    continue
                total_fetched += 1
                if total_fetched % 100 == 0: # Log progress periodically
                     logger.info(f"Fetched {total_fetched} messages so far (ID: {message.id})...")
                yield message

            logger.info(f"Finished fetching messages. Total fetched: {total_fetched}")

        except FloodWaitError as e:
            logger.warning(f"Flood wait encountered. Waiting for {e.seconds} seconds.")
            await asyncio.sleep(e.seconds + 5) # Wait extra time
            # Consider resuming fetch, but for simplicity, we might just stop here or re-raise
            raise # Re-raise to potentially retry later or stop execution
        except (ChannelPrivateError, ChannelInvalidError) as e:
            logger.error(f"Cannot access channel {self.config.telegram_channel}: {e}. Check permissions or channel ID/username.")
            raise
        except RpcCallFailError as e:
             logger.error(f"Telegram API RPC call failed: {e}. This might be temporary.")
             # Implement retry logic here if desired
             raise
        except Exception as e:
            logger.error(f"An unexpected error occurred during message fetching: {e}", exc_info=self.config.verbose)
            raise

    # Expose the client instance for direct use if needed (e.g., media download)
    def get_client(self) -> TelegramClient:
        return self.client
