import asyncio
import re
import sys
from pathlib import Path
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
from src.forum_manager import ForumManager
from src.retry_manager import TELEGRAM_API_CONFIG, retry_manager
from src.utils import clear_screen, logger, notify_and_pause
async def notify_and_pause_async(text: str, duration: float = 1.0) -> None:
    """
    Notify the user and pause asynchronously.

    Args:
        text (str): The message to display.
        duration (float): Duration to pause in seconds.

    Returns:
        None
    """
    notify_and_pause(text)
    await asyncio.sleep(duration)

class TelegramManager:
    """
    Manages Telegram connection, dialog selection, and user interaction.
    """
    def __init__(self, config: Config):
        """
        Initialize Telegram manager with configuration.

        Args:
            config (Config): The configuration object.

        Returns:
            None
        """
        self.config = config
        proxy_info = None
        if config.proxy_type and config.proxy_addr and config.proxy_port:
            proxy_scheme = config.proxy_type.lower()
            if proxy_scheme not in ['socks4', 'socks5', 'http']:
                logger.warning(f"Unsupported proxy type for Telethon: '{proxy_scheme}'. Ignoring.")
            else:
                proxy_info = (proxy_scheme, config.proxy_addr, config.proxy_port)

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

        Returns:
            bool: True if connection and authentication are successful, False otherwise.
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
            self.client_connected = True

            # Инициализируем ForumManager после успешного подключения
            self.forum_manager = ForumManager(self.client, self.config)

            rprint(Panel(f"[bold green]Authorization successful![/bold green] [dim]Welcome, {username}[/dim]", expand=False))
            return True
        except Exception as e:
            logger.error(f"Unexpected connection error: {e}", exc_info=(self.config.log_level == 'DEBUG'))
            raise TelegramConnectionError(f"Unexpected error: {e}") from e

    async def _authenticate(self) -> bool:
        """
        Handle Telegram authentication flow.

        Returns:
            bool: True if authentication is successful, False otherwise.

        Raises:
            TelegramConnectionError: If authentication fails.
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

        Returns:
            bool: True if disconnected successfully, False otherwise.
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
        """
        Resolve a Telegram entity by its identifier.

        Args:
            entity_identifier (str|int): The identifier of the entity.

        Returns:
            Optional[Any]: The resolved entity or None if not found.
        """
        entity_id_str = str(entity_identifier).strip()
        if not entity_id_str:
            return None
        if entity_id_str in self.entity_cache:
            return self.entity_cache[entity_id_str]

        async def _resolve_operation():
            entity = await self._get_entity(entity_id_str)
            if entity:
                self.entity_cache[entity_id_str] = entity
                return entity
            return None

        try:
            return await retry_manager.retry_async(
                _resolve_operation,
                f"entity_resolve_{entity_id_str}",
                TELEGRAM_API_CONFIG
            )
        except FloodWaitError as e:
            logger.warning(f"Flood wait: {e.seconds}s. Waiting...")
            await asyncio.sleep(e.seconds + 1)
            # Retry once more after flood wait
            try:
                return await _resolve_operation()
            except Exception:
                pass
        except (UsernameNotOccupiedError, UsernameInvalidError, PeerIdInvalidError,
                ChannelPrivateError, ChatAdminRequiredError, UserNotParticipantError) as e:
            logger.error(f"Access error for '{entity_id_str}': {type(e).__name__}")
            return None
        except Exception as e:
            logger.error(f"Failed to resolve '{entity_id_str}' after all attempts: {e}")
            return None

    async def _get_entity(self, entity_id_str: str) -> Optional[Any]:
        """
        Get a Telegram entity by its string identifier.

        Args:
            entity_id_str (str): The entity identifier as a string.

        Returns:
            Optional[Any]: The resolved entity or None.
        """
        try:
            return await self.client.get_entity(entity_id_str)
        except ValueError:
            if entity_id_str.lstrip('-').isdigit():
                return await self.client.get_entity(int(entity_id_str))
            raise

    async def fetch_messages(self, entity: Any, limit: Optional[int] = None, min_id: Optional[int] = None) -> AsyncGenerator[Message, None]:
        """
        Fetch messages from a Telegram entity.

        Args:
            entity (Any): The Telegram entity to fetch messages from.
            limit (Optional[int]): The maximum number of messages to fetch.
            min_id (Optional[int]): The minimum message ID to start from.

        Yields:
            Message: Telegram message objects.
        """
        if min_id:
            logger.info(f"Starting from message ID: {min_id}")

        async for message in self.client.iter_messages(
            entity=entity, limit=limit, offset_id=0, reverse=True,
            min_id=min_id or 0, wait_time=self.config.request_delay):
            if isinstance(message, Message) and not message.action:
                yield message

    def _display_menu(self):
        """
        Display the interactive options menu using rprint.

        Returns:
            None
        """
        clear_screen()
        rprint("-" * 40)
        rprint("[bold yellow]Options:[/bold yellow]")
        rprint(" [cyan]1.[/cyan] List recent dialogs")
        rprint(" [cyan]2.[/cyan] Enter ID/Username/Link manually")
        rprint(" [cyan]3.[/cyan] Export forum topic by link")
        rprint(" [cyan]4.[/cyan] Export single post by link")
        rprint(" [cyan]5.[/cyan] Finish selection and start export")
        rprint(" [cyan]6.[/cyan] Exit")
        rprint("-" * 40)
        if self.config.export_targets:
            rprint("[bold green]Export targets:[/bold green]")
            for target in self.config.export_targets:
                rprint(f" • {getattr(target, 'name', target.id) or target.id} [dim]({getattr(target, 'type', 'unknown')})[/dim]")
        else:
            rprint("[dim]No export targets selected.[/dim]")
        print("Choose an option (1-6): ", end="")

    async def run_interactive_selection(self):
        """
        Run the interactive selection menu for export targets.

        Returns:
            bool: True if selection completed, False otherwise.
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
                await self._export_forum_topic_by_link()
            elif choice == '4':
                await self.export_single_post_by_link()
            elif choice == '5':
                if not self.config.export_targets:
                    await notify_and_pause_async("[red]No targets selected. Please select at least one.[/red]")
                else:
                    await notify_and_pause_async("[green]Finished selection. Starting export...[/green]")
                    break
            elif choice == '6':
                await notify_and_pause_async("[yellow]Exiting...[/yellow]")
                sys.exit(0)
            else:
                await notify_and_pause_async("[red]Invalid choice. Please enter a number from 1 to 6.[/red]")
        return True

    def _display_dialogs(self, dialogs, dialog_map, start_index, page_num=None):
        """
        Display a list of dialogs with rich formatting.

        Args:
            dialogs (list): List of dialog objects.
            dialog_map (dict): Mapping of index to dialog entity.
            start_index (int): Starting index for display.
            page_num (Optional[int]): Current page number.

        Returns:
            None
        """
        header = "[bold underline]Recent Dialogs:[/bold underline]"
        if page_num is not None:
            header += f" [dim](Page {page_num})[/dim]"
        rprint(header)
        for i, dialog in enumerate(dialogs, start=start_index):
            entity = dialog.entity
            entity_type = self._get_entity_type_name(entity)

            # Добавляем иконки для типов
            type_icon = ""
            if isinstance(entity, types.Channel):
                if getattr(entity, 'forum', False):
                    type_icon = "📋"  # Иконка форума
                elif getattr(entity, 'megagroup', False):
                    type_icon = "👥"  # Иконка супергруппы
                else:
                    type_icon = "📢"  # Иконка канала
            elif isinstance(entity, types.Chat):
                type_icon = "💬"  # Иконка обычного чата
            elif isinstance(entity, types.User):
                type_icon = "👤"  # Иконка пользователя

            rprint(f" [cyan]{i}.[/cyan] {type_icon} {dialog.name} [dim]({entity_type})[/dim]")
            dialog_map[i] = entity

    async def _list_and_select_dialogs(self):
        """
        List recent dialogs and allow user to select them interactively.

        Returns:
            bool: True if dialogs were listed and selection attempted.
        """
        await notify_and_pause_async("[cyan]Fetching recent dialogs...[/cyan]")
        offset_date, offset_id, offset_peer = None, 0, None

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
                    await notify_and_pause_async("[yellow]No more dialogs found.[/yellow]")
                    if page_num > 1:
                        print("You are at the last page. Enter 'p' to go to previous page or 'c' to cancel: ", end="")
                        selection = input().strip().lower()
                        if selection in ('c', 'cancel'):
                            break
                        if selection in ('p', 'prev', 'previous'):
                            if page_stack:
                                prev = page_stack.pop()
                                offset_date, offset_id, offset_peer = prev
                                page_num -= 1
                                await notify_and_pause_async("[bold]Fetching previous page...[/bold]")
                                continue
                    break

                dialog_map = {}
                self._display_dialogs(dialogs, dialog_map, 1, page_num=page_num)

                print("Enter numbers to add (e.g., 1, 3, 5), n for next page, p for previous page, or c for cancel: ", end="")
                selection = input().strip().lower()

                if selection in ('c', 'cancel'):
                    break
                if selection in ('n', 'next'):
                    page_stack.append((offset_date, offset_id, offset_peer))
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
                    await notify_and_pause_async("[bold]Fetching next page...[/bold]")
                    continue
                if selection in ('p', 'prev', 'previous'):
                    if page_stack:
                        prev = page_stack.pop()
                        offset_date, offset_id, offset_peer = prev
                        page_num = max(1, page_num - 1)
                        await notify_and_pause_async("[bold]Fetching previous page...[/bold]")
                        continue
                    else:
                        continue
                added = await self._process_dialog_selection(selection, dialog_map)
                if added:
                    pass

            except Exception as e:
                logger.error(f"Error during dialog selection: {e}", exc_info=True)
                break
        return True

    def _get_entity_type_name(self, entity):
        """
        Return the type name of a Telegram entity with forum detection.

        Args:
            entity (Any): The Telegram entity.

        Returns:
            str: The type name of the entity.
        """
        if isinstance(entity, types.User):
            return "User"
        if isinstance(entity, types.Chat):
            return "Group Chat"
        if isinstance(entity, types.Channel):
            # Проверяем, является ли канал форумом
            if getattr(entity, 'forum', False):
                return "Forum"
            elif getattr(entity, 'megagroup', False):
                return "Supergroup"
            elif getattr(entity, 'broadcast', False):
                return "Channel"
            else:
                return "Channel"
        return type(entity).__name__

    async def _process_dialog_selection(self, selection: str, dialog_map: Dict[int, Any]) -> bool:
        """
        Process user selection of dialogs by number with forum detection.

        Args:
            selection (str): The user's selection input.
            dialog_map (Dict[int, Any]): Mapping of indices to entities.

        Returns:
            bool: True if at least one dialog was added, False otherwise.
        """
        try:
            indices = [int(s.strip()) for s in selection.split(',') if s.strip()]
            if not indices:
                return False

            valid_indices = set(dialog_map.keys())
            added_count = 0
            invalid_indices = [index for index in indices if index not in valid_indices]
            if invalid_indices:
                await notify_and_pause_async("[red]Invalid input. Enter number in range 1-20.[/red]")
                return False

            for index in indices:
                entity = dialog_map.get(index)
                if entity:
                    # Проверяем, является ли это форумом
                    if self.forum_manager and await self.forum_manager.is_forum_chat(entity):
                        await notify_and_pause_async(f"[cyan]📋 Detected forum chat: {getattr(entity, 'title', 'Unknown')}[/cyan]")
                        await self._handle_forum_selection(entity)
                    else:
                        target = self._create_export_target_from_entity(entity)
                        self.config.add_export_target(target)
                        await notify_and_pause_async(f"[green]Added:[/green] {target.name or target.id}")
                    added_count += 1
            return added_count > 0
        except ValueError:
            await notify_and_pause_async("[red]Invalid input. Please enter numbers separated by commas.[/red]")
            return False

    def _create_export_target_from_entity(self, entity) -> ExportTarget:
        """
        Create an ExportTarget from a Telegram entity with proper type detection.

        Args:
            entity (Any): The Telegram entity.

        Returns:
            ExportTarget: The created export target.
        """
        name = getattr(entity, 'title', getattr(entity, 'username', str(entity.id)))

        # Определяем правильный тип для ExportTarget
        if isinstance(entity, types.Channel):
            if getattr(entity, 'forum', False):
                export_type = "forum_chat"
            elif getattr(entity, 'megagroup', False):
                export_type = "chat"
            else:
                export_type = "channel"
        elif isinstance(entity, types.Chat):
            export_type = "chat"
        elif isinstance(entity, types.User):
            export_type = "user"
        else:
            export_type = "unknown"

        target = ExportTarget(id=entity.id, name=name, type=export_type)

        # Для форумов устанавливаем дополнительные параметры
        if export_type == "forum_chat":
            target.is_forum = True
            target.export_all_topics = True

        return target

    async def _select_dialog_manually(self):
        """
        Allow user to manually enter a chat/channel ID, username, or link.

        Returns:
            bool: True if manual selection completed.
        """
        rprint("\n[bold yellow]Manual Selection Mode[/bold yellow]")
        while True:
            print("\nEnter Chat/Channel ID, @username, or t.me/ link (or c for cancel): ", end="")
            identifier = input().strip()
            if identifier.lower() == 'c':
                break
            if not identifier:
                continue

            await notify_and_pause_async(f"[dim]Resolving '{identifier}'...[/dim]")

            # Проверяем, является ли это ссылкой на топик форума
            if '/c/' in identifier and self.forum_manager:
                topic_result = await self.forum_manager.detect_topic_from_url(identifier)
                if topic_result:
                    chat_id, topic_id = topic_result
                    entity = await self.resolve_entity(chat_id)
                    if entity and await self.forum_manager.is_forum_chat(entity):
                        target = ExportTarget(
                            id=chat_id,
                            name=f"{getattr(entity, 'title', 'Forum')} > Topic {topic_id}",
                            type="forum_topic",
                            topic_id=topic_id,
                            is_forum=True,
                            export_all_topics=False
                        )
                        await notify_and_pause_async(f"[green]Resolved Forum Topic:[/green] {target.name}")
                        print("Add this topic for export? (y/n): ", end="")
                        if input().strip().lower() == 'y':
                            self.config.add_export_target(target)
                            await notify_and_pause_async(f"[green]Added:[/green] {target.name}")
                            break
                        continue

            entity = await self.resolve_entity(identifier)
            if entity:
                # Проверяем, является ли это форумом
                if self.forum_manager and await self.forum_manager.is_forum_chat(entity):
                    await self._handle_forum_selection(entity)
                    break
                else:
                    target = self._create_export_target_from_entity(entity)
                    await notify_and_pause_async(f"[green]Resolved:[/green] {target.name} (Type: {target.type}, ID: {target.id})")
                    print("Add this target? (y/n): ", end="")
                    if input().strip().lower() == 'y':
                        self.config.add_export_target(target)
                        await notify_and_pause_async(f"[green]Added:[/green] {target.name or target.id}")
                        break
            else:
                await notify_and_pause_async(f"[bold red]Could not resolve or access '{identifier}'. Check input and permissions.[/bold red]")

        return True

    async def _export_forum_topic_by_link(self):
        """
        Handler for exporting forum topics by direct link.
        """
        clear_screen()
        rprint("\n[bold cyan]📋 Export Forum Topic by Link[/bold cyan]")
        rprint("[yellow]Enter a forum topic link (e.g., https://t.me/c/2217062060/96):[/yellow]")

        link = input("\nTopic link: ").strip()
        if not link:
            await notify_and_pause_async("[red]No link provided.[/red]")
            return

        if not self.forum_manager:
            await notify_and_pause_async("[red]Forum manager not initialized.[/red]")
            return

        # Парсим ссылку на топик
        topic_result = await self.forum_manager.detect_topic_from_url(link)
        if not topic_result:
            await notify_and_pause_async("[red]Invalid forum topic link format.[/red]")
            return

        chat_id, topic_id = topic_result
        await notify_and_pause_async(f"[dim]Resolving forum chat: {chat_id}...[/dim]")

        entity = await self.resolve_entity(chat_id)
        if not entity:
            await notify_and_pause_async(f"[red]Could not resolve forum chat '{chat_id}'.[/red]")
            return

        if not await self.forum_manager.is_forum_chat(entity):
            await notify_and_pause_async(f"[red]The chat '{chat_id}' is not a forum.[/red]")
            return

        # Получаем информацию о топике
        topics = await self.forum_manager.get_forum_topics(entity)
        topic_info = None
        for topic in topics:
            if topic.topic_id == topic_id:
                topic_info = topic
                break

        if not topic_info:
            await notify_and_pause_async(f"[red]Topic {topic_id} not found in this forum.[/red]")
            return

        # Создаем target для экспорта
        entity_name = getattr(entity, 'title', f"Forum_{entity.id}")
        target = ExportTarget(
            id=chat_id,
            name=f"{entity_name} > {topic_info.title}",
            type="forum_topic",
            topic_id=topic_id,
            is_forum=True,
            export_all_topics=False
        )

        self.config.add_export_target(target)
        await notify_and_pause_async(f"[green]✅ Added forum topic:[/green] {topic_info.title}")
        await notify_and_pause_async(f"[cyan]📋 Forum: {entity_name}[/cyan]")
        await notify_and_pause_async(f"[cyan]📝 Topic: {topic_info.title} (ID: {topic_id})[/cyan]")

    async def _handle_forum_selection(self, entity: Any):
        """
        Обрабатывает выбор форума с топиками.

        Args:
            entity: Telegram entity форума
        """
        try:
            entity_name = getattr(entity, 'title', f"Forum_{entity.id}")
            await notify_and_pause_async(f"[cyan]📋 This is a forum chat: {entity_name}[/cyan]")

            # Получаем список топиков
            topics = await self.forum_manager.get_forum_topics(entity)
            if not topics:
                await notify_and_pause_async("[yellow]No topics found in this forum.[/yellow]")
                return

            # Показываем топики
            topic_info = self.forum_manager.format_topic_info_for_display(topics)
            print(f"\n{topic_info}\n")

            print("Forum export options:")
            print("1. Export all topics")
            print("2. Export specific topics")
            print("3. Cancel")

            while True:
                choice = input("Choose option (1-3): ").strip()

                if choice == '1':
                    # Экспорт всех топиков
                    target = ExportTarget(
                        id=str(entity.id),
                        name=entity_name,
                        type="forum_chat",
                        is_forum=True,
                        export_all_topics=True
                    )
                    logger.info(f"Creating forum_chat target: ID={target.id}, Type={target.type}, Is_forum={target.is_forum}")
                    self.config.add_export_target(target)
                    await notify_and_pause_async(f"[green]Added all topics from:[/green] {entity_name}")
                    break

                elif choice == '2':
                    # Выбор конкретных топиков
                    while True:
                        print("\nEnter topic numbers (comma-separated, e.g., 1,3,5) or 'back' to return: ", end="")
                        selection = input().strip()

                        if selection.lower() == 'back':
                            break

                        try:
                            selected_indices = [int(x.strip()) - 1 for x in selection.split(',')]
                            selected_topics = []
                            invalid_indices = []

                            for idx in selected_indices:
                                if 0 <= idx < len(topics):
                                    topic = topics[idx]
                                    target = ExportTarget(
                                        id=str(entity.id),
                                        name=f"{entity_name} > {topic.title}",
                                        type="forum_topic",
                                        topic_id=topic.topic_id,
                                        is_forum=True,
                                        export_all_topics=False
                                    )
                                    logger.info(f"Creating forum_topic target: ID={target.id}, Type={target.type}, Topic_ID={target.topic_id}, Is_forum={target.is_forum}")
                                    self.config.add_export_target(target)
                                    selected_topics.append(topic.title)
                                else:
                                    invalid_indices.append(idx + 1)

                            if invalid_indices:
                                rprint(f"[red]Invalid topic numbers: {', '.join(map(str, invalid_indices))}[/red]")
                                rprint(f"[yellow]Valid range: 1-{len(topics)}[/yellow]")
                                continue

                            if selected_topics:
                                await notify_and_pause_async(f"[green]Added {len(selected_topics)} topics:[/green] {', '.join(selected_topics)}")
                                break
                            else:
                                rprint("[red]No valid topics selected.[/red]")
                                continue

                        except ValueError:
                            rprint("[red]Invalid format. Please enter numbers separated by commas (e.g., 1,3,5).[/red]")
                            continue
                        except Exception as e:
                            rprint(f"[red]Error processing selection: {e}[/red]")
                            continue
                    break

                elif choice == '3':
                    # Отмена
                    await notify_and_pause_async("[yellow]Forum export cancelled.[/yellow]")
                    break

                else:
                    rprint(f"[red]Invalid option '{choice}'. Please choose 1, 2, or 3.[/red]")
                    continue

        except Exception as e:
            logger.error(f"Error handling forum selection: {e}")
            await notify_and_pause_async(f"[red]Error processing forum: {e}[/red]")

    async def export_single_post_by_link(self):
        """
        Handler for exporting a single Telegram post by link.

        Returns:
            None
        """
        print("\nEnter the link to the Telegram post (e.g., https://t.me/channel/12345): ", end="")
        link = input().strip()

        match = re.match(
            r"^https?://t\.me/(?P<username>[\w\d_]+)/(?P<post_id>\d+)$"
            r"|^https?://t\.me/c/(?P<chan_id>\d+)/(?P<post_id2>\d+)$",
            link
        )
        if not match:
            await notify_and_pause_async("[red]Invalid link format. Please provide a valid Telegram post link.[/red]")
            return

        username = match.group("username")
        post_id = match.group("post_id") or match.group("post_id2")
        chan_id = match.group("chan_id")

        if username:
            entity_identifier = username
        elif chan_id:
            entity_identifier = f"-100{chan_id}"
        else:
            await notify_and_pause_async("[red]Could not extract channel and post ID from the link.[/red]")
            return

        if not post_id:
            await notify_and_pause_async("[red]Could not extract post ID from the link.[/red]")
            return

        await notify_and_pause_async(f"[dim]Resolving channel: {entity_identifier}...[/dim]")
        entity = await self.resolve_entity(entity_identifier)
        if not entity:
            await notify_and_pause_async(f"[red]Could not resolve channel '{entity_identifier}'.[/red]")
            return

        try:
            message = await self.client.get_messages(entity, ids=int(post_id))
        except Exception as e:
            await notify_and_pause_async(f"[red]Failed to fetch message: {e}[/red]")
            return

        if not message:
            await notify_and_pause_async(f"[red]Message with ID {post_id} not found in this channel.[/red]")
            return
        else:
            await notify_and_pause_async(f"[green]Post found! Channel: {entity_identifier}, Post ID: {post_id}[/green]")

        messages_to_process = await self.collect_album_messages(entity, message)

        # Сохраняем список сообщений для pipeline
        self.config._single_post_messages_to_process = messages_to_process

        channel_name = getattr(entity, 'username', None) or getattr(entity, 'title', None) or str(entity.id)
        export_dir_name = f"{channel_name}_{post_id}"
        export_root = Path(self.config.export_path) / export_dir_name
        media_path = export_root / (self.config.media_subdir or "_media")
        export_root.mkdir(parents=True, exist_ok=True)
        media_path.mkdir(parents=True, exist_ok=True)
        single_post_target = ExportTarget(
            id=str(entity.id),
            name=export_dir_name,
            type="single_post",
            message_id=int(post_id)
        )
        self.config.export_targets.clear()
        self.config.add_export_target(single_post_target)
        return

    async def collect_album_messages(self, entity, message):
        """
        Collect all messages belonging to the same album (grouped media) as the given message.

        Args:
            entity: Telegram entity (channel/group/user).
            message: The main message (post) to check for grouped media.

        Returns:
            List[Message]: All messages in the album (grouped media), or [message] if not an album.
        """
        grouped_id = getattr(message, "grouped_id", None)
        if not grouped_id:
            return [message]

        album_messages = await self.client.get_messages(
            entity,
            limit=50,
            min_id=message.id - 25,
            max_id=message.id + 25
        )
        album_messages = [m for m in album_messages if m and getattr(m, "grouped_id", None) == grouped_id]
        album_messages.append(message)
        album_messages = list({m.id: m for m in album_messages}.values())
        album_messages.sort(key=lambda m: m.id)
        return album_messages

    def get_client(self) -> TelegramClient:
        """
        Return the underlying TelegramClient instance.

        Returns:
            TelegramClient: The Telegram client instance.

        Raises:
            RuntimeError: If the client is not initialized.
        """
        if not self.client:
            raise RuntimeError("TelegramManager not initialized properly.")
        return self.client
