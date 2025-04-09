import asyncio
from typing import Optional, AsyncGenerator, Dict, Union, Any
from telethon import TelegramClient, types
from telethon.tl.types import Message
from telethon.errors import (
    FloodWaitError, ChannelPrivateError, UserDeactivatedBanError, ChatAdminRequiredError, UserNotParticipantError,
    AuthKeyError, RpcCallFailError, ChannelInvalidError, SessionPasswordNeededError, UsernameNotOccupiedError, UsernameInvalidError, PeerIdInvalidError
)
from src.config import Config, ExportTarget
from src.utils import logger
import sys # For exit

class TelegramManager:
    def __init__(self, config: Config):
        self.config = config
        self.client = TelegramClient(
            config.session_name,
            config.api_id,
            config.api_hash,
            # system_version="4.16.30-vxCUSTOM", # Example customization
            device_model="Telegram Markdown Exporter",
            app_version="1.0.0",
            connection_retries=5,
            retry_delay=2,
            request_retries=5 # Retries for API calls themselves
        )
        self.entity_cache: Dict[str, Any] = {} # Cache resolved entities
        self.client_connected = False

    async def connect(self):
        """Connects and authenticates the Telegram client."""
        if self.client_connected:
            logger.info("Client already connected.")
            return True

        logger.info("Connecting to Telegram...")
        try:
            # Start the client
            # Using connect() first allows checking authorization before potentially starting interactively
            await self.client.connect()

            if not await self.client.is_user_authorized():
                logger.info("Authorization required.")
                if self.config.phone_number:
                    try:
                        await self.client.send_code_request(self.config.phone_number)
                        code = input("Enter the code you received from Telegram: ")
                        await self.client.sign_in(self.config.phone_number, code)
                        logger.info("Signed in successfully using code.")
                    except SessionPasswordNeededError:
                        logger.info("Two-step verification (2FA) enabled.")
                        password = input("Enter your 2FA password: ")
                        try:
                            await self.client.sign_in(password=password)
                            logger.info("Signed in successfully using 2FA password.")
                        except Exception as pwd_err:
                            logger.critical(f"Failed to sign in with 2FA password: {pwd_err}")
                            raise TelegramConnectionError(f"2FA sign-in failed: {pwd_err}") from pwd_err
                    except Exception as e:
                        logger.critical(f"Failed to sign in with code: {e}")
                        raise TelegramConnectionError(f"Code sign-in failed: {e}") from e
                else:
                    # Bot token or other methods would go here if implemented
                    # e.g., await self.client.start(bot_token=self.config.bot_token)
                    logger.critical("Cannot authorize: Phone number not provided in config/env and session is invalid.")
                    raise TelegramConnectionError("Phone number required for authorization.")

            # Double-check authorization status after sign-in attempts
            if not await self.client.is_user_authorized():
                 logger.critical("Authorization failed even after sign-in attempt.")
                 raise TelegramConnectionError("Authorization failed.")

            me = await self.client.get_me()
            username = getattr(me, 'username', None) or getattr(me, 'first_name', 'Unknown User')
            logger.info(f"Telegram client connected and authorized as: {username} (ID: {me.id})")
            self.client_connected = True
            return True

        except (AuthKeyError, UserDeactivatedBanError) as e:
             logger.error(f"Authentication error: {e}. Session file '{self.config.session_name}.session' might be invalid or account banned. Delete the session file and try again.")
             raise TelegramConnectionError(f"Authentication error: {e}") from e
        except ConnectionError as e:
             # This often wraps other errors like Gaierror
             logger.error(f"Network connection error: {e}. Check internet connection and Telegram availability.")
             raise TelegramConnectionError(f"Network error: {e}") from e
        except TelegramConnectionError: # Re-raise specific connection errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting/authorizing Telegram client: {e}", exc_info=self.config.verbose)
            raise TelegramConnectionError(f"Unexpected connection error: {e}") from e

    async def disconnect(self):
        """Disconnects the Telegram client gracefully."""
        if self.client and self.client.is_connected():
            logger.info("Disconnecting Telegram client...")
            try:
                await self.client.disconnect()
                self.client_connected = False
                logger.info("Telegram client disconnected.")
            except Exception as e:
                logger.error(f"Error during client disconnection: {e}")
        elif self.client:
            logger.info("Telegram client was not connected.")
        else:
            logger.info("Telegram client object not available.")


    async def resolve_entity(self, entity_identifier: Union[str, int]) -> Optional[Any]:
        """Resolves a user, chat, or channel identifier to a Telethon Peer object with caching."""
        entity_identifier_str = str(entity_identifier).strip()
        if not entity_identifier_str:
            logger.warning("Attempted to resolve an empty entity identifier.")
            return None

        # Check cache first
        if entity_identifier_str in self.entity_cache:
            return self.entity_cache[entity_identifier_str]

        logger.debug(f"Resolving entity: {entity_identifier_str}")
        attempts = 0
        max_attempts = 2 # Try resolving a couple of times for transient issues

        while attempts < max_attempts:
            attempts += 1
            try:
                # Use get_input_entity for broader compatibility, then get_entity if needed
                # Sometimes get_entity works better for usernames, get_input_entity for IDs
                try:
                    # Try resolving (handles usernames, phone numbers, IDs, links)
                    entity = await self.client.get_entity(entity_identifier_str)
                except ValueError: # Often means it's not a username/link, try as ID
                    if entity_identifier_str.lstrip('-').isdigit():
                         entity = await self.client.get_entity(int(entity_identifier_str))
                    else:
                        raise # Reraise original ValueError if not a digit

                if entity:
                    logger.debug(f"Resolved '{entity_identifier_str}' to entity: {getattr(entity, 'title', getattr(entity, 'username', entity.id))}")
                    self.entity_cache[entity_identifier_str] = entity # Cache successful result
                    return entity
                else:
                    # Should not happen if get_entity doesn't raise error, but check anyway
                    logger.warning(f"get_entity resolved '{entity_identifier_str}' to None/False.")
                    return None

            except FloodWaitError as e:
                logger.warning(f"Flood wait encountered while resolving entity '{entity_identifier_str}'. Waiting {e.seconds}s...")
                await asyncio.sleep(e.seconds + 1)
                # Continue loop to retry
            except (UsernameNotOccupiedError, UsernameInvalidError, PeerIdInvalidError, ValueError, TypeError) as e:
                # Specific errors indicating the entity likely doesn't exist or identifier is wrong format
                logger.error(f"Could not resolve entity '{entity_identifier_str}': {type(e).__name__}. Check the ID/username/link.")
                return None # Don't retry if identifier seems fundamentally wrong
            except (ChannelPrivateError, ChatAdminRequiredError, UserNotParticipantError) as e:
                logger.error(f"Cannot access entity '{entity_identifier_str}': {type(e).__name__}. Check permissions or participation.")
                return None # Don't retry permission errors
            except RpcCallFailError as e:
                 logger.warning(f"Telegram API RPC call failed while resolving '{entity_identifier_str}': {e}. Retrying if possible...")
                 await asyncio.sleep(1 * attempts) # Simple backoff before retry
            except Exception as e:
                logger.error(f"Unexpected error resolving entity '{entity_identifier_str}': {e}", exc_info=self.config.verbose)
                await asyncio.sleep(1 * attempts) # Simple backoff before retry

        logger.error(f"Failed to resolve entity '{entity_identifier_str}' after {max_attempts} attempts.")
        return None

    async def fetch_messages(
        self,
        entity: Any, # Use resolved entity object
        min_id: Optional[int] = None
        ) -> AsyncGenerator[Message, None]:
        """Fetches messages from the target entity asynchronously, handling pagination and errors."""

        entity_name = getattr(entity, 'title', getattr(entity, 'username', entity.id))
        logger.info(f"Fetching messages from: {entity_name} (ID: {entity.id})")
        if min_id:
            logger.info(f"Fetching messages with ID greater than {min_id}")

        total_fetched = 0
        last_logged_count = 0
        log_interval = 500 # Log progress every N messages

        try:
            # Use iter_messages for efficient pagination
            # Fix: Use limit=None instead of limit=0.0 to fetch all messages
            # The float 0.0 limit was causing no messages to be fetched
            async for message in self.client.iter_messages(
                entity=entity,
                limit=None,  # Fetch all messages matching criteria
                offset_id=0, # Start from the beginning (or min_id effectively handles offset)
                reverse=True, # Fetch oldest first (important for incremental `min_id` logic)
                min_id=min_id or 0, # Telethon handles starting after this ID
                wait_time=self.config.request_delay # Basic delay between internal requests
            ):
                # Basic filtering (can be expanded)
                if not isinstance(message, Message): continue # Skip service messages etc.
                if getattr(message, 'action', None): continue # Skip channel actions

                total_fetched += 1
                if total_fetched - last_logged_count >= log_interval:
                    logger.info(f"[{entity_name}] Fetched {total_fetched} messages so far (last ID: {message.id})...")
                    last_logged_count = total_fetched

                yield message

            logger.info(f"Finished fetching messages from {entity_name}. Total fetched: {total_fetched}")

        except FloodWaitError as e:
            logger.warning(f"[{entity_name}] Flood wait encountered during message fetch. Waiting {e.seconds}s...")
            await asyncio.sleep(e.seconds + 5) # Wait extra time
            # In a real-world scenario, you might want to resume fetching after the wait.
            # For simplicity here, we raise an error to signal the outer loop.
            raise TelegramConnectionError(f"Flood wait received ({e.seconds}s)") from e
        except (ChannelPrivateError, ChannelInvalidError, ChatAdminRequiredError, UserNotParticipantError) as e:
            logger.error(f"[{entity_name}] Cannot access messages: {e}. Check permissions or ID.")
            raise TelegramConnectionError(f"Access error: {e}") from e
        except RpcCallFailError as e:
            logger.error(f"[{entity_name}] Telegram API RPC call failed during message fetch: {e}.")
            raise TelegramConnectionError(f"RPC error: {e}") from e
        except Exception as e:
            logger.error(f"[{entity_name}] An unexpected error occurred during message fetching: {e}", exc_info=self.config.verbose)
            raise ExporterError(f"Unexpected fetch error: {e}") from e


    async def run_interactive_selection(self):
        """Handles the interactive selection of chats/channels to export."""
        if not self.client_connected:
            logger.error("Cannot run interactive selection: Telegram client not connected.")
            await self.connect() # Attempt connection first
            if not self.client_connected:
                return # Exit if connection failed

        me = await self.client.get_me()
        username = getattr(me, 'username', getattr(me, 'first_name', 'User'))
        print(f"\n--- Welcome, {username}! ---")
        print("Select chats or channels to export.")

        while True:
            print("\nOptions:")
            print(" 1. List recent dialogs (Chats, Channels, Users)")
            print(" 2. Enter ID/Username/Link manually")
            print(" 3. Finish selection and start export")
            print(" 4. Exit")
            choice = input("Choose an option (1-4): ").strip()

            if choice == '1':
                await self._list_and_select_dialogs()
            elif choice == '2':
                await self._select_dialog_manually()
            elif choice == '3':
                if not self.config.export_targets:
                    print("\nNo targets selected. Please select at least one target before starting.")
                    continue
                else:
                    print("\nFinished selection. Starting export process...")
                    break # Exit selection loop
            elif choice == '4':
                print("Exiting.")
                sys.exit(0)
            else:
                print("Invalid choice. Please enter a number between 1 and 4.")

    async def _list_and_select_dialogs(self):
        """Lists recent dialogs and allows user selection."""
        print("\nFetching recent dialogs...")
        try:
            dialogs = await self.client.get_dialogs(limit=20) # Get recent 20 dialogs
            if not dialogs:
                print("No dialogs found.")
                return

            print("Recent Dialogs:")
            dialog_map = {}
            for i, dialog in enumerate(dialogs):
                entity = dialog.entity
                entity_id = getattr(entity, 'id', 'N/A')
                entity_type = type(entity).__name__.replace('Channel', 'Channel/Group').replace('User', 'User').replace('Chat', 'Group') # Basic type name
                print(f" {i+1}. {dialog.name} (Type: {entity_type}, ID: {entity_id})")
                dialog_map[i+1] = entity # Map index to entity object

            while True:
                selection = input("Enter numbers of dialogs to add (e.g., 1, 3, 5), or 'c' to cancel: ").strip()
                if selection.lower() == 'c':
                    break
                try:
                    indices = [int(s.strip()) for s in selection.split(',') if s.strip()]
                    added_count = 0
                    for index in indices:
                        if index in dialog_map:
                             entity = dialog_map[index]
                             target = ExportTarget(id=entity.id, name=getattr(entity, 'title', getattr(entity, 'username', str(entity.id))))
                             # Refine type based on Telethon type
                             if isinstance(entity, types.User): target.type = 'user'
                             elif isinstance(entity, types.Chat): target.type = 'group' # Basic group
                             elif isinstance(entity, types.Channel): target.type = 'channel' # Channel or Supergroup
                             else: target.type = 'unknown'

                             self.config.add_export_target(target)
                             print(f"Added: {target.name or target.id}")
                             added_count += 1
                        else:
                            print(f"Invalid selection: {index}")
                    if added_count > 0:
                         break # Exit after successful addition
                except ValueError:
                    print("Invalid input. Please enter numbers separated by commas.")

        except Exception as e:
            logger.error(f"Failed to list dialogs: {e}", exc_info=self.config.verbose)
            print(f"Error fetching dialogs: {e}")

    async def _select_dialog_manually(self):
        """Allows user to enter an ID, username, or link manually."""
        while True:
            identifier = input("\nEnter Chat/Channel ID, @username, or t.me/ link (or 'c' to cancel): ").strip()
            if identifier.lower() == 'c':
                break
            if not identifier:
                continue

            print(f"Resolving '{identifier}'...")
            entity = await self.resolve_entity(identifier)

            if entity:
                target_name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))
                target_id = entity.id
                target = ExportTarget(id=target_id, name=target_name)
                # Refine type based on Telethon type
                if isinstance(entity, types.User): target.type = 'user'
                elif isinstance(entity, types.Chat): target.type = 'group' # Basic group
                elif isinstance(entity, types.Channel): target.type = 'channel' # Channel or Supergroup
                else: target.type = 'unknown'

                print(f"Resolved: {target.name} (Type: {target.type}, ID: {target.id})")
                confirm = input("Add this target? (y/n): ").strip().lower()
                if confirm == 'y':
                    self.config.add_export_target(target)
                    print(f"Added: {target.name or target.id}")
                    break # Exit after successful addition
            else:
                print(f"Could not resolve or access '{identifier}'. Please check the input and your permissions.")
                # Loop continues for another attempt

    # Expose the client instance for direct use if needed (e.g., media download)
    def get_client(self) -> TelegramClient:
        # Ensure client is initialized, though connection status might vary
        if not self.client:
             raise RuntimeError("TelegramManager not initialized properly.")
        return self.client

# Custom Exceptions
class TelegramConnectionError(Exception):
    pass

class ExporterError(Exception):
    pass
