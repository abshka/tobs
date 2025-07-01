import asyncio
import sys
from time import sleep
from typing import Any, AsyncGenerator, Dict, Optional, Union

from rich import print as rprint
from rich.panel import Panel
from telethon import TelegramClient, types
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    PeerIdInvalidError,
    SessionPasswordNeededError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserNotParticipantError,
)
from telethon.tl.types import Message

from src.config import Config, ExportTarget
from src.exceptions import TelegramConnectionError
from src.utils import clear_screen, logger


class TelegramManager:
    """TODO: Add description."""
    """
    Manages Telegram connection, dialog selection, and user interaction.
    """
    def __init__(self, config: Config):
        """TODO: Add description."""
        """Initialize Telegram manager with configuration."""
        self.config = config
        proxy_info = None
        if config.proxy_type and config.proxy_addr and config.proxy_port:
            proxy_scheme = config.proxy_type.lower()
            if proxy_scheme not in ['socks4', 'socks5', 'http']:
                logger.warning(f"Unsupported proxy type for Telethon: '{proxy_scheme}'. Ignoring.")
            else:
                proxy_info = (proxy_scheme, config.proxy_addr, config.proxy_port)
                logger.info(f"Using {proxy_scheme.upper()} proxy for Telethon: {config.proxy_addr}:{config.proxy_port}")

        self.client = TelegramClient(
            config.session_name, config.api_id, config.api_hash,
            device_model="Telegram Markdown Exporter", app_version="1.0.0",
            connection_retries=5, retry_delay=2, request_retries=5,
            proxy=proxy_info
        )
        self.entity_cache: Dict[str, Any] = {}
        self.client_connected = False

    async def connect(self) -> bool:
        """
        Connect and authenticate with Telegram.
        Shows a styled panel for connection and authorization.
        """
        if self.client_connected:
            logger.info("Client already connected.")
            return True

        rprint(Panel("[bold cyan]Connecting to Telegram...[/bold cyan]", expand=False))
        if not self.config.api_id or not self.config.api_hash:
            raise TelegramConnectionError("API ID and API Hash must be provided in the configuration.")

        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self._authenticate()
            if not await self.client.is_user_authorized():
                raise TelegramConnectionError("Authorization failed even after sign-in attempt.")

            me = await self.client.get_me()
            username = getattr(me, 'username', getattr(me, 'first_name', 'Unknown User'))
            logger.info(f"Connected as: {username} (ID: {getattr(me, 'id', 'unknown')})")
            self.client_connected = True
            rprint(Panel(f"[bold green]Authorization successful![/bold green] [dim]Welcome, {username}[/dim]", expand=False))
            return True
        except Exception as e:
            logger.error(f"Unexpected connection error: {e}", exc_info=(self.config.log_level == 'DEBUG'))
            raise TelegramConnectionError(f"Unexpected error: {e}") from e

    async def _authenticate(self) -> bool:
        """
        Handle Telegram authentication flow.
        Prompts user for code and 2FA password if needed.
        """
        rprint("[yellow]Authorization required.[/yellow]")
        if not self.config.phone_number:
            raise TelegramConnectionError("Phone number required for authorization.")
        try:
            await self.client.send_code_request(self.config.phone_number)
            rprint("[bold]Enter the code you received in Telegram:[/bold]", end=" ")
            code = input().strip()
            await self.client.sign_in(self.config.phone_number, code)
            return True
        except SessionPasswordNeededError:
            rprint("[bold]Enter your 2FA password:[/bold]", end=" ")
            password = input().strip()
            await self.client.sign_in(password=password)
            return True
        except Exception as e:
            logger.critical(f"Failed to sign in: {e}", exc_info=True)
            raise TelegramConnectionError(f"Sign-in failed: {e}") from e

    async def disconnect(self) -> bool:
        """
        Disconnects the Telegram client gracefully.
        """
        if not self.client_connected:
            return True
        try:
            logger.info("Disconnecting Telegram client...")
            await self.client.disconnect()
            self.client_connected = False
            logger.info("Telegram client disconnected.")
            return True
        except Exception as e:
            logger.error(f"Error disconnecting Telegram client: {e}")
            return False

    async def resolve_entity(self, entity_identifier: Union[str, int]) -> Optional[Any]:
        entity_id_str = str(entity_identifier).strip()
        if not entity_id_str:
            return None
        if entity_id_str in self.entity_cache:
            return self.entity_cache[entity_id_str]

        for attempt in range(1, 3):
            try:
                entity = await self._get_entity(entity_id_str)
                if entity:
                    self.entity_cache[entity_id_str] = entity
                    return entity
            except FloodWaitError as e:
                logger.warning(f"Flood wait: {e.seconds}s. Waiting...")
                await asyncio.sleep(e.seconds + 1)
            except (UsernameNotOccupiedError, UsernameInvalidError, PeerIdInvalidError, ChannelPrivateError, ChatAdminRequiredError, UserNotParticipantError) as e:
                logger.error(f"Access error for '{entity_id_str}': {type(e).__name__}")
                return None
            except Exception as e:
                logger.error(f"Error resolving '{entity_id_str}': {e}", exc_info=(self.config.log_level == 'DEBUG'))
                await asyncio.sleep(1 * attempt)

        logger.error(f"Failed to resolve '{entity_id_str}' after multiple attempts.")
        return None

    async def _get_entity(self, entity_id_str: str) -> Optional[Any]:
        try:
            return await self.client.get_entity(entity_id_str)
        except ValueError:
            if entity_id_str.lstrip('-').isdigit():
                return await self.client.get_entity(int(entity_id_str))
            raise

    async def fetch_messages(self, entity: Any, min_id: Optional[int] = None) -> AsyncGenerator[Message, None]:
        entity_name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))
        logger.info(f"Fetching messages from: {entity_name} (ID: {entity.id})")
        if min_id:
            logger.info(f"Starting from message ID: {min_id}")

        async for message in self.client.iter_messages(
            entity=entity, limit=None, offset_id=0, reverse=True,
            min_id=min_id or 0, wait_time=self.config.request_delay
        ):
            if isinstance(message, Message) and not message.action:
                yield message

    def _display_menu(self):
        """
        Display the interactive options menu using rprint.
        """
        clear_screen()
        rprint("-" * 40)
        rprint("[bold yellow]Options:[/bold yellow]")
        rprint(" [cyan]1.[/cyan] List recent dialogs")
        rprint(" [cyan]2.[/cyan] Enter ID/Username/Link manually")
        rprint(" [cyan]3.[/cyan] Finish selection and start export")
        rprint(" [cyan]4.[/cyan] Exit")
        rprint("-" * 40)
        # Новый блок: показываем выбранные цели экспорта
        if self.config.export_targets:
            rprint("[bold green]Export targets:[/bold green]")
            for target in self.config.export_targets:
                rprint(f" • {getattr(target, 'name', target.id) or target.id} [dim]({getattr(target, 'type', 'unknown')})[/dim]")
        else:
            rprint("[dim]No export targets selected.[/dim]")
        rprint("[bold]Choose an option (1-4):[/bold]", end=" ")

    async def run_interactive_selection(self):
        """TODO: Add description."""
        """
        Run the interactive selection menu for export targets.
        """
        if not self.client_connected:
            await self.connect()
            if not self.client_connected:
                return False

        clear_screen()
        while True:
            self._display_menu()
            choice = input().strip()
            if choice == '1':
                await self._list_and_select_dialogs()
            elif choice == '2':
                await self._select_dialog_manually()
            elif choice == '3':
                if not self.config.export_targets:
                    rprint("[red]No targets selected. Please select at least one.[/red]")
                else:
                    rprint("[green]Finished selection. Starting export...[/green]")
                    break
            elif choice == '4':
                rprint("[yellow]Exiting...[/yellow]")
                sys.exit(0)
            else:
                rprint("[red]Invalid choice. Please enter a number from 1 to 4.[/red]")
        return True

    def _display_dialogs(self, dialogs, dialog_map, start_index, page_num=None):
        """
        Display a list of dialogs with rich formatting.
        """
        header = "[bold underline]Recent Dialogs:[/bold underline]"
        if page_num is not None:
            header += f" [dim](Page {page_num})[/dim]"
        rprint(header)
        for i, dialog in enumerate(dialogs, start=start_index):
            entity = dialog.entity
            entity_type = self._get_entity_type_name(entity)
            rprint(f" [cyan]{i}.[/cyan] {dialog.name} [dim]({entity_type})[/dim]")
            dialog_map[i] = entity

    async def _list_and_select_dialogs(self):
        """TODO: Add description."""
        """
        List recent dialogs and allow user to select them interactively.
        """
        rprint("[cyan]Fetching recent dialogs...[/cyan]")
        offset_date, offset_id, offset_peer = None, 0, None

        # For paging: keep a stack of previous pages' offsets
        page_stack = []
        page_num = 1

        while True:
            try:
                clear_screen()
                get_dialogs_kwargs = {
                    "limit": self.config.dialog_fetch_limit,
                    "offset_date": offset_date,
                    "offset_id": offset_id,
                }
                if offset_peer is not None:
                    get_dialogs_kwargs["offset_peer"] = offset_peer

                dialogs = await self.client.get_dialogs(**get_dialogs_kwargs)
                if not dialogs:
                    rprint("[yellow]No more dialogs found.[/yellow]")
                    # If on a page > 1, allow user to go back
                    if page_num > 1:
                        rprint("[bold]You are at the last page. Enter 'p' to go to previous page or 'c' to cancel:[/bold]", end=" ")
                        selection = input().strip().lower()
                        if selection in ('c', 'cancel'):
                            break
                        if selection in ('p', 'prev', 'previous'):
                            if page_stack:
                                prev = page_stack.pop()
                                offset_date, offset_id, offset_peer = prev
                                page_num -= 1
                                rprint("[bold]Fetching previous page...[/bold]")
                                continue
                    break

                dialog_map = {}
                self._display_dialogs(dialogs, dialog_map, 1, page_num=page_num)

                rprint("[bold]Enter numbers to add (e.g., 1, 3, 5), n for next page, p for previous page, or c for cancel:[/bold]", end=" ")
                selection = input().strip().lower()

                if selection in ('c', 'cancel'):
                    break
                if selection in ('n', 'next'):
                    # Save current offsets to stack for "previous page"
                    page_stack.append((offset_date, offset_id, offset_peer))
                    # Move to next page by updating offsets based on last dialog
                    last_dialog = dialogs[-1]
                    offset_date = last_dialog.date
                    try:
                        offset_id = int(getattr(last_dialog, "id", 0))
                        if not (-2147483648 <= offset_id <= 2147483647):
                            offset_id = 0
                    except Exception:
                        offset_id = 0
                    offset_peer = last_dialog.entity
                    page_num += 1
                    rprint("[bold]Fetching next page...[/bold]")
                    continue
                if selection in ('p', 'prev', 'previous'):
                    if page_stack:
                        prev = page_stack.pop()
                        offset_date, offset_id, offset_peer = prev
                        page_num = max(1, page_num - 1)
                        rprint("[bold]Fetching previous page...[/bold]")
                        continue
                    else:
                        continue
                # Handle adding dialogs by number
                added = await self._process_dialog_selection(selection, dialog_map)
                if added:
                    rprint("[green]Target(s) added.[/green]")
                    sleep(1)
            except Exception as e:
                logger.error(f"Error during dialog selection: {e}", exc_info=True)
                break
        return True

    def _get_entity_type_name(self, entity):
        """TODO: Add description."""
        if isinstance(entity, types.User):
            return "User"
        if isinstance(entity, types.Chat):
            return "Group"
        if isinstance(entity, types.Channel):
            return "Channel"
        return type(entity).__name__

    async def _process_dialog_selection(self, selection: str, dialog_map: Dict[int, Any]) -> bool:
        """
        Process user selection of dialogs by number.
        """
        try:
            indices = [int(s.strip()) for s in selection.split(',') if s.strip()]
            if not indices:
                return False

            valid_indices = set(dialog_map.keys())
            added_count = 0
            invalid_indices = [index for index in indices if index not in valid_indices]
            if invalid_indices:
                rprint("[red]Invalid input. Enter number in range 1-20.[/red]")
                sleep(1)
                return False

            for index in indices:
                entity = dialog_map.get(index)
                if entity:
                    target = self._create_export_target_from_entity(entity)
                    self.config.add_export_target(target)
                    rprint(f"[green]Added:[/green] {target.name or target.id}")
                    added_count += 1
            return added_count > 0
        except ValueError:
            rprint("[red]Invalid input. Please enter numbers separated by commas.[/red]")
            sleep(1)
            return False

    def _create_export_target_from_entity(self, entity) -> ExportTarget:
        """
        Create an ExportTarget from a Telegram entity.
        """
        name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))
        return ExportTarget(id=entity.id, name=name, type=self._get_entity_type_name(entity))

    async def _select_dialog_manually(self):
        """TODO: Add description."""
        """
        Allow user to manually enter a chat/channel ID, username, or link.
        """
        while True:
            rprint("\n[bold]Enter Chat/Channel ID, @username, or t.me/ link (or c for cancel):[/bold]", end=" ")
            identifier = input().strip()
            if identifier.lower() == 'c':
                break
            if not identifier:
                continue

            rprint(f"[dim]Resolving '{identifier}'...[/dim]")
            entity = await self.resolve_entity(identifier)
            if entity:
                target = self._create_export_target_from_entity(entity)
                rprint(f"[green]Resolved:[/green] {target.name} (Type: {target.type}, ID: {target.id})")
                rprint("[bold]Add this target? (y/n):[/bold]", end=" ")
                if input().strip().lower() == 'y':
                    self.config.add_export_target(target)
                    rprint(f"[green]Added:[/green] {target.name or target.id}")
                    sleep(1)
                    break
            else:
                rprint(f"[bold red]Could not resolve or access '{identifier}'. Check input and permissions.[/bold red]")
        return True

    def get_client(self) -> TelegramClient:
        """
        Return the underlying TelegramClient instance.
        """
        if not self.client:
            raise RuntimeError("TelegramManager not initialized properly.")
        return self.client
