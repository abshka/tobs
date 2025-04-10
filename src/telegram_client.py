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
import sys

def _read_line_robust(prompt: str = "") -> str:
    """Reads a line from stdin, breaking on CR or LF, handling prompt."""
    if prompt:
        print(prompt, end="", flush=True) # Ensure prompt is displayed

    line_buffer = []
    while True:
        try:
            char = sys.stdin.read(1)
        except KeyboardInterrupt:
             # Re-raise KeyboardInterrupt if Ctrl+C is pressed during read
             print("\nOperation cancelled by user.")
             raise KeyboardInterrupt
        except Exception as e:
             # Handle other potential read errors
             logger.error(f"Error reading input: {e}")
             return "" # Return empty string on error

        if not char:
            # EOF (e.g., if input is redirected from a file and file ends)
            logger.warning("EOF detected while reading input.")
            break
        if char == '\n' or char == '\r':
            # End of line detected
            break # Break on either \n or \r
        line_buffer.append(char)

    # Add a newline to mimic standard input() behavior in the terminal
    print() # Move to the next line after input is received

    return "".join(line_buffer).strip()


class TelegramManager:
    def __init__(self, config: Config):
        self.config = config
        self.client = TelegramClient(
            config.session_name,
            config.api_id,
            config.api_hash,
            device_model="Telegram Markdown Exporter",
            app_version="1.0.0",
            connection_retries=5,
            retry_delay=2,
            request_retries=5
        )
        self.entity_cache: Dict[str, Any] = {}
        self.client_connected = False

    async def connect(self):
        """Connects and authenticates the Telegram client."""
        if self.client_connected:
            logger.info("Client already connected.")
            return True

        logger.info("Connecting to Telegram...")
        try:
            await self.client.connect()

            if not self.client.is_user_authorized():
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
                    logger.critical("Cannot authorize: Phone number not provided in config/env and session is invalid.")
                    raise TelegramConnectionError("Phone number required for authorization.")

            if not self.client.is_user_authorized():
                 logger.critical("Authorization failed even after sign-in attempt.")
                 raise TelegramConnectionError("Authorization failed.")

            me = await self.client.get_me()
            username = getattr(me, 'username', None) or getattr(me, 'first_name', 'Unknown User')
            logger.info(f"Telegram client connected and authorized as: {username} (ID: {getattr(me, 'id', 'unknown')})")
            self.client_connected = True
            return True

        except (AuthKeyError, UserDeactivatedBanError) as e:
             logger.error(f"Authentication error: {e}. Session file '{self.config.session_name}.session' might be invalid or account banned. Delete the session file and try again.")
             raise TelegramConnectionError(f"Authentication error: {e}") from e
        except ConnectionError as e:
             logger.error(f"Network connection error: {e}. Check internet connection and Telegram availability.")
             raise TelegramConnectionError(f"Network error: {e}") from e
        except TelegramConnectionError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting/authorizing Telegram client: {e}", exc_info=self.config.verbose)
            raise TelegramConnectionError(f"Unexpected connection error: {e}") from e

    async def disconnect(self):
        """Disconnects the Telegram client gracefully."""
        if self.client and self.client_connected:
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

        if entity_identifier_str in self.entity_cache:
            cached_entity = self.entity_cache[entity_identifier_str]
            logger.debug(f"Using cached entity for '{entity_identifier_str}'")
            return cached_entity

        logger.debug(f"Resolving entity: {entity_identifier_str}")
        attempts = 0
        max_attempts = 2

        while attempts < max_attempts:
            attempts += 1
            try:
                entity = None
                try:
                    entity = await self.client.get_entity(entity_identifier_str)
                except ValueError:
                    if entity_identifier_str.lstrip('-').isdigit():
                         entity = await self.client.get_entity(int(entity_identifier_str))
                    else:
                        raise

                if entity:
                    entity_name = getattr(entity, 'title', getattr(entity, 'username', getattr(entity, 'id', 'unknown')))
                    logger.debug(f"Resolved '{entity_identifier_str}' to entity: {entity_name}")
                    self.entity_cache[entity_identifier_str] = entity
                    return entity

                logger.warning(f"get_entity resolved '{entity_identifier_str}' to None/False.")
                return None

            except FloodWaitError as e:
                logger.warning(f"Flood wait encountered while resolving entity '{entity_identifier_str}'. Waiting {e.seconds}s...")
                await asyncio.sleep(e.seconds + 1)
            except (UsernameNotOccupiedError, UsernameInvalidError, PeerIdInvalidError, ValueError, TypeError) as e:
                logger.error(f"Could not resolve entity '{entity_identifier_str}': {type(e).__name__}. Check the ID/username/link.")
                return None
            except (ChannelPrivateError, ChatAdminRequiredError, UserNotParticipantError) as e:
                logger.error(f"Cannot access entity '{entity_identifier_str}': {type(e).__name__}. Check permissions or participation.")
                return None
            except RpcCallFailError as e:
                 logger.warning(f"Telegram API RPC call failed while resolving '{entity_identifier_str}': {e}. Retrying if possible...")
                 await asyncio.sleep(1 * attempts)
            except Exception as e:
                logger.error(f"Unexpected error resolving entity '{entity_identifier_str}': {e}", exc_info=self.config.verbose)
                await asyncio.sleep(1 * attempts)

        logger.error(f"Failed to resolve entity '{entity_identifier_str}' after {max_attempts} attempts.")
        return None

    async def fetch_messages(
        self,
        entity: Any,
        min_id: Optional[int] = None
        ) -> AsyncGenerator[Message, None]:
        """Fetches messages from the target entity asynchronously, handling pagination and errors."""

        entity_name = getattr(entity, 'title', getattr(entity, 'username', getattr(entity, 'id', 'unknown')))
        entity_id = getattr(entity, 'id', 'unknown')
        logger.info(f"Fetching messages from: {entity_name} (ID: {entity_id})")
        if min_id:
            logger.info(f"Fetching messages with ID greater than {min_id}")

        total_fetched = 0
        last_logged_count = 0
        log_interval = 500

        # Check access level first
        try:
            logger.debug(f"Testing access to entity {entity_id} by fetching one message...")
            test_message = await self.client.get_messages(entity=entity, limit=1)
            logger.debug(f"Access test result: {'Message found' if test_message else 'No messages found'}")
        except Exception as e:
            logger.error(f"Access test for entity {entity_id} failed: {type(e).__name__}: {e}")
            raise TelegramConnectionError(f"Access error in preliminary test: {e}") from e

        try:
            async for message in self.client.iter_messages(
                entity=entity,
                limit=None,
                offset_id=0,
                reverse=True,
                min_id=min_id or 0,
                wait_time=self.config.request_delay
            ):
                logger.debug(f"Raw message received: type={type(message).__name__}, id={getattr(message, 'id', 'unknown')}")

                if not isinstance(message, Message):
                    logger.debug(f"Skipping non-Message item: {type(message).__name__}")
                    continue

                if getattr(message, 'action', None):
                    logger.debug(f"Skipping action message: {message.id}")
                    continue

                total_fetched += 1
                if total_fetched - last_logged_count >= log_interval:
                    logger.info(f"[{entity_name}] Fetched {total_fetched} messages so far (last ID: {message.id})...")
                    last_logged_count = total_fetched

                yield message

            logger.info(f"Finished fetching initial batch from {entity_name}. Total fetched: {total_fetched}")

            # If we successfully fetched the initial batch and it wasn't empty, fetch the rest
            if total_fetched > 0:
                logger.info(f"Continuing to fetch remaining messages from {entity_name}...")
                last_id = None

                async for message in self.client.iter_messages(
                    entity=entity,
                    limit=0,  # No limit for the rest
                    offset_id=last_id if last_id else 0,
                    reverse=True,
                    min_id=min_id or 0,
                    wait_time=self.config.request_delay
                ):
                    if not isinstance(message, Message) or getattr(message, 'action', None):
                        continue

                    if last_id and message.id >= last_id:
                        continue  # Skip duplicates from the first batch

                    total_fetched += 1
                    last_id = message.id

                    if total_fetched - last_logged_count >= log_interval:
                        logger.info(f"[{entity_name}] Fetched {total_fetched} messages so far (last ID: {message.id})...")
                        last_logged_count = total_fetched

                    yield message

            logger.info(f"Finished fetching all messages from {entity_name}. Total fetched: {total_fetched}")

        except FloodWaitError as e:
            logger.warning(f"[{entity_name}] Flood wait encountered during message fetch. Waiting {e.seconds}s...")
            await asyncio.sleep(e.seconds + 5)
            raise TelegramConnectionError(f"Flood wait received ({e.seconds}s)") from e
        except (ChannelPrivateError, ChannelInvalidError, ChatAdminRequiredError, UserNotParticipantError) as e:
            logger.error(f"[{entity_name}] Cannot access messages: {e}. Check permissions or ID.")
            raise TelegramConnectionError(f"Access error: {e}") from e
        except RpcCallFailError as e:
            logger.error(f"[{entity_name}] Telegram API RPC call failed during message fetch: {e}.")
            raise TelegramConnectionError(f"RPC error: {e}") from e
        except Exception as e:
            logger.error(f"[{entity_name}] An unexpected error occurred during message fetching: {type(e).__name__}: {e}", exc_info=self.config.verbose)
            logger.info("If you're having trouble with message fetching, ensure you have access rights to this entity and that your API credentials have the necessary permissions.")
            raise ExporterError(f"Unexpected fetch error: {e}") from e

    async def run_interactive_selection(self):
        """Handles the interactive selection of chats/channels to export."""
        if not self.client_connected:
            logger.error("Cannot run interactive selection: Telegram client not connected.")
            await self.connect()
            if not self.client_connected:
                return

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
            choice = _read_line_robust("Choose an option (1-4): ")

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
                    break
            elif choice == '4':
                print("Exiting.")
                sys.exit(0)
            else:
                print("Invalid choice. Please enter a number between 1 and 4.")

    async def _list_and_select_dialogs(self):
        """Lists recent dialogs and allows user selection."""
        print("\nFetching recent dialogs...")
        try:
            dialogs = await self.client.get_dialogs(limit=20)
            if not dialogs:
                print("No dialogs found.")
                return

            print("Recent Dialogs:")
            dialog_map = {}
            for i, dialog in enumerate(dialogs):
                entity = dialog.entity
                entity_id = getattr(entity, 'id', 'N/A')
                entity_type = type(entity).__name__.replace('Channel', 'Channel/Group').replace('User', 'User').replace('Chat', 'Group')
                print(f" {i+1}. {dialog.name} (Type: {entity_type}, ID: {entity_id})")
                dialog_map[i+1] = entity

            while True:
                selection = _read_line_robust("Enter numbers of dialogs to add (e.g., 1, 3, 5), or 'c' to cancel: ")
                if selection.lower() == 'c':
                    break
                try:
                    indices = [int(s.strip()) for s in selection.split(',') if s.strip()]
                    added_count = 0
                    for index in indices:
                        if index in dialog_map:
                             entity = dialog_map[index]
                             target = ExportTarget(id=getattr(entity, 'id', 0), name=getattr(entity, 'title', getattr(entity, 'username', str(getattr(entity, 'id', 0)))))
                             if isinstance(entity, types.User): target.type = 'user'
                             elif isinstance(entity, types.Chat): target.type = 'group'
                             elif isinstance(entity, types.Channel): target.type = 'channel'
                             else: target.type = 'unknown'

                             self.config.add_export_target(target)
                             print(f"Added: {target.name or target.id}")
                             added_count += 1
                        else:
                            print(f"Invalid selection: {index}")
                    if added_count > 0:
                         break
                except ValueError:
                    print("Invalid input. Please enter numbers separated by commas.")

        except Exception as e:
            logger.error(f"Failed to list dialogs: {e}", exc_info=self.config.verbose)
            print(f"Error fetching dialogs: {e}")

    async def _select_dialog_manually(self):
        """Allows user to enter an ID, username, or link manually."""
        while True:
            identifier = _read_line_robust("\nEnter Chat/Channel ID, @username, or t.me/ link (or 'c' to cancel): ")
            if identifier.lower() == 'c':
                break
            if not identifier:
                continue

            print(f"Resolving '{identifier}'...")
            entity = await self.resolve_entity(identifier)

            if entity:
                target_name = getattr(entity, 'title', getattr(entity, 'username', str(getattr(entity, 'id', 0))))
                target_id = getattr(entity, 'id', 0)
                target = ExportTarget(id=target_id, name=target_name)
                if isinstance(entity, types.User): target.type = 'user'
                elif isinstance(entity, types.Chat): target.type = 'group'
                elif isinstance(entity, types.Channel): target.type = 'channel'
                else: target.type = 'unknown'

                print(f"Resolved: {target.name} (Type: {target.type}, ID: {target.id})")
                confirm = input("Add this target? (y/n): ").strip().lower()
                if confirm == 'y':
                    self.config.add_export_target(target)
                    print(f"Added: {target.name or target.id}")
                    break
            else:
                print(f"Could not resolve or access '{identifier}'. Please check the input and your permissions.")

    def get_client(self) -> TelegramClient:
        if not self.client:
             raise RuntimeError("TelegramManager not initialized properly.")
        return self.client

class TelegramConnectionError(Exception):
    pass

class ExporterError(Exception):
    pass
