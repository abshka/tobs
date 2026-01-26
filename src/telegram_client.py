import asyncio
import re
import sys
import os
import time
from collections import deque
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

from rich import print as rprint
from rich.panel import Panel
from rich.prompt import Confirm
from telethon import TelegramClient, types
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.errors.rpcerrorlist import PhoneCodeInvalidError
# Backwards-compat alias for older Telethon versions
try:
    from telethon.errors.rpcerrorlist import PhoneCodeInvalidError as PhoneCodeInvalidErrorRPC
except Exception:
    PhoneCodeInvalidErrorRPC = PhoneCodeInvalidError
from telethon.tl.functions.channels import GetFullChannelRequest, GetForumTopicsRequest
from telethon.tl.types import Channel, ForumTopic, Message, User

from src.config import ITER_MESSAGES_TIMEOUT, Config, ExportTarget
from src.core.connection import PoolType
from src.exceptions import TelegramConnectionError
from src.input_peer_cache import InputPeerCache
from src.logging_context import update_context_prefix
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


class ThrottleDetector:
    """
    Detects server-side throttling by monitoring API request latencies.
    
    When Telegram starts throttling, request latencies increase significantly
    (typically 3-10x baseline). This detector tracks latencies and warns when
    throttling is detected.
    """
    
    def __init__(self, window_size: int = 100, threshold_multiplier: float = 3.0):
        """
        Initialize throttle detector.
        
        Args:
            window_size: Number of recent requests to track
            threshold_multiplier: Latency multiplier to trigger warning (3.0 = 3x baseline)
        """
        self.request_times: deque = deque(maxlen=window_size)
        self.window_size = window_size
        self.threshold_multiplier = threshold_multiplier
        self.avg_latency_ms = 0.0
        self.baseline_latency_ms = 0.0
        self._throttle_detected = False
    
    def record(self, latency_ms: float):
        """
        Record a request latency and check for throttling.
        
        Args:
            latency_ms: Request latency in milliseconds
        """
        self.request_times.append(latency_ms)
        
        # Calculate average latency
        if len(self.request_times) > 0:
            self.avg_latency_ms = sum(self.request_times) / len(self.request_times)
        
        # Establish baseline after initial warmup
        if len(self.request_times) == self.window_size and self.baseline_latency_ms == 0:
            self.baseline_latency_ms = min(self.request_times)
            logger.debug(f"📊 ThrottleDetector baseline: {self.baseline_latency_ms:.0f}ms")
        
        # Detect throttling: avg latency > threshold * baseline
        if len(self.request_times) >= 20 and self.baseline_latency_ms > 0:
            threshold = self.baseline_latency_ms * self.threshold_multiplier
            
            if self.avg_latency_ms > threshold:
                if not self._throttle_detected:
                    logger.warning(
                        f"🐌 Server throttling detected: "
                        f"avg latency {self.avg_latency_ms:.0f}ms "
                        f"(baseline: {self.baseline_latency_ms:.0f}ms, "
                        f"threshold: {threshold:.0f}ms)"
                    )
                    self._throttle_detected = True
            else:
                if self._throttle_detected:
                    logger.info(f"✅ Throttling cleared: avg latency {self.avg_latency_ms:.0f}ms")
                    self._throttle_detected = False
    
    def is_throttled(self) -> bool:
        """Check if currently throttled."""
        return self._throttle_detected
    
    def get_stats(self) -> Dict[str, float]:
        """Get current statistics."""
        return {
            "avg_latency_ms": self.avg_latency_ms,
            "baseline_latency_ms": self.baseline_latency_ms,
            "sample_count": len(self.request_times),
            "is_throttled": self._throttle_detected,
        }


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

    def __init__(
        self,
        config: Config,
        connection_manager: Any = None,
        cache_manager: Any = None,
    ):
        self.config = config
        self.connection_manager = connection_manager
        self.cache_manager = cache_manager
        proxy_info = None
        # Be defensive: tests or simple DummyConfig objects may omit proxy attributes
        if getattr(config, "proxy_type", None) and getattr(config, "proxy_addr", None) and getattr(config, "proxy_port", None):
            proxy_scheme = getattr(config, "proxy_type").lower()
            if proxy_scheme not in ["socks4", "socks5", "http"]:
                logger.warning(
                    f"Unsupported proxy type for Telethon: '{proxy_scheme}'. Ignoring."
                )
            else:
                proxy_info = (
                    proxy_scheme,
                    getattr(config, "proxy_addr"),
                    getattr(config, "proxy_port"),
                )

        base_timeout = getattr(config.performance, "base_download_timeout", 300.0)

        # Security S-5: Socket/connection timeouts
        # Telethon uses MTProto with built-in socket timeouts (not aiohttp).
        # Configuration:
        #   - timeout=300s: total request timeout (prevents indefinite hangs)
        #   - connection_retries=20: max connection attempts before failure
        #   - retry_delay=1s: delay between retry attempts
        #   - auto_reconnect=True: automatic reconnection on connection loss
        # This prevents DoS via socket hanging (total max hang: ~20s retries + 300s timeout)

        # Legacy automatic conversion support removed; use Telethon .session file for authentication.
        # If you need to migrate a legacy session, convert the file externally and place the resulting .session file in the app directory.

        api_id = getattr(config, "api_id", None)
        api_hash = getattr(config, "api_hash", None)

        if api_id and api_hash:
            self.client = TelegramClient(
                getattr(config, "session_name", "tobs_session"),
                api_id,
                api_hash,
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
        else:
            logger.info("API credentials not provided; TelegramClient not instantiated (testing/offline mode)")
            self.client = None
        self._original_client = self.client  # Store original client for operations that don't support Takeout
        self.entity_cache: Dict[str, Any] = {}
        self.topics_cache: Dict[Union[str, int], List[TopicInfo]] = {}
        self.client_connected = False
        self._external_takeout_id: Optional[int] = None
        
        # C-3: InputPeer cache for reducing entity resolution API calls
        cache_size = getattr(config.performance, "input_peer_cache_size", 1000)
        cache_ttl = getattr(config.performance, "input_peer_cache_ttl", 3600.0)
        self._input_peer_cache = InputPeerCache(
            max_size=cache_size,
            ttl_seconds=cache_ttl
        )
        logger.info(f"InputPeerCache enabled: size={cache_size}, ttl={cache_ttl}s")
        
        # Throttle detector for server-side throttling detection
        self._throttle_detector = ThrottleDetector(window_size=100, threshold_multiplier=3.0)
        logger.debug("ThrottleDetector initialized")

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
            # Request an auth code and allow the user a few attempts to enter it correctly.
            await self.client.send_code_request(self.config.phone_number)
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                # If running non-interactively (no TTY), read code from env var if provided
                env_code = os.getenv("EXPORT_TG_CODE")
                if env_code:
                    rprint("[dim]Using code from EXPORT_TG_CODE environment variable[/dim]")
                    code = env_code.strip()
                else:
                    if not sys.stdin.isatty():
                        raise TelegramConnectionError(
                            "No TTY available for code entry. Set EXPORT_TG_CODE env var for non-interactive runs."
                        )
                    rprint(f"[bold]Enter the code you received in Telegram (attempt {attempt}/{max_attempts}):[/bold]", end=" ")
                    try:
                        code = input().strip()
                    except EOFError:
                        raise TelegramConnectionError("No input available for code entry (EOF)")

                try:
                    await self.client.sign_in(self.config.phone_number, code)
                    return True
                except (PhoneCodeInvalidError, PhoneCodeInvalidErrorRPC):
                    logger.warning("Invalid phone code entered by user.")
                    rprint("[red]Invalid code. Please try again.[/red]")
                    if attempt == max_attempts:
                        raise TelegramConnectionError(
                            "Maximum code entry attempts exceeded."
                        )
                    continue
                except SessionPasswordNeededError:
                    rprint("[bold]Enter your 2FA password:[/bold]", end=" ")
                    try:
                        password = input().strip()
                    except EOFError:
                        raise TelegramConnectionError(
                            "No input available for 2FA password (EOF)"
                        )
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

    async def get_total_message_count(self, entity: Any) -> int:
        """
        Get total message count for entity WITHOUT loading all messages.
        
        Uses Telegram API's GetHistoryRequest with limit=1 to efficiently
        fetch total count from history.count field.
        
        Args:
            entity: Telegram entity (chat/channel/user)
        
        Returns:
            Total message count (0 if error or cannot determine)
        """
        try:
            from telethon.tl.functions.messages import GetHistoryRequest
            
            # Get input peer for entity
            input_peer = await self.client.get_input_entity(entity)
            
            # Fetch history with limit=1 to get count efficiently
            result = await self.client(GetHistoryRequest(
                peer=input_peer,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=1,
                max_id=0,
                min_id=0,
                hash=0
            ))
            
            # Extract count based on response type
            from telethon.tl.types.messages import Messages, MessagesSlice, ChannelMessages
            
            if isinstance(result, Messages):
                # For Messages: count = len(messages)
                count = len(result.messages)
            elif isinstance(result, (MessagesSlice, ChannelMessages)):
                # For MessagesSlice/ChannelMessages: use .count field
                count = result.count
            else:
                # Fallback
                count = getattr(result, 'count', 0)
            
            # 🔥 CRITICAL: Sanitize unrealistic counts (INT32_MAX placeholder)
            MAX_REASONABLE_COUNT = 100_000_000  # 100M messages = ~10 years of 24/7 activity
            if count > MAX_REASONABLE_COUNT:
                logger.warning(
                    f"⚠️ Unrealistic message count {count:,} (likely placeholder), "
                    f"falling back to 0 (streaming mode)"
                )
                return 0  # Fall back to streaming without total (shows 0/0)
            
            logger.info(f"📊 Total message count for {entity}: {count:,}")
            return count if count > 0 else 0  # Ensure non-negative
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to get message count: {e}")
            return 0  # Return 0 on error (streaming mode)

            return 0

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
    
    async def get_input_entity_cached(self, entity: Any) -> Any:
        """
        Get InputPeer for entity with caching support (C-3 optimization).
        
        Reduces redundant get_input_entity API calls by caching InputPeer objects.
        Uses LRU cache with TTL for automatic expiration.
        
        Args:
            entity: Telegram entity (can be entity object, ID, or username)
        
        Returns:
            InputPeer object (InputPeerUser, InputPeerChannel, or InputPeerChat)
        """
        # Extract entity ID for cache key
        entity_id = None
        if isinstance(entity, (int, str)):
            # For raw IDs or usernames, resolve first
            resolved = await self.resolve_entity(entity)
            if resolved:
                entity_id = getattr(resolved, "id", None)
                entity = resolved
        else:
            entity_id = getattr(entity, "id", None)
        
        if entity_id is None:
            # Fallback to direct API call if ID cannot be extracted
            return await self.client.get_input_entity(entity)
        
        # Check cache first
        cached_peer = self._input_peer_cache.get(entity_id)
        if cached_peer is not None:
            logger.debug(f"InputPeerCache HIT for entity_id={entity_id}")
            return cached_peer
        
        # Cache miss - fetch from API
        logger.debug(f"InputPeerCache MISS for entity_id={entity_id}")
        input_peer = await self.client.get_input_entity(entity)
        
        # Store in cache
        self._input_peer_cache.set(entity_id, input_peer)
        
        return input_peer
    
    def get_input_peer_cache_metrics(self) -> dict:
        """Get InputPeer cache performance metrics."""
        return self._input_peer_cache.get_metrics()

    async def fetch_messages(
        self,
        entity: Any,
        limit: Optional[int] = None,
        min_id: Optional[int] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> AsyncGenerator[Message, None]:
        """
        Fetch messages from a Telegram entity with FloodWaitError retry support and timeout.
        Yields messages one-by-one with timeout protection.

        Supports lazy loading pagination when enabled in config.

        Args:
            entity: Telegram entity to fetch from
            limit: Maximum messages to fetch (None = all)
            min_id: Start from this message ID
            page: Page number for pagination (0-based, requires page_size)
            page_size: Messages per page (uses config.lazy_message_page_size if None)
        """
        # 🔍 CRITICAL DEBUG: Verify which fetch_messages is called
        logger.info(
            f"🔍 TelegramManager.fetch_messages() CALLED (BASE CLASS) - entity={entity}, limit={limit}, page={page}"
        )

        # Handle pagination parameters
        if page is not None:
            if page_size is None:
                page_size = self.config.lazy_message_page_size
            # Calculate offset_id for pagination
            # Note: This is a simplified implementation
            # In practice, you'd need to track message IDs for proper pagination
            offset_id: int = 0  # Would need to be calculated based on page
            logger.info(f"Fetching page {page} with page size {page_size}")
        else:
            offset_id = 0

        if min_id:
            logger.info(f"{update_context_prefix()}Starting from message ID: {min_id}")

        # Apply pagination limit if specified
        if page is not None and page_size:
            effective_limit: Optional[int] = page_size
        else:
            effective_limit = limit

        # Optimization: Use batch fetching with get_messages instead of iter_messages
        batch_size = getattr(self.config, "batch_fetch_size", 100)
        total_fetched = 0
        current_offset_id = offset_id

        logger.info(f"🚀 Starting batch fetch (batch_size={batch_size})")

        while True:
            # Check if we've reached the limit
            if effective_limit is not None and total_fetched >= effective_limit:
                break

            # Calculate batch limit
            current_batch_limit = batch_size
            if effective_limit is not None:
                current_batch_limit = min(batch_size, effective_limit - total_fetched)

            # Retry loop for the batch
            batch_messages = []
            max_retries = 3
            retry_count = 0

            while retry_count < max_retries:
                try:
                    # Fetch batch of messages
                    # Note: using reverse=True to fetch messages in chronological order (oldest first).
                    # We get a batch of oldest messages first and then use the last element's id as the offset
                    # for the next batch to continue forward in time.
                    batch_messages = await self.client.get_messages(
                        entity=entity,
                        limit=current_batch_limit,
                        offset_id=current_offset_id,
                        min_id=min_id or 0,
                        wait_time=self.config.request_delay,
                        reverse=True,
                    )
                    break  # Success
                except FloodWaitError as e:
                    retry_count += 1
                    wait_time = e.seconds
                    logger.warning(
                        f"⏳ FloodWait detected: need to wait {wait_time}s (attempt {retry_count}/{max_retries})"
                    )
                    if retry_count < max_retries:
                        await asyncio.sleep(wait_time + 1)
                    else:
                        logger.error(
                            "❌ Max retries reached for batch fetching (FloodWait)"
                        )
                        raise
                except Exception as e:
                    retry_count += 1
                    logger.warning(
                        f"⚠️ Error fetching batch: {e} (attempt {retry_count}/{max_retries})"
                    )
                    if retry_count < max_retries:
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"❌ Max retries reached for batch fetching: {e}")
                        raise

            if not batch_messages:
                break  # No more messages

            # Process batch (yield in chronological order when reverse=True)
            if batch_messages:
                first_id = batch_messages[0].id
                last_id = batch_messages[-1].id
                logger.info(f"📦 Batch: {len(batch_messages)} messages, IDs {first_id} → {last_id} (reverse=True)")
            
            for message in batch_messages:
                # Accept Telethon Message instances or any message-like object with an id.
                # Skip service messages that have an 'action' attribute set.
                if getattr(message, "action", None):
                    continue
                yield message
                total_fetched += 1

                if effective_limit is not None and total_fetched >= effective_limit:
                    break

            # Update offset for next batch
            # With reverse=True: batch goes from old→new, so last message is newest
            # Next batch starts AFTER this ID to continue forward in time
            if batch_messages:
                current_offset_id = batch_messages[-1].id

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
        offset_date: Optional[Any] = None
        offset_id: Optional[int] = 0
        offset_peer: Optional[Any] = None
        page_stack = []
        page_num = 1

        while True:
            try:
                clear_screen()
                dialogs = await self.client.get_dialogs(
                    limit=self.config.dialog_fetch_limit,
                    offset_date=offset_date,
                    offset_id=offset_id or 0,
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
                        int(last_dialog.id or 0),
                        last_dialog.entity,
                    )
                    page_num += 1
                    continue
                if selection in ("p", "prev", "previous") and page_stack:
                    popped = page_stack.pop()
                    offset_date, offset_id, offset_peer = popped
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

        # Use robust parser first
        from src.utils import LinkParser

        parsed = LinkParser.parse(link)

        chat_id = None
        topic_id = None

        if parsed:
            chat_id = parsed["peer"]
            # If topic_id is explicit (from ?thread= or /c/ID/TOPIC/MSG)
            if parsed["topic_id"]:
                topic_id = parsed["topic_id"]
            # If it's a private link /c/ID/MSG, the MSG might be the topic ID if it's a topic creation message
            # But usually /c/ID/MSG points to a message.
            # If we are looking for a TOPIC, we assume the user pasted a link TO the topic (which is usually the first message)
            elif parsed["message_id"]:
                topic_id = parsed["message_id"]
        else:
            # Fallback to old regex
            topic_result = await self.detect_topic_from_url(link)
            if topic_result:
                chat_id, topic_id = topic_result

        if not chat_id or not topic_id:
            await notify_and_pause_async("[red]Invalid forum topic link format.")
            return

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
        self,
        entity: Any,
        force_refresh: bool = False,
        limit: Optional[int] = None,
        offset_topic: int = 0,
    ) -> List[TopicInfo]:
        entity_id = str(getattr(entity, "id", entity))

        # Check if lazy pagination is enabled
        if self.config.enable_lazy_loading and self.config.lazy_topic_pagination:
            # For lazy loading, implement pagination
            return await self._get_forum_topics_paginated(entity, limit, offset_topic)

        # Original behavior for non-lazy mode
        if not force_refresh and entity_id in self.topics_cache:
            return self.topics_cache[entity_id]

        topics = []
        try:
            # Use configured limit or default to 100
            fetch_limit = limit or 100

            # GetForumTopicsRequest doesn't work with Takeout, use original client
            # If self.client is a TakeoutSessionWrapper, extract the underlying client
            client_to_use = self.client
            if hasattr(self.client, 'client'):
                # This is TakeoutSessionWrapper, get the underlying client
                client_to_use = self.client.client
                logger.info(f"🔍 Detected TakeoutSessionWrapper, using underlying client for GetForumTopicsRequest")
            elif hasattr(self, '_original_client') and self._original_client:
                # Fallback to _original_client if set
                client_to_use = self._original_client
                logger.info(f"🔍 Using _original_client for GetForumTopicsRequest")
            else:
                logger.info(f"🔍 Using self.client directly for GetForumTopicsRequest")
            
            # Check if client is connected (is_connected is a property, not a method)
            if hasattr(client_to_use, 'is_connected'):
                is_connected = client_to_use.is_connected()
                logger.info(f"🔍 Client connection status: {is_connected}")
            
            async def _fetch_topics():
                return await client_to_use(
                    GetForumTopicsRequest(
                        channel=entity,
                        offset_date=None,
                        offset_id=0,
                        offset_topic=offset_topic,
                        limit=fetch_limit,
                    )
                )

            result = (
                await self.connection_manager.execute_with_retry(
                    _fetch_topics, f"fetch_forum_topics_{entity_id}", PoolType.API
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

    async def _get_forum_topics_paginated(
        self, entity: Any, limit: Optional[int] = None, offset_topic: int = 0
    ) -> List[TopicInfo]:
        """
        Get forum topics with pagination support for lazy loading.

        Args:
            entity: Forum entity
            limit: Maximum topics to fetch (uses config.lazy_topic_page_size if None)
            offset_topic: Topic offset for pagination

        Returns:
            List of TopicInfo objects
        """
        page_size = limit or self.config.lazy_topic_page_size
        topics: List[TopicInfo] = []
        current_offset = offset_topic

        # GetForumTopicsRequest doesn't work with Takeout, use original client
        # If self.client is a TakeoutSessionWrapper, extract the underlying client
        client_to_use = self.client
        if hasattr(self.client, 'client'):
            # This is TakeoutSessionWrapper, get the underlying client
            client_to_use = self.client.client
        elif hasattr(self, '_original_client') and self._original_client:
            # Fallback to _original_client if set
            client_to_use = self._original_client

        try:
            while len(topics) < page_size:
                remaining = page_size - len(topics)
                fetch_limit = min(remaining, 100)  # Telegram API limit

                async def _fetch_page():
                    return await client_to_use(
                        GetForumTopicsRequest(
                            channel=entity,
                            offset_date=None,
                            offset_id=0,
                            offset_topic=current_offset,
                            limit=fetch_limit,
                        )
                    )

                result = (
                    await self.connection_manager.execute_with_retry(
                        _fetch_page,
                        f"fetch_forum_topics_page_{current_offset}",
                        PoolType.API,
                    )
                    if self.connection_manager
                    else await _fetch_page()
                )

                if not hasattr(result, "topics") or not result.topics:
                    break  # No more topics

                page_topics = []
                for topic in result.topics:
                    if isinstance(topic, ForumTopic):
                        message_count = await self._get_topic_message_count_via_api(
                            entity, topic.id
                        )
                        topic_info = TopicInfo(
                            topic_id=topic.id,
                            title=topic.title,
                            icon_emoji=getattr(topic, "icon_emoji_id", None) or "💬",
                            created_date=getattr(topic, "date", None),
                            is_closed=getattr(topic, "closed", False),
                            is_pinned=getattr(topic, "pinned", False),
                            message_count=message_count,
                        )
                        page_topics.append(topic_info)

                if not page_topics:
                    break  # No valid topics in this page

                topics.extend(page_topics)
                current_offset = page_topics[-1].topic_id

                # If we got fewer topics than requested, we've reached the end
                if len(page_topics) < fetch_limit:
                    break

            logger.debug(
                f"Fetched {len(topics)} topics with pagination (offset: {offset_topic})"
            )

        except Exception as e:
            logger.error(f"Error fetching paginated forum topics: {e}")

        return topics

    async def _get_topic_message_count_via_api(self, entity: Any, topic_id: int) -> int:
        cache_key = f"topic_msg_count_{entity.id}_{topic_id}"

        # Check cache first
        if self.cache_manager:
            cached_count = await self.cache_manager.get(cache_key)
            if cached_count is not None:
                logger.info(f"🔍 Cache hit for topic {topic_id}: returning {cached_count} (cached value)")
                return cached_count

        count = 0
        try:
            # For forum topics, we MUST use get_messages with reply_to parameter
            # GetFullChannelRequest returns total channel messages, not per-topic
            logger.info(f"🔍 Fetching message count for topic {topic_id} via API...")
            result = await self.client.get_messages(
                entity, reply_to=topic_id, limit=0
            )
            # get_messages(limit=0) returns an object with 'total' attribute
            count = getattr(result, "total", 0)
            logger.info(
                f"✅ Topic {topic_id} has {count} messages (fresh from API)"
            )

        except Exception as e:
            logger.warning(
                f"Error fetching topic message count for {entity.id}/{topic_id}: {e}"
            )
            count = 0

        # Store in cache
        if self.cache_manager:
            await self.cache_manager.set(cache_key, count, ttl=3600)  # Cache for 1 hour

        return count

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
        Much more efficient for large topics.

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
        messages_yielded = 0

        # Optimization: Use batch fetching with get_messages instead of iter_messages
        batch_size = getattr(self.config, "batch_fetch_size", 100)
        current_offset_id = offset_id

        logger.info(
            f"🚀 Starting topic stream batch fetch (topic={topic_id}, batch_size={batch_size})"
        )

        while True:
            # Check if we've reached the limit
            if limit is not None and messages_yielded >= limit:
                break

            # Calculate batch limit
            current_batch_limit = batch_size
            if limit is not None:
                current_batch_limit = min(batch_size, limit - messages_yielded)

            # Retry loop for the batch
            batch_messages = []
            max_retries = 3
            retry_count = 0

            while retry_count < max_retries:
                try:
                    # Fetch batch of topic messages with latency tracking
                    start_time = time.time()
                    batch_messages = await self.client.get_messages(
                        entity=entity,
                        limit=current_batch_limit,
                        offset_id=current_offset_id,
                        reply_to=topic_id,
                        min_id=min_id or 0,
                        wait_time=self.config.request_delay,
                    )
                    
                    # Record latency for throttle detection
                    latency_ms = (time.time() - start_time) * 1000
                    self._throttle_detector.record(latency_ms)
                    
                    break  # Success
                except FloodWaitError as e:
                    retry_count += 1
                    wait_time = e.seconds
                    logger.warning(
                        f"⏳ FloodWait detected in topic {topic_id}: need to wait {wait_time}s (attempt {retry_count}/{max_retries})"
                    )
                    if retry_count < max_retries:
                        # Adaptive backoff: increase wait time if throttling detected
                        if self._throttle_detector.is_throttled():
                            extra_wait = min(wait_time * 0.5, 60)  # Add up to 60s extra
                            logger.info(f"🐌 Throttling detected, adding {extra_wait:.0f}s extra wait")
                            await asyncio.sleep(wait_time + extra_wait)
                        else:
                            await asyncio.sleep(wait_time + 1)
                    else:
                        logger.error(
                            f"❌ Max retries reached for topic {topic_id} batch fetching"
                        )
                        raise
                except Exception as e:
                    retry_count += 1
                    logger.warning(
                        f"⚠️ Error fetching topic batch: {e} (attempt {retry_count}/{max_retries})"
                    )
                    if retry_count < max_retries:
                        await asyncio.sleep(5)
                    else:
                        logger.error(f"❌ Max retries reached for topic fetching: {e}")
                        raise

            if not batch_messages:
                break  # No more messages

            # Process batch
            for message in batch_messages:
                # Skip the topic creation message itself if returned
                if message.id != topic_id:
                    yield message
                    messages_yielded += 1

                    if limit is not None and messages_yielded >= limit:
                        break

            # Update offset for next batch
            if batch_messages:
                current_offset_id = batch_messages[-1].id

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
        async for message in self.get_topic_messages_stream(
            entity, topic_id, limit, min_id
        ):
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
