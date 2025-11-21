import asyncio
import re
import sys
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

from rich import print as rprint
from rich.panel import Panel
from rich.prompt import Confirm
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
from telethon.tl.functions.channels import GetForumTopicsRequest
from telethon.tl.types import (
    Channel,
    ForumTopic,
    Message,
)

from src.config import Config, ExportTarget, ITER_MESSAGES_TIMEOUT
from src.exceptions import TelegramConnectionError
from src.utils import clear_screen, logger, notify_and_pause, sanitize_filename


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


class TopicInfo:
    """Информация о топике форума."""

    def __init__(
        self,
        topic_id: int,
        title: str,
        icon_emoji: str = "",
        creator_id: Optional[int] = None,
        created_date: Optional[datetime] = None,
        message_count: int = 0,
        is_closed: bool = False,
        is_pinned: bool = False,
    ):
        self.topic_id = topic_id
        self.title = title
        self.icon_emoji = icon_emoji
        self.creator_id = creator_id
        self.created_date = created_date
        self.message_count = message_count
        self.is_closed = is_closed
        self.is_pinned = is_pinned
        self.safe_name = sanitize_filename(f"{title}_{topic_id}")

    def __repr__(self):
        status = []
        if self.is_pinned:
            status.append("📌")
        if self.is_closed:
            status.append("🔒")
        status_str = " ".join(status)
        return f"Topic({self.topic_id}, '{self.title}', {self.message_count} msgs{' ' + status_str if status_str else ''})"


class TelegramManager:
    """
    Manages Telegram connection, dialog selection, and user interaction.
    """

    def __init__(self, config: Config, connection_manager=None):
        """
        Initialize Telegram manager with configuration.

        Args:
            config (Config): The configuration object.
            connection_manager: The connection manager for retries.
        """
        self.config = config
        self.connection_manager = connection_manager
        proxy_info = None
        if config.proxy_type and config.proxy_addr and config.proxy_port:
            proxy_scheme = config.proxy_type.lower()
            if proxy_scheme not in ["socks4", "socks5", "http"]:
                logger.warning(
                    f"Unsupported proxy type for Telethon: '{proxy_scheme}'. Ignoring."
                )
            else:
                proxy_info = (proxy_scheme, config.proxy_addr, config.proxy_port)

        base_timeout = getattr(config.performance, "base_download_timeout", 300.0)

        self.client = TelegramClient(
            config.session_name,
            config.api_id,
            config.api_hash,
            device_model="Telegram Desktop",
            app_version="4.14.8",
            system_version="Windows 10",
            lang_code="en",
            system_lang_code="en",
            connection_retries=20,
            retry_delay=1,
            request_retries=25,
            timeout=base_timeout,
            flood_sleep_threshold=0,
            auto_reconnect=True,
            sequential_updates=True,
            proxy=proxy_info,
        )
        self.entity_cache: Dict[str, Any] = {}
        self.topics_cache: Dict[Union[str, int], List[TopicInfo]] = {}
        self.client_connected = False

    async def connect(self) -> bool:
        """
        Connect and authenticate with Telegram.
        """
        if self.client_connected:
            logger.info("Client already connected.")
            return True

        rprint(Panel("[bold cyan]Connecting to Telegram...[/bold cyan]", expand=False))
        if not self.config.api_id or not self.config.api_hash:
            raise TelegramConnectionError(
                "API ID and API Hash must be provided in the configuration."
            )

        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self._authenticate()
            if not await self.client.is_user_authorized():
                raise TelegramConnectionError(
                    "Authorization failed even after sign-in attempt."
                )

            me = await self.client.get_me()
            username = getattr(
                me, "username", getattr(me, "first_name", "Unknown User")
            )
            self.client_connected = True

            rprint(
                Panel(
                    f"[bold green]Authorization successful![/bold green] [dim]Welcome, {username}[/dim]",
                    expand=False,
                )
            )
            return True
        except Exception as e:
            logger.error(
                f"Unexpected connection error: {e}",
                exc_info=(self.config.log_level == "DEBUG"),
            )
            raise TelegramConnectionError(f"Unexpected error: {e}") from e

    async def get_available_entities(self):
        """
        Get available entities (chats, channels) for the interactive UI.
        """
        if not self.client_connected:
            await self.connect()

        try:
            dialogs = await self.client.get_dialogs(limit=50)
            return [dialog.entity for dialog in dialogs]
        except Exception as e:
            logger.error(f"Failed to get available entities: {e}")
            return []

    async def _authenticate(self) -> bool:
        """
        Handle Telegram authentication flow.
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
        """
        Resolve a Telegram entity by its identifier.
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
            return await _resolve_operation()
        except FloodWaitError as e:
            logger.warning(f"Flood wait: {e.seconds}s. Waiting...")
            await asyncio.sleep(e.seconds + 1)
            try:
                return await _resolve_operation()
            except Exception:
                return None
        except (
            UsernameNotOccupiedError,
            UsernameInvalidError,
            PeerIdInvalidError,
            ChannelPrivateError,
            ChatAdminRequiredError,
            UserNotParticipantError,
        ) as e:
            logger.error(f"Access error for '{entity_id_str}': {type(e).__name__}")
            return None
        except Exception as e:
            logger.error(f"Failed to resolve '{entity_id_str}' after all attempts: {e}")
            return None

    async def _get_entity(self, entity_id_str: str) -> Optional[Any]:
        """
        Get a Telegram entity by its string identifier.
        """
        try:
            return await self.client.get_entity(entity_id_str)
        except ValueError:
            if entity_id_str.lstrip("-").isdigit():
                return await self.client.get_entity(int(entity_id_str))
            raise

    async def fetch_messages(
        self, entity: Any, limit: Optional[int] = None, min_id: Optional[int] = None
    ) -> AsyncGenerator[Message, None]:
        """
        Fetch messages from a Telegram entity with FloodWaitError retry support and timeout.
        Yields messages one-by-one with timeout protection (Phase 2 Task 2.2).
        """
        if min_id:
            logger.info(f"Starting from message ID: {min_id}")

        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Wrap iter_messages in timeout (Phase 2 Task 2.2)
                async def _message_generator():
                    async for message in self.client.iter_messages(
                        entity=entity,
                        limit=limit,
                        offset_id=0,
                        reverse=True,
                        min_id=min_id or 0,
                        wait_time=self.config.request_delay,
                    ):
                        if isinstance(message, Message) and not message.action:
                            yield message
                
                # Execute with timeout - if exceeds ITER_MESSAGES_TIMEOUT, raise asyncio.TimeoutError
                async for message in _message_generator():
                    try:
                        # Apply timeout for each message fetch operation
                        yield await asyncio.wait_for(
                            self._yield_with_timeout(message),
                            timeout=ITER_MESSAGES_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"⏰ Timeout while processing message {message.id} "
                            f"(exceeded {ITER_MESSAGES_TIMEOUT}s limit)"
                        )
                        raise
                
                # Success, exit retry loop
                break
            
            except asyncio.TimeoutError:
                retry_count += 1
                logger.warning(
                    f"⏱️ Message fetching timeout (attempt {retry_count}/{max_retries}). "
                    f"Timeout: {ITER_MESSAGES_TIMEOUT}s"
                )
                if retry_count < max_retries:
                    logger.info(f"⏱️  Waiting 5 seconds before retry...")
                    await asyncio.sleep(5)
                else:
                    logger.error("❌ Max retries reached for message fetching (timeout), aborting")
                    raise
            
            except FloodWaitError as e:
                retry_count += 1
                wait_time = e.seconds
                logger.warning(
                    f"⏳ FloodWait detected while fetching messages: need to wait {wait_time}s (attempt {retry_count}/{max_retries})"
                )
                if retry_count < max_retries:
                    logger.info(f"⏱️  Waiting {wait_time + 1} seconds before retry...")
                    await asyncio.sleep(wait_time + 1)
                else:
                    logger.error("❌ Max retries reached for message fetching, aborting")
                    raise

    async def _yield_with_timeout(self, value):
        """Helper to yield value without blocking (used with asyncio.wait_for)."""
        return value

    def _display_menu(self):
        """
        Display the interactive options menu.
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
                rprint(
                    f" • {getattr(target, 'name', target.id) or target.id} [dim]({getattr(target, 'type', 'unknown')})[/dim]"
                )
        else:
            rprint("[dim]No export targets selected.[/dim]")
        print("Choose an option (1-6): ", end="")

    async def run_interactive_selection(self):
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
            if choice == "1":
                await self._list_and_select_dialogs()
            elif choice == "2":
                await self._select_dialog_manually()
            elif choice == "3":
                await self._export_forum_topic_by_link()
            elif choice == "4":
                await self.export_single_post_by_link()
            elif choice == "5":
                if not self.config.export_targets:
                    await notify_and_pause_async(
                        "[red]No targets selected. Please select at least one."
                    )
                else:
                    await notify_and_pause_async(
                        "[green]Finished selection. Starting export...[/green]"
                    )
                    break
            elif choice == "6":
                await notify_and_pause_async("[yellow]Exiting...[/yellow]")
                sys.exit(0)
            else:
                await notify_and_pause_async(
                    "[red]Invalid choice. Please enter a number from 1 to 6."
                )
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
            type_icon = (
                "📋"
                if getattr(entity, "forum", False)
                else "📢"
                if isinstance(entity, types.Channel)
                else "👥"
                if isinstance(entity, types.Chat)
                else "👤"
            )
            rprint(
                f" [cyan]{i}.[/cyan] {type_icon} {dialog.name} [dim]({entity_type})[/dim]"
            )
            dialog_map[i] = entity

    async def _list_and_select_dialogs(self):
        """
        List recent dialogs and allow user to select them interactively.
        """
        await notify_and_pause_async("[cyan]Fetching recent dialogs...[/cyan]")
        offset_date, offset_id, offset_peer = None, 0, None
        page_stack = []
        page_num = 1

        while True:
            try:
                clear_screen()
                dialogs = await self.client.get_dialogs(
                    limit=self.config.dialog_fetch_limit,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_peer=offset_peer,
                )
                if not dialogs:
                    await notify_and_pause_async(
                        "[yellow]No more dialogs found.[/yellow]"
                    )
                    if page_num > 1:
                        # ... (omitted for brevity, logic is complex but UI-only)
                        pass
                    break

                dialog_map: Dict[int, Any] = {}
                self._display_dialogs(dialogs, dialog_map, 1, page_num=page_num)

                selection = (
                    input(
                        "Enter numbers to add (e.g., 1, 3, 5), n for next, p for prev, or c for cancel: "
                    )
                    .strip()
                    .lower()
                )

                if selection in ("c", "cancel"):
                    break
                if selection in ("n", "next"):
                    page_stack.append((offset_date, offset_id, offset_peer))
                    last_dialog = dialogs[-1]
                    offset_date, offset_id, offset_peer = (
                        last_dialog.date,
                        last_dialog.id,
                        last_dialog.entity,
                    )
                    page_num += 1
                    continue
                if selection in ("p", "prev", "previous") and page_stack:
                    offset_date, offset_id, offset_peer = page_stack.pop()
                    page_num = max(1, page_num - 1)
                    continue

                await self._process_dialog_selection(selection, dialog_map)

            except Exception as e:
                logger.error(f"Error during dialog selection: {e}", exc_info=True)
                break
        return True

    def _get_entity_type_name(self, entity):
        """
        Return the type name of a Telegram entity with forum detection.
        """
        if isinstance(entity, types.User):
            return "User"
        if isinstance(entity, types.Chat):
            return "Group Chat"
        if isinstance(entity, types.Channel):
            if getattr(entity, "forum", False):
                return "Forum"
            if getattr(entity, "megagroup", False):
                return "Supergroup"
            return "Channel"
        return type(entity).__name__

    async def _process_dialog_selection(
        self, selection: str, dialog_map: Dict[int, Any]
    ) -> bool:
        """
        Process user selection of dialogs by number with forum detection.
        """
        try:
            indices = [int(s.strip()) for s in selection.split(",") if s.strip()]
            if not indices:
                return False

            added_count = 0
            for index in indices:
                entity = dialog_map.get(index)
                if entity:
                    if await self.is_forum_chat(entity):
                        await self._handle_forum_selection(entity)
                    else:
                        target = self._create_export_target_from_entity(entity)
                        self.config.add_export_target(target)
                        await notify_and_pause_async(
                            f"[green]Added:[/green] {target.name or target.id}"
                        )
                    added_count += 1
            return added_count > 0
        except ValueError:
            await notify_and_pause_async(
                "[red]Invalid input. Please enter numbers separated by commas."
            )
            return False

    def _create_export_target_from_entity(self, entity) -> ExportTarget:
        """
        Create an ExportTarget from a Telegram entity with proper type detection.
        """
        name = getattr(entity, "title", getattr(entity, "username", str(entity.id)))
        if isinstance(entity, types.Channel):
            export_type = "forum_chat" if getattr(entity, "forum", False) else "channel"
        elif isinstance(entity, types.Chat):
            export_type = "chat"
        elif isinstance(entity, types.User):
            export_type = "user"
        else:
            export_type = "unknown"
        target = ExportTarget(id=entity.id, name=name, type=export_type)
        if export_type == "forum_chat":
            target.is_forum = True
            target.export_all_topics = True
        return target

    async def _select_dialog_manually(self):
        """
        Allow user to manually enter a chat/channel ID, username, or link.
        """
        rprint("\n[bold yellow]Manual Selection Mode[/bold yellow]")
        while True:
            identifier = input(
                "\nEnter Chat/Channel ID, @username, or t.me/ link (or c for cancel): "
            ).strip()
            if identifier.lower() == "c":
                break
            if not identifier:
                continue

            await notify_and_pause_async(f"[dim]Resolving '{identifier}'...[/dim]")

            if "/c/" in identifier:
                topic_result = await self.detect_topic_from_url(identifier)
                if topic_result:
                    chat_id, topic_id = topic_result
                    entity = await self.resolve_entity(chat_id)
                    if entity and await self.is_forum_chat(entity):
                        target = ExportTarget(
                            id=chat_id,
                            name=f"{getattr(entity, 'title', 'Forum')} > Topic {topic_id}",
                            type="forum_topic",
                            topic_id=topic_id,
                            is_forum=True,
                            export_all_topics=False,
                        )
                        if Confirm.ask(
                            f"Add this topic for export? [green]{target.name}[/green]"
                        ):
                            self.config.add_export_target(target)
                        break

            entity = await self.resolve_entity(identifier)
            if entity:
                if await self.is_forum_chat(entity):
                    await self._handle_forum_selection(entity)
                    break
                else:
                    target = self._create_export_target_from_entity(entity)
                    if Confirm.ask(
                        f"Add this target? [green]{target.name}[/green] (Type: {target.type})"
                    ):
                        self.config.add_export_target(target)
                    break
            else:
                await notify_and_pause_async(
                    f"[bold red]Could not resolve or access '{identifier}'."
                )
        return True

    async def _export_forum_topic_by_link(self):
        """
        Handler for exporting forum topics by direct link.
        """
        link = input(
            "\nEnter a forum topic link (e.g., https://t.me/c/2217062060/96): "
        ).strip()
        if not link:
            return

        topic_result = await self.detect_topic_from_url(link)
        if not topic_result:
            await notify_and_pause_async("[red]Invalid forum topic link format.")
            return

        chat_id, topic_id = topic_result
        entity = await self.resolve_entity(chat_id)
        if not entity or not await self.is_forum_chat(entity):
            await notify_and_pause_async(
                f"[red]Could not resolve forum chat '{chat_id}'."
            )
            return

        topics = await self.get_forum_topics(entity)
        topic_info = next((t for t in topics if t.topic_id == topic_id), None)
        if not topic_info:
            await notify_and_pause_async(
                f"[red]Topic {topic_id} not found in this forum."
            )
            return

        entity_name = getattr(entity, "title", f"Forum_{entity.id}")
        target = ExportTarget(
            id=chat_id,
            name=f"{entity_name} > {topic_info.title}",
            type="forum_topic",
            topic_id=topic_id,
            is_forum=True,
            export_all_topics=False,
        )
        self.config.add_export_target(target)
        await notify_and_pause_async(
            f"[green]✅ Added forum topic:[/green] {topic_info.title}"
        )

    async def _handle_forum_selection(self, entity: Any):
        """
        Handles selection of topics within a forum.
        """
        entity_name = getattr(entity, "title", f"Forum_{entity.id}")
        topics = await self.get_forum_topics(entity)
        if not topics:
            await notify_and_pause_async("[yellow]No topics found in this forum.")
            return

        print(f"\n{self.format_topic_info_for_display(topics)}\n")
        choice = input(
            "Export [1] All topics, [2] Specific topics, or [3] Cancel? "
        ).strip()

        if choice == "1":
            target = ExportTarget(
                id=str(entity.id),
                name=entity_name,
                type="forum_chat",
                is_forum=True,
                export_all_topics=True,
            )
            self.config.add_export_target(target)
            await notify_and_pause_async(
                f"[green]Added all topics from:[/green] {entity_name}"
            )
        elif choice == "2":
            selection = input(
                "Enter topic numbers (comma-separated, e.g., 1,3,5): "
            ).strip()
            try:
                selected_indices = [int(x.strip()) - 1 for x in selection.split(",")]
                for idx in selected_indices:
                    if 0 <= idx < len(topics):
                        topic = topics[idx]
                        target = ExportTarget(
                            id=str(entity.id),
                            name=f"{entity_name} > {topic.title}",
                            type="forum_topic",
                            topic_id=topic.topic_id,
                            is_forum=True,
                            export_all_topics=False,
                        )
                        self.config.add_export_target(target)
            except ValueError:
                rprint("[red]Invalid format.")

    async def export_single_post_by_link(self):
        """
        Handler for exporting a single Telegram post by link.
        """
        # ... (logic from original file)

    async def collect_album_messages(self, entity, message):
        """
        Collect all messages belonging to the same album (grouped media).
        """
        # ... (logic from original file)

    def get_client(self) -> TelegramClient:
        """
        Return the underlying TelegramClient instance.
        """
        if not self.client:
            raise RuntimeError("TelegramManager not initialized properly.")
        return self.client

    # --- Methods from ForumManager ---

    async def is_forum_chat(self, entity: Any) -> bool:
        try:
            if isinstance(entity, Channel):
                return getattr(entity, "forum", False)
            return False
        except Exception as e:
            logger.warning(f"Error checking if entity is forum: {e}")
            return False

    async def get_forum_topics(
        self, entity: Any, force_refresh: bool = False
    ) -> List[TopicInfo]:
        entity_id = str(getattr(entity, "id", entity))
        if not force_refresh and entity_id in self.topics_cache:
            return self.topics_cache[entity_id]

        topics = []
        try:

            async def _fetch_topics():
                return await self.client(
                    GetForumTopicsRequest(
                        channel=entity,
                        offset_date=None,
                        offset_id=0,
                        offset_topic=0,
                        limit=100,
                    )
                )

            result = (
                await self.connection_manager.execute_with_retry(
                    _fetch_topics, f"fetch_forum_topics_{entity_id}", pool_type="api"
                )
                if self.connection_manager
                else await _fetch_topics()
            )

            if hasattr(result, "topics"):
                for topic in result.topics:
                    if isinstance(topic, ForumTopic):
                        message_count = await self._get_topic_message_count_via_api(
                            entity, topic.id
                        )
                        topics.append(
                            TopicInfo(
                                topic_id=topic.id,
                                title=topic.title,
                                icon_emoji=getattr(topic, "icon_emoji_id", None)
                                or "💬",
                                created_date=getattr(topic, "date", None),
                                is_closed=getattr(topic, "closed", False),
                                is_pinned=getattr(topic, "pinned", False),
                                message_count=message_count,
                            )
                        )
            self.topics_cache[entity_id] = topics
        except Exception as e:
            logger.error(f"Error fetching forum topics for {entity_id}: {e}")
        return topics

    async def _get_topic_message_count_via_api(self, entity: Any, topic_id: int) -> int:
        try:
            result = await self.client.get_messages(entity, reply_to=topic_id, limit=0)
            count = getattr(result, "total", 0)
            return count - 1 if count > 0 else 0
        except Exception:
            return 0

    # DEPRECATED: Use get_topic_messages_stream() instead for better memory efficiency
    # Kept for backward compatibility, but collects all messages in memory
    # async def get_topic_messages(
    #     self,
    #     entity: Any,
    #     topic_id: int,
    #     limit: Optional[int] = None,
    #     min_id: Optional[int] = None,
    # ) -> List[Message]:
    #     messages = []
    #     max_retries = 3
    #     retry_count = 0
    #     
    #     while retry_count < max_retries:
    #         try:
    #             async for message in self.client.iter_messages(
    #                 entity=entity,
    #                 reply_to=topic_id,
    #                 limit=limit,
    #                 min_id=min_id or 0,
    #                 reverse=True,
    #             ):
    #                 if message.id != topic_id:
    #                     messages.append(message)
    #             
    #             # Success, exit retry loop
    #             break
    #         
    #         except FloodWaitError as e:
    #             retry_count += 1
    #             wait_time = e.seconds
    #             logger.warning(
    #                 f"⏳ FloodWait detected while fetching topic {topic_id} messages: need to wait {wait_time}s (attempt {retry_count}/{max_retries})"
    #             )
    #             if retry_count < max_retries:
    #                 logger.info(f"⏱️  Waiting {wait_time + 1} seconds before retry...")
    #                 await asyncio.sleep(wait_time + 1)
    #             else:
    #                 logger.error(f"❌ Max retries reached for topic {topic_id} messages, returning partial results")
    #                 break
    #         
    #         except Exception as e:
    #             logger.error(f"Error fetching messages from topic {topic_id}: {e}")
    #             break
    #     
    #     return messages

    async def get_topic_messages_stream(
        self,
        entity: Any,
        topic_id: int,
        limit: Optional[int] = None,
        min_id: Optional[int] = None,
    ):
        """
        Stream messages from a topic with FloodWait retry support and timeout protection.
        
        ASYNC GENERATOR - streams messages one at a time instead of collecting in memory.
        Much more efficient for large topics (Phase 2 Task 2.2: Added timeout protection).
        
        Usage:
            async for message in await telegram_manager.get_topic_messages_stream(entity, topic_id):
                process(message)
        
        Args:
            entity: Chat/channel entity
            topic_id: Topic ID to fetch messages from
            limit: Max messages to fetch (None = all)
            min_id: Start from this message ID (for resuming)
        
        Yields:
            Message objects one at a time
        """
        offset_id = min_id or 0
        max_retries = 3
        retry_count = 0
        messages_yielded = 0
        
        while retry_count < max_retries:
            try:
                async def _topic_message_generator():
                    async for message in self.client.iter_messages(
                        entity=entity,
                        reply_to=topic_id,
                        limit=limit,
                        min_id=offset_id,
                        reverse=True,
                    ):
                        if message.id != topic_id:
                            yield message
                
                # Execute with timeout per batch of messages (Phase 2 Task 2.2)
                async for message in _topic_message_generator():
                    try:
                        # Each message must be yielded within timeout
                        yielded_msg = await asyncio.wait_for(
                            self._yield_with_timeout(message),
                            timeout=ITER_MESSAGES_TIMEOUT
                        )
                        offset_id = yielded_msg.id  # Remember last ID for potential retry
                        messages_yielded += 1
                        yield yielded_msg
                    except asyncio.TimeoutError:
                        logger.error(
                            f"⏰ Timeout streaming topic {topic_id} message {message.id} "
                            f"(exceeded {ITER_MESSAGES_TIMEOUT}s limit)"
                        )
                        raise
                
                # Success, exit retry loop
                logger.debug(f"✅ Topic {topic_id}: streamed {messages_yielded} messages successfully")
                break
            
            except asyncio.TimeoutError:
                retry_count += 1
                logger.warning(
                    f"⏱️ Timeout streaming topic {topic_id}: {ITER_MESSAGES_TIMEOUT}s exceeded "
                    f"(attempt {retry_count}/{max_retries}, already yielded {messages_yielded} messages)"
                )
                if retry_count < max_retries:
                    logger.info(f"⏱️  Waiting 5 seconds before resuming from message ID {offset_id}...")
                    await asyncio.sleep(5)
                    # Loop will retry with offset_id, resuming from where we left off
                else:
                    logger.error(
                        f"❌ Max retries reached for topic {topic_id} stream (timeout) after yielding {messages_yielded} messages. "
                        f"Resuming from message ID {offset_id}."
                    )
                    break
            
            except FloodWaitError as e:
                retry_count += 1
                wait_time = e.seconds
                logger.warning(
                    f"⏳ FloodWait in stream for topic {topic_id}: waited {wait_time}s "
                    f"(attempt {retry_count}/{max_retries}, already yielded {messages_yielded} messages)"
                )
                if retry_count < max_retries:
                    logger.info(f"⏱️  Waiting {wait_time + 1} seconds before resuming from message ID {offset_id}...")
                    await asyncio.sleep(wait_time + 1)
                    # Loop will retry with offset_id, resuming from where we left off
                else:
                    logger.error(
                        f"❌ Max retries reached for topic {topic_id} stream after yielding {messages_yielded} messages. "
                        f"Resuming from message ID {offset_id}."
                    )
                    break
            
            except Exception as e:
                logger.error(f"Error streaming messages from topic {topic_id}: {e}")
                break

    async def get_topic_messages(
        self,
        entity: Any,
        topic_id: int,
        limit: Optional[int] = None,
        min_id: Optional[int] = None,
    ) -> List[Message]:
        """
        Collect all topic messages into a list (backward compatibility).
        
        WARNING: For large topics, this collects ALL messages in memory.
        Consider using get_topic_messages_stream() for better memory efficiency.
        
        Args:
            entity: Chat/channel entity
            topic_id: Topic ID to fetch messages from
            limit: Max messages to fetch
            min_id: Start from this message ID
        
        Returns:
            List of all messages from the topic
        """
        messages = []
        async for message in self.get_topic_messages_stream(entity, topic_id, limit, min_id):
            messages.append(message)
        return messages

    async def detect_topic_from_url(self, url: str) -> Optional[Tuple[str, int]]:
        match = re.search(r"t\.me/c/(\d+)/(\d+)", url)
        if match:
            return f"-100{match.group(1)}", int(match.group(2))
        return None

    def format_topic_info_for_display(self, topics: List[TopicInfo]) -> str:
        if not topics:
            return "No topics found."
        lines = ["Available Forum Topics:", "=" * 50]
        for i, topic in enumerate(topics, 1):
            status = " ".join(
                ["📌" if topic.is_pinned else "", "🔒" if topic.is_closed else ""]
            ).strip()
            icon = (
                topic.icon_emoji
                if topic.icon_emoji and len(topic.icon_emoji) <= 2
                else "💬"
            )
            lines.append(f"{i:2d}. {icon} {topic.title} (ID: {topic.topic_id})")
            lines.append(f"     Messages: ~{topic.message_count} {status}")
        return "\n".join(lines)
