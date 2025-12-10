"""
Interactive UI module for TOBS.
Handles user interaction, menu systems, and interactive configuration.
Provides modular user interface functionality.
"""

import asyncio
from pathlib import Path
from typing import List, Literal, Optional, cast

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from ..config import Config, ExportTarget
from ..telegram_client import TelegramManager
from ..utils import clear_screen, get_entity_display_name, logger, prompt_int


class InteractiveUI:
    """
    Interactive user interface for TOBS configuration and operation.
    Provides menu-driven interface for target selection and configuration.
    """

    def __init__(self, config: Config, telegram_manager: TelegramManager):
        self.config = config
        self.telegram_manager = telegram_manager
        self.console = Console()

    async def run_interactive_mode(self) -> bool:
        """
        Run interactive mode for target selection and configuration.

        Returns:
            True if user wants to proceed with export, False to exit
        """
        try:
            clear_screen()
            self._show_welcome_banner()

            while True:
                clear_screen()
                self._show_welcome_banner()
                action = await self._show_main_menu()

                if action == "export":
                    if self.config.export_targets:
                        return True
                    else:
                        rprint(
                            "[yellow]No targets selected. Please select targets first.[/yellow]"
                        )
                        continue

                elif action == "select_targets":
                    await self._run_target_selection()

                elif action == "configure":
                    await self._run_configuration()

                elif action == "show_targets":
                    self._show_selected_targets()

                elif action == "performance":
                    await self._configure_performance()

                elif action == "exit":
                    return False

        except KeyboardInterrupt:
            rprint("\n[yellow]Operation cancelled by user.[/yellow]")
            return False
        except Exception as e:
            logger.error(f"Interactive mode error: {e}")
            rprint(f"[red]Error in interactive mode: {e}[/red]")
            return False

    def _show_welcome_banner(self):
        """Display welcome banner and current status."""
        banner_text = """
[bold blue]TOBS - Telegram Exporter[/bold blue]
        """

        panel = Panel(
            banner_text.strip(),
            title="[bold green]Welcome to TOBS[/bold green]",
            border_style="green",
        )
        self.console.print(panel)

    async def _show_main_menu(self) -> str:
        """
        Show main menu and get user choice.

        Returns:
            Selected action as string
        """
        rprint("\n[bold cyan]Main Menu[/bold cyan]")
        rprint("=" * 40)

        menu_options = [
            ("1", "select_targets", "Select Export Targets"),
            (
                "2",
                "show_targets",
                f"Show Selected Targets ({len(self.config.export_targets)} selected)",
            ),
            ("3", "configure", "Configure Export Settings"),
            ("4", "performance", "Performance Settings"),
            ("5", "export", "[bold green]Start Export[/bold green]"),
            ("6", "exit", "Exit"),
        ]

        for key, _, description in menu_options:
            rprint(f"[cyan]{key}.[/cyan] {description}")

        while True:
            choice = Prompt.ask("\n[bold]Choose an option[/bold]", default="5")

            for key, action, _ in menu_options:
                if choice == key:
                    return action

            rprint("[red]Invalid choice. Please try again.[/red]")

    async def _run_target_selection(self):
        """Run interactive target selection process with pagination."""
        try:
            # Get available chats/channels
            entities = await self.telegram_manager.get_available_entities()

            if not entities:
                clear_screen()
                rprint("[bold cyan]Target Selection[/bold cyan]")
                rprint("=" * 40)
                rprint("[yellow]No chats or channels found.[/yellow]")
                input("\nPress Enter to continue...")
                return

            # Use pagination for entity selection
            await self._paginated_entity_selection(entities)

        except Exception as e:
            logger.error(f"Target selection failed: {e}")
            clear_screen()
            rprint("[bold cyan]Target Selection[/bold cyan]")
            rprint("=" * 40)
            rprint(f"[red]Error during target selection: {e}[/red]")
            input("\nPress Enter to continue...")

    async def _paginated_entity_selection(self, entities: List):
        """Display entities with pagination and allow selection by numbers."""
        page_size = 20
        current_page = 0
        total_pages = (len(entities) - 1) // page_size + 1

        while True:
            clear_screen()
            rprint("[bold cyan]Target Selection[/bold cyan]")
            rprint("=" * 40)

            # Calculate page bounds
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, len(entities))
            page_entities = entities[start_idx:end_idx]

            # Display entities table for current page
            table = Table(
                title=f"Available Chats and Channels (Page {current_page + 1}/{total_pages})"
            )
            table.add_column("№", style="cyan", width=3)
            table.add_column("Name", style="green")
            table.add_column("Type", style="yellow", width=12)
            table.add_column("ID", style="blue", width=15)

            entity_map = {}
            for i, entity in enumerate(page_entities, 1):
                # Use our smart type detection instead of raw type name
                entity_type = self._get_entity_type(entity)
                # Capitalize for display
                display_type = entity_type.replace("_", " ").title()
                entity_id = getattr(entity, "id", "Unknown")
                entity_title = getattr(
                    entity, "title", getattr(entity, "username", str(entity_id))
                )

                table.add_row(str(i), entity_title, display_type, str(entity_id))
                entity_map[i] = entity

            self.console.print(table)

            # Show navigation and selection options
            rprint("\n[bold]Options:[/bold]")
            rprint("• Enter numbers to select (e.g., 1, 3, 5)")
            rprint("• 'l' or 'link' to select by link/ID")
            if current_page < total_pages - 1:
                rprint("• 'n' or 'next' for next page")
            if current_page > 0:
                rprint("• 'p' or 'prev' for previous page")
            rprint("• 'c' or 'cancel' to return to main menu")

            selection = input("\nYour choice: ").strip().lower()

            if selection in ("c", "cancel"):
                break
            elif selection in ("l", "link"):
                # Handle link/ID based selection
                await self._add_target_by_link()
                continue
            elif selection in ("n", "next") and current_page < total_pages - 1:
                current_page += 1
                continue
            elif selection in ("p", "prev", "previous") and current_page > 0:
                current_page -= 1
                continue
            else:
                # Process number selection
                try:
                    # Parse selected numbers
                    if selection:
                        selected_nums = []
                        for num_str in selection.replace(",", " ").split():
                            num = int(num_str.strip())
                            if 1 <= num <= len(page_entities):
                                selected_nums.append(num)

                        # Add selected entities to config
                        added_count = 0
                        for num in selected_nums:
                            if num in entity_map:
                                entity = entity_map[num]
                                entity_type = self._get_entity_type(entity)
                                entity_name = get_entity_display_name(entity)

                                # Check if already added
                                if any(
                                    t.id == entity.id
                                    for t in self.config.export_targets
                                ):
                                    rprint(
                                        f"[yellow]Target '{entity_name}' is already added.[/yellow]"
                                    )
                                    continue

                                # Different handling based on entity type
                                if entity_type == "forum":
                                    # Handle forum selection (show topics)
                                    await self._handle_forum_target_selection(
                                        entity, entity_name
                                    )
                                elif entity_type in ["channel", "chat"]:
                                    # Handle channel/group with message range selection
                                    await self._handle_regular_target_selection(
                                        entity, entity_name, entity_type
                                    )
                                elif entity_type == "user":
                                    # Handle user (private chat) with message range selection
                                    await self._handle_regular_target_selection(
                                        entity, entity_name, entity_type
                                    )
                                else:
                                    rprint(
                                        f"[yellow]Unknown entity type: {entity_type}[/yellow]"
                                    )

                                added_count += 1

                        if added_count > 0:
                            rprint(f"[green]Added {added_count} target(s).[/green]")
                        if selected_nums and added_count == 0:
                            rprint(
                                "[yellow]All selected targets were already added.[/yellow]"
                            )

                        input("\nPress Enter to continue...")

                except ValueError:
                    rprint(
                        "[red]Invalid input. Please enter numbers separated by commas or spaces.[/red]"
                    )
                    input("\nPress Enter to continue...")

    async def _handle_regular_target_selection(
        self, entity, entity_name: str, entity_type: str
    ):
        """
        Handle selection for regular targets (channels, chats, users) with message range options.
        """
        # Show message ID preview
        latest_id = await self._show_message_id_preview(entity, entity_name)

        # Ask user to choose input method
        input_mode = Prompt.ask(
            f"\n[cyan]How do you want to specify the export range for '{entity_name}'?[/cyan]",
            choices=["all", "last", "from_id"],
            default="all",
        )

        start_message_id = 0  # Default: all messages

        if input_mode == "last":
            # User wants last N messages
            last_n_input = Prompt.ask(
                "[cyan]How many last messages to export?[/cyan]\n"
                "[dim](e.g., 50, 100, 500)[/dim]",
                default="100",
            ).strip()

            try:
                last_n = int(last_n_input)
                if last_n <= 0:
                    rprint("[yellow]Invalid count, using all messages[/yellow]")
                    start_message_id = 0
                elif latest_id is not None:
                    # Calculate start_message_id from latest_id - N + 1
                    calculated_id = max(0, latest_id - last_n + 1)
                    start_message_id = calculated_id
                    rprint(
                        f"[green]✓ Will export last {last_n} messages (from ID {calculated_id})[/green]"
                    )
                else:
                    rprint(
                        "[yellow]⚠️  Could not determine latest message ID, using all messages[/yellow]"
                    )
                    start_message_id = 0
            except ValueError:
                rprint("[yellow]Invalid input, using all messages[/yellow]")
                start_message_id = 0

        elif input_mode == "from_id":
            # User wants to specify exact message ID
            start_id_input = Prompt.ask(
                "[cyan]Start from message ID?[/cyan]\n[dim](Enter message ID)[/dim]",
                default="0",
            ).strip()

            try:
                start_message_id = int(start_id_input)
                if start_message_id < 0:
                    start_message_id = 0
                    rprint("[yellow]Invalid ID, using 0 (all messages)[/yellow]")
                elif start_message_id > 0:
                    rprint(
                        f"[green]✓ Will start from message ID: {start_message_id}[/green]"
                    )
            except ValueError:
                start_message_id = 0
                rprint("[yellow]Invalid input, using 0 (all messages)[/yellow]")

        # Create and add target
        target = ExportTarget(
            id=entity.id,
            name=entity_name,
            type=entity_type,
            start_message_id=start_message_id,
        )
        self.config.add_export_target(target)
        rprint(f"[green]✓ Added: {entity_name}[/green]")

    async def _handle_forum_target_selection(self, entity, entity_name: str):
        """
        Handle selection for forum targets with topic selection.
        """
        rprint(f"\n[cyan]📋 '{entity_name}' is a forum with topics[/cyan]")

        # Fetch forum topics
        rprint("[dim]Fetching forum topics...[/dim]")
        try:
            topics = await self.telegram_manager.get_forum_topics(entity)
        except Exception as e:
            logger.error(f"Failed to fetch forum topics for {entity_name}: {e}")
            rprint(f"[red]✗ Error fetching forum topics: {e}[/red]")
            rprint(
                "[yellow]This forum might be inaccessible or have restricted permissions[/yellow]"
            )
            return

        if not topics:
            rprint("[yellow]⚠️  No topics found in this forum[/yellow]")
            rprint(
                "[dim]This could mean the forum is empty or you don't have access[/dim]"
            )
            return

        # Display topics (all topics including closed ones)
        rprint(f"\n[bold cyan]Available topics in '{entity_name}':[/bold cyan]")
        for i, topic in enumerate(topics, 1):
            status = ""
            if topic.is_pinned:
                status += " 📌"
            if topic.is_closed:
                status += " 🔒"
            rprint(f"  {i}. {topic.title} ({topic.message_count} messages){status}")

        # Ask user what to export
        rprint("\n[bold]What would you like to export?[/bold]")
        export_choice = Prompt.ask(
            "[cyan]Choose option[/cyan]",
            choices=["all", "select", "cancel"],
            default="all",
        )

        if export_choice == "cancel":
            rprint("[dim]Cancelled forum selection[/dim]")
            return

        if export_choice == "all":
            # Export all topics
            target = ExportTarget(
                id=entity.id,
                name=entity_name,
                type="forum",
                is_forum=True,
                export_all_topics=True,
            )
            self.config.add_export_target(target)
            rprint(f"[green]✓ Added all topics from: {entity_name}[/green]")

        elif export_choice == "select":
            # Export specific topics
            selection = Prompt.ask(
                "[cyan]Enter topic numbers (comma-separated, e.g., 1,3,5)[/cyan]"
            ).strip()

            try:
                selected_indices = [int(x.strip()) - 1 for x in selection.split(",")]
                added_topics = []

                for idx in selected_indices:
                    if 0 <= idx < len(topics):
                        topic = topics[idx]
                        target = ExportTarget(
                            id=entity.id,
                            name=f"{entity_name} > {topic.title}",
                            type="forum_topic",
                            topic_id=topic.topic_id,
                            is_forum=True,
                            export_all_topics=False,
                        )
                        self.config.add_export_target(target)
                        added_topics.append(topic.title)
                    else:
                        rprint(f"[yellow]⚠️  Invalid topic number: {idx + 1}[/yellow]")

                if added_topics:
                    rprint(f"[green]✓ Added {len(added_topics)} topic(s):[/green]")
                    for topic_title in added_topics:
                        rprint(f"  • {topic_title}")
            except ValueError:
                rprint("[red]Invalid format. Please use comma-separated numbers.[/red]")

    async def _add_target_by_link(self):
        """
        Add export target by link, username, or ID.
        Validates availability like any other target.
        """
        clear_screen()
        rprint("[bold cyan]Add Target by Link/ID[/bold cyan]")
        rprint("=" * 40)

        rprint("\n[bold]Enter one of the following:[/bold]")
        rprint("• Channel/Chat link: https://t.me/channelname")
        rprint("• Channel/Chat username: @channelname or channelname")
        rprint("• Entity ID: 123456789")
        rprint("• Leave blank to cancel")

        link_input = Prompt.ask("\nTarget link or ID").strip()

        if not link_input:
            return

        try:
            rprint("\n[cyan]Resolving target...[/cyan]")

            # Use robust link parser first
            from src.utils import LinkParser

            parsed = LinkParser.parse(link_input)

            resolve_input = link_input
            if parsed and parsed["peer"]:
                resolve_input = parsed["peer"]
                rprint(f"[dim]Detected peer from link: {resolve_input}[/dim]")

            # Attempt to resolve the entity
            entity = await self.telegram_manager.resolve_entity(resolve_input)

            if not entity:
                rprint(
                    "[red]✗ Target not found or not accessible.[/red]\n"
                    "[yellow]Possible reasons:[/yellow]\n"
                    "• Channel/chat is private and you don't have access\n"
                    "• Username doesn't exist\n"
                    "• Invalid ID format\n"
                    "• You're not a member of the chat"
                )
                input("\nPress Enter to continue...")
                return

            # Create export target from resolved entity
            entity_name = get_entity_display_name(entity)
            entity_id = entity.id

            # Check if already added
            if any(t.id == entity_id for t in self.config.export_targets):
                rprint(
                    f"[yellow]⚠ Target '{entity_name}' (ID: {entity_id}) is already added.[/yellow]"
                )
                input("\nPress Enter to continue...")
                return

            # Different handling based on entity type
            entity_type = self._get_entity_type(entity)

            if entity_type == "forum":
                # Handle forum selection (show topics)
                await self._handle_forum_target_selection(entity, entity_name)
            elif entity_type in ["channel", "chat", "user"]:
                # Handle channel/group/user with message range selection
                await self._handle_regular_target_selection(
                    entity, entity_name, entity_type
                )
            else:
                rprint(f"[yellow]Unknown entity type: {entity_type}[/yellow]")
                input("\nPress Enter to continue...")
                return

            rprint(f"\n[green]✓ Successfully added target: {entity_name}[/green]")
            input("\nPress Enter to continue...")

        except Exception as e:
            logger.error(f"Error adding target by link: {e}")
            rprint(
                f"[red]✗ Error adding target: {e}[/red]\n"
                "[yellow]Please check the link/ID and try again.[/yellow]"
            )
            input("\nPress Enter to continue...")

    def _get_entity_type(self, entity) -> str:
        """Determine export type for entity."""
        entity_type = type(entity).__name__

        # Map Telegram entity types to export types
        type_mapping = {
            "Channel": "channel",
            "Chat": "chat",
            "User": "user",
            "ChatForbidden": "chat",
            "ChannelForbidden": "channel",
        }

        base_type = type_mapping.get(entity_type, "unknown")

        # Check if it's a forum (Channel with forum=True)
        if base_type == "channel" and hasattr(entity, "forum") and entity.forum:
            return "forum"

        return base_type

    async def _show_message_id_preview(self, entity, entity_name: str) -> Optional[int]:
        """
        Fetch and display message ID information for an entity.
        Shows latest message ID, approximate total, and recent message previews.

        Args:
            entity: Telegram entity to preview
            entity_name: Display name of the entity

        Returns:
            Latest message ID if found, None otherwise
        """
        try:
            rprint(
                f"\n[cyan]📊 Fetching message information for '{entity_name}'...[/cyan]"
            )

            # Fetch latest 3 messages for preview
            messages = []
            async for msg in self.telegram_manager.client.iter_messages(
                entity, limit=3
            ):
                if msg:  # Skip empty messages
                    messages.append(msg)

            if not messages:
                rprint("[yellow]⚠️  No messages found in this chat[/yellow]")
                return None

            # Display message ID information
            latest = messages[0]
            # Convert UTC to local timezone
            if latest.date:
                if latest.date.tzinfo is None:
                    import datetime

                    latest_date_utc = latest.date.replace(tzinfo=datetime.timezone.utc)
                else:
                    latest_date_utc = latest.date
                local_date = latest_date_utc.astimezone()
                latest_date = local_date.strftime("%b %d, %H:%M")
            else:
                latest_date = "Unknown"

            rprint(f"\n[bold cyan]📊 Message ID Information:[/bold cyan]")
            rprint(f"Latest message ID: [cyan]{latest.id}[/cyan] ({latest_date})\n")

            # Display recent message previews
            rprint("[bold]Recent messages preview:[/bold]")
            # Reverse messages to show in chronological order (oldest -> newest)
            for i, msg in enumerate(reversed(messages)):
                # Get message text or media indicator
                if msg.text:
                    text = msg.text.replace("\n", " ")  # Remove newlines
                    if len(text) > 50:
                        text = text[:50] + "..."
                elif msg.media:
                    text = f"[{self._get_media_type(msg)}]"
                else:
                    text = "[empty message]"

                # Format message date (convert UTC to local)
                if msg.date:
                    import datetime

                    if msg.date.tzinfo is None:
                        msg_date_utc = msg.date.replace(tzinfo=datetime.timezone.utc)
                    else:
                        msg_date_utc = msg.date
                    local_msg_date = msg_date_utc.astimezone()
                    msg_date = local_msg_date.strftime("%b %d, %H:%M")
                else:
                    msg_date = "?"

                # Display with nice formatting
                connector = "├─" if i < len(messages) - 1 else "└─"
                rprint(
                    f'[green]{connector}[/green] ID [cyan]{msg.id}[/cyan] ({msg_date}): "{text}"'
                )

            rprint(
                f"\n[dim]💡 Tip: You can export all messages, last N messages, or start from specific ID[/dim]"
            )

            return latest.id

        except Exception as e:
            logger.debug(f"Failed to fetch message preview: {e}")
            rprint(f"[yellow]⚠️  Unable to fetch message info: {e}[/yellow]")
            rprint(
                "[dim]You can still enter a message ID manually if you know it[/dim]"
            )
            return None

    def _get_media_type(self, message) -> str:
        """Get human-readable media type from message."""
        if not message.media:
            return "No media"

        media_type = type(message.media).__name__
        type_mapping = {
            "MessageMediaPhoto": "Photo",
            "MessageMediaDocument": "Document",
            "MessageMediaVideo": "Video",
            "MessageMediaAudio": "Audio",
            "MessageMediaVoice": "Voice",
            "MessageMediaContact": "Contact",
            "MessageMediaLocation": "Location",
            "MessageMediaPoll": "Poll",
            "MessageMediaSticker": "Sticker",
            "MessageMediaGif": "GIF",
        }
        return type_mapping.get(media_type, "Media")

    def _show_selected_targets(self):
        """Display currently selected export targets."""
        clear_screen()

        if not self.config.export_targets:
            rprint("[yellow]No targets selected.[/yellow]")
            input("\nPress Enter to continue...")
            return

        table = Table(title="Selected Export Targets")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")

        for target in self.config.export_targets:
            table.add_row(str(target.id), target.name, target.type)

        self.console.print(table)

        # Options for selected targets
        rprint("\n[bold]Options:[/bold]")
        rprint("1. Clear all targets")
        rprint("2. Remove specific target")
        rprint("3. Continue")

        choice = Prompt.ask("Choose option", choices=["1", "2", "3"], default="3")

        if choice == "1":
            if Confirm.ask("Clear all selected targets?"):
                self.config.export_targets.clear()
                rprint("[green]All targets cleared.[/green]")
        elif choice == "2":
            self._remove_specific_target()

        if choice != "3":
            input("\nPress Enter to continue...")

    def _remove_specific_target(self):
        """Remove a specific target from selection."""
        if not self.config.export_targets:
            return

        rprint("\nEnter target ID to remove:")
        for i, target in enumerate(self.config.export_targets):
            rprint(f"{i + 1}. {target.name} (ID: {target.id})")

        try:
            max_target = len(self.config.export_targets)
            index = prompt_int("Target number", 1)
            if not (1 <= index <= max_target):
                rprint(f"[red]Please enter a number between 1 and {max_target}.[/red]")
                return
            removed = self.config.export_targets.pop(index - 1)
            rprint(f"[green]Removed target: {removed.name}[/green]")
        except (ValueError, IndexError):
            rprint("[red]Invalid target number.[/red]")

    async def _run_configuration(self):
        """Run configuration menu."""
        while True:
            clear_screen()
            rprint("[bold cyan]Export Configuration[/bold cyan]")
            rprint("=" * 40)

            # Display current settings
            self._show_current_config()

            # Configuration options
            rprint("\n[bold]Configuration Options:[/bold]")
            rprint("1. Configure media download settings")
            rprint("2. Set export path")
            rprint("3. Configure media processing settings")
            rprint("4. Return to main menu")

            choice = Prompt.ask(
                "Choose option", choices=["1", "2", "3", "4"], default="4"
            )

            if choice == "1":
                await self._configure_media_download_settings()
                input("\nPress Enter to continue...")
            elif choice == "2":
                new_path = Prompt.ask(
                    "Enter export path", default=str(self.config.export_path)
                )
                self.config.export_path = Path(new_path)
                rprint(f"[green]Export path set to: {self.config.export_path}[/green]")
                input("\nPress Enter to continue...")
            elif choice == "3":
                await self._configure_media_settings()
                input("\nPress Enter to continue...")
            elif choice == "4":
                break

    def _show_current_config(self):
        """Display current configuration."""
        table = Table(title="Current Configuration")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Export Path", str(self.config.export_path))
        table.add_row("Download Photos", "✓" if self.config.download_photos else "✗")
        table.add_row("Download Videos", "✓" if self.config.download_videos else "✗")
        table.add_row("Download Audio", "✓" if self.config.download_audio else "✗")
        table.add_row("Download Other", "✓" if self.config.download_other else "✗")
        table.add_row("Selected Targets", str(len(self.config.export_targets)))

        # Media processing settings
        video_proc = "✓" if getattr(self.config, "process_video", False) else "✗"
        audio_proc = "✓" if getattr(self.config, "process_audio", True) else "✗"
        image_proc = "✓" if getattr(self.config, "process_images", True) else "✗"
        table.add_row("Video Processing", video_proc)
        table.add_row("Audio Processing", audio_proc)
        table.add_row("Image Processing", image_proc)

        # Transcription settings
        transcription_enabled = "✓" if self.config.enable_transcription else "✗"
        table.add_row("Audio Transcription", transcription_enabled)
        if self.config.enable_transcription:
            # Show simplified transcription info (Whisper Large V3 only)
            table.add_row("  Model", "Faster-Whisper Large V3")
            lang = self.config.transcription.language or "auto"
            table.add_row("  Language", lang)
            device = self.config.transcription.device or "auto"
            table.add_row("  Device", device)
            # Show parallelism settings (v5.1.0)
            max_concurrent = getattr(self.config.transcription, "max_concurrent", 2)
            sorting = getattr(self.config.transcription, "sorting", "size_asc")
            table.add_row("  Parallelism", f"×{max_concurrent}")
            table.add_row("  Sorting", sorting)

        self.console.print(table)

    async def _configure_media_download_settings(self):
        """Configure individual media download settings."""
        while True:
            clear_screen()
            rprint("[bold cyan]Media Download Settings[/bold cyan]")
            rprint("=" * 40)
            rprint(
                "[dim]Configure which types of media to download during export[/dim]"
            )

            # Show current settings
            photos_status = "✓ Enabled" if self.config.download_photos else "✗ Disabled"
            videos_status = "✓ Enabled" if self.config.download_videos else "✗ Disabled"
            audio_status = "✓ Enabled" if self.config.download_audio else "✗ Disabled"
            other_status = "✓ Enabled" if self.config.download_other else "✗ Disabled"

            rprint("\n[bold]Current Settings:[/bold]")
            rprint(f"1. Photos: {photos_status}")
            rprint(f"2. Videos: {videos_status}")
            rprint(f"3. Audio: {audio_status}")
            rprint(f"4. Other (stickers, docs): {other_status}")

            # Extension filters display
            inc_str = (
                ", ".join(self.config.include_extensions)
                if self.config.include_extensions
                else "None"
            )
            exc_str = (
                ", ".join(self.config.exclude_extensions)
                if self.config.exclude_extensions
                else "None"
            )
            rprint(f"5. Included Extensions: [cyan]{inc_str}[/cyan]")
            rprint(f"6. Excluded Extensions: [cyan]{exc_str}[/cyan]")

            rprint("7. Enable all types")
            rprint("8. Disable all types")
            rprint("9. Return")

            choice = Prompt.ask(
                "Choose option",
                choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"],
                default="9",
            )

            if choice == "1":
                self.config.download_photos = not self.config.download_photos
                status = "enabled" if self.config.download_photos else "disabled"
                rprint(f"[green]Photo download {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "2":
                self.config.download_videos = not self.config.download_videos
                status = "enabled" if self.config.download_videos else "disabled"
                rprint(f"[green]Video download {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "3":
                self.config.download_audio = not self.config.download_audio
                status = "enabled" if self.config.download_audio else "disabled"
                rprint(f"[green]Audio download {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "4":
                self.config.download_other = not self.config.download_other
                status = "enabled" if self.config.download_other else "disabled"
                rprint(f"[green]Other media download {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "5":
                rprint(
                    "[dim]Enter extensions separated by comma (e.g. jpg, png, mp4)[/dim]"
                )
                rprint("[dim]Leave empty to clear list[/dim]")
                inp = Prompt.ask("Extensions to INCLUDE")
                if not inp.strip():
                    self.config.include_extensions = []
                else:
                    self.config.include_extensions = [
                        e.strip().lower().replace(".", "")
                        for e in inp.split(",")
                        if e.strip()
                    ]
                rprint(
                    f"[green]Updated include list: {self.config.include_extensions}[/green]"
                )
                input("Press Enter to continue...")
            elif choice == "6":
                rprint(
                    "[dim]Enter extensions separated by comma (e.g. zip, rar, exe)[/dim]"
                )
                rprint("[dim]Leave empty to clear list[/dim]")
                inp = Prompt.ask("Extensions to EXCLUDE")
                if not inp.strip():
                    self.config.exclude_extensions = []
                else:
                    self.config.exclude_extensions = [
                        e.strip().lower().replace(".", "")
                        for e in inp.split(",")
                        if e.strip()
                    ]
                rprint(
                    f"[green]Updated exclude list: {self.config.exclude_extensions}[/green]"
                )
                input("Press Enter to continue...")
            elif choice == "7":
                self.config.download_photos = True
                self.config.download_videos = True
                self.config.download_audio = True
                self.config.download_other = True
                rprint("[green]All media downloads enabled[/green]")
                input("Press Enter to continue...")
            elif choice == "8":
                self.config.download_photos = False
                self.config.download_videos = False
                self.config.download_audio = False
                self.config.download_other = False
                rprint("[green]All media downloads disabled[/green]")
                input("Press Enter to continue...")
            elif choice == "9":
                break

    async def _configure_media_settings(self):
        """Configure media-specific settings."""
        while True:
            clear_screen()
            rprint("[bold cyan]Media Processing Settings[/bold cyan]")
            rprint("=" * 40)

            # Show current settings
            video_status = "✓ Enabled" if self.config.process_video else "✗ Disabled"
            image_status = "✓ Enabled" if self.config.process_images else "✗ Disabled"
            audio_status = "✓ Enabled" if self.config.process_audio else "✗ Disabled"

            rprint("\n[bold]Current Settings:[/bold]")
            rprint(f"1. Video processing: {video_status}")
            rprint(f"2. Image processing: {image_status}")
            rprint(f"3. Audio processing: {audio_status}")
            rprint("4. Transcription settings")
            rprint("5. Return")

            choice = Prompt.ask(
                "Choose option", choices=["1", "2", "3", "4", "5"], default="5"
            )

            if choice == "1":
                self.config.process_video = not self.config.process_video
                status = "enabled" if self.config.process_video else "disabled"
                rprint(f"[green]Video processing {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "2":
                self.config.process_images = not self.config.process_images
                status = "enabled" if self.config.process_images else "disabled"
                rprint(f"[green]Image processing {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "3":
                self.config.process_audio = not self.config.process_audio
                status = "enabled" if self.config.process_audio else "disabled"
                rprint(f"[green]Audio processing {status}[/green]")
                input("Press Enter to continue...")
            elif choice == "4":
                await self._configure_transcription_settings()
            elif choice == "5":
                break

    async def _configure_transcription_settings(self):
        """Configure audio transcription settings."""
        while True:
            clear_screen()
            rprint("[bold cyan]Audio Transcription Settings[/bold cyan]")
            rprint("=" * 40)

            # Show current settings
            transcription_status = (
                "✓ Enabled" if self.config.enable_transcription else "✗ Disabled"
            )
            language = self.config.transcription_language or "Auto-detect"
            device = self.config.transcription_device
            compute_type = self.config.transcription_compute_type
            cache_status = (
                "✓ Enabled" if self.config.transcription_cache_enabled else "✗ Disabled"
            )
            # Parallelism settings (v5.1.0)
            max_concurrent = getattr(self.config.transcription, "max_concurrent", 2)
            sorting = getattr(self.config.transcription, "sorting", "size_asc")

            rprint("\n[bold]Current Settings:[/bold]")
            rprint(f"1. Toggle Transcription: {transcription_status}")
            rprint(f"2. Set Language: {language}")
            rprint(f"3. Set Device: {device}")
            rprint(f"4. Set Compute Type: {compute_type}")
            rprint(f"5. Toggle Cache: {cache_status}")
            rprint(f"6. Set Parallelism: ×{max_concurrent}")
            rprint(f"7. Set Sorting: {sorting}")
            rprint("8. Return to main menu")

            rprint("\n[dim]Note: Transcription converts voice messages to text.[/dim]")
            rprint(
                "[dim]Using Whisper Large V3 (multi-language, CPU/GPU support)[/dim]"
            )

            choice = Prompt.ask(
                "Choose option",
                choices=["1", "2", "3", "4", "5", "6", "7", "8"],
                default="8",
            )

            if choice == "1":
                # Toggle transcription on/off
                self.config.enable_transcription = not self.config.enable_transcription
                status = "enabled" if self.config.enable_transcription else "disabled"
                rprint(f"[green]Audio transcription {status}[/green]")
                input("Press Enter to continue...")

            elif choice == "2":
                # Set language
                clear_screen()
                rprint("[bold cyan]Transcription Language[/bold cyan]")
                rprint("=" * 40)
                rprint("\n[bold]Options:[/bold]")
                rprint("1. Auto-detect (recommended)")
                rprint("2. Russian (ru)")
                rprint("3. English (en)")
                rprint("4. Ukrainian (uk)")
                rprint("5. Custom language code")

                lang_choice = Prompt.ask(
                    "Select language", choices=["1", "2", "3", "4", "5"], default="1"
                )

                if lang_choice == "1":
                    self.config.transcription_language = None
                    rprint("[green]Language set to auto-detect[/green]")
                elif lang_choice == "2":
                    self.config.transcription_language = "ru"
                    rprint("[green]Language set to Russian[/green]")
                elif lang_choice == "3":
                    self.config.transcription_language = "en"
                    rprint("[green]Language set to English[/green]")
                elif lang_choice == "4":
                    self.config.transcription_language = "uk"
                    rprint("[green]Language set to Ukrainian[/green]")
                elif lang_choice == "5":
                    custom_lang = Prompt.ask(
                        "Enter language code (e.g., 'de', 'fr', 'es')"
                    ).strip()
                    if custom_lang:
                        self.config.transcription_language = custom_lang
                        rprint(f"[green]Language set to: {custom_lang}[/green]")
                input("Press Enter to continue...")

            elif choice == "3":
                # Select device
                clear_screen()
                rprint("[bold cyan]Transcription Device[/bold cyan]")
                rprint("=" * 40)
                rprint("\n[bold]Available Devices:[/bold]")
                rprint("1. cpu  - CPU processing (slower, works everywhere)")
                rprint("2. cuda - NVIDIA GPU (fastest, requires CUDA)")
                rprint("3. auto - Automatic selection")

                device_choice = Prompt.ask(
                    "Select device", choices=["1", "2", "3"], default="1"
                )

                device_map = {"1": "cpu", "2": "cuda", "3": "auto"}
                self.config.transcription_device = device_map[device_choice]
                rprint(
                    f"[green]Transcription device set to: {self.config.transcription_device}[/green]"
                )
                input("Press Enter to continue...")

            elif choice == "4":
                # Select compute type
                clear_screen()
                rprint("[bold cyan]Compute Type[/bold cyan]")
                rprint("=" * 40)
                rprint("\n[bold]Available Types:[/bold]")
                rprint("1. int8    - Fastest, lower quality (recommended for CPU)")
                rprint("2. float16 - Balanced (recommended for GPU)")
                rprint("3. float32 - Highest quality, slowest")

                compute_choice = Prompt.ask(
                    "Select compute type", choices=["1", "2", "3"], default="1"
                )

                compute_map = {"1": "int8", "2": "float16", "3": "float32"}
                self.config.transcription_compute_type = compute_map[compute_choice]
                rprint(
                    f"[green]Compute type set to: {self.config.transcription_compute_type}[/green]"
                )
                input("Press Enter to continue...")

            elif choice == "5":
                # Toggle cache
                self.config.transcription_cache_enabled = (
                    not self.config.transcription_cache_enabled
                )
                status = (
                    "enabled" if self.config.transcription_cache_enabled else "disabled"
                )
                rprint(f"[green]Transcription cache {status}[/green]")
                rprint(
                    "[dim]Cache stores transcription results to avoid re-processing.[/dim]"
                )
                input("Press Enter to continue...")

            elif choice == "6":
                # Set parallelism
                clear_screen()
                rprint("[bold cyan]Transcription Parallelism[/bold cyan]")
                rprint("=" * 40)
                rprint("\n[bold]Options:[/bold]")
                rprint("0. Auto (1 for GPU, CPU cores/4 for CPU)")
                rprint("1. Sequential (safest, recommended for GPU)")
                rprint("2. Low parallelism (×2, default)")
                rprint("3. Medium parallelism (×4)")
                rprint("4. High parallelism (×8, CPU only)")

                para_choice = Prompt.ask(
                    "Select parallelism", choices=["0", "1", "2", "3", "4"], default="2"
                )

                para_map = {"0": 0, "1": 1, "2": 2, "3": 4, "4": 8}
                self.config.transcription.max_concurrent = para_map[para_choice]
                rprint(
                    f"[green]Parallelism set to: ×{self.config.transcription.max_concurrent}[/green]"
                )
                input("Press Enter to continue...")

            elif choice == "7":
                # Set sorting
                clear_screen()
                rprint("[bold cyan]Transcription Sorting[/bold cyan]")
                rprint("=" * 40)
                rprint("\n[bold]Options:[/bold]")
                rprint("1. size_asc  - Smaller files first (faster feedback)")
                rprint("2. size_desc - Larger files first (LPT, better parallelism)")
                rprint("3. none      - Original order")

                sort_choice = Prompt.ask(
                    "Select sorting", choices=["1", "2", "3"], default="1"
                )

                sort_map = {"1": "size_asc", "2": "size_desc", "3": "none"}
                self.config.transcription.sorting = sort_map[sort_choice]
                rprint(
                    f"[green]Sorting set to: {self.config.transcription.sorting}[/green]"
                )
                input("Press Enter to continue...")

            elif choice == "8":
                break

    async def _configure_performance(self):
        """Configure performance settings."""
        while True:
            clear_screen()
            rprint("[bold cyan]Performance Configuration[/bold cyan]")
            rprint("=" * 40)

            # Show current settings
            rprint(
                f"\nCurrent Profile: [green]{self.config.performance_profile}[/green]"
            )
            takeout_status = "Enabled" if self.config.use_takeout else "Disabled"
            rprint(f"Takeout Mode: [green]{takeout_status}[/green]")

            # Sharding status
            sharding_status = (
                "Enabled" if self.config.enable_shard_fetch else "Disabled"
            )
            rprint(
                f"Sharding (Parallel Message Fetch): [green]{sharding_status}[/green]"
            )
            if self.config.enable_shard_fetch:
                rprint(f"  ├─ Shard Count: [green]{self.config.shard_count}[/green]")
                rprint(
                    f"  └─ Chunk Size: [green]{self.config.shard_chunk_size}[/green] messages/request"
                )

            rprint("\n[bold]Options:[/bold]")
            rprint("1. Change Performance Profile")
            rprint("2. Configure Sharding")
            rprint("3. Return")

            choice = Prompt.ask("Select option", choices=["1", "2", "3"], default="3")

            if choice == "1":
                # Show available profiles
                profiles = ["conservative", "balanced", "aggressive"]

                rprint("\n[bold]Performance Profiles:[/bold]")
                for i, profile in enumerate(profiles, 1):
                    rprint(f"{i}. {profile.title()}")

                p_choice = Prompt.ask(
                    "Select profile", choices=["1", "2", "3"], default="2"
                )
                profile_map = {"1": "conservative", "2": "balanced", "3": "aggressive"}
                selected_profile = cast(
                    Literal["conservative", "balanced", "aggressive", "custom"],
                    profile_map[p_choice],
                )
                self.config.update_performance_profile(selected_profile)
                rprint(f"[green]Performance profile set to: {selected_profile}[/green]")
                input("\nPress Enter to continue...")

            elif choice == "2":
                # Configure sharding
                enable_sharding = Confirm.ask(
                    "Enable parallel message fetching (sharding)?",
                    default=self.config.enable_shard_fetch,
                )
                self.config.enable_shard_fetch = enable_sharding

                if enable_sharding:
                    rprint(
                        "\n[bold yellow]ℹ️ Sharding accelerates message fetching for large chats (10k+ messages)[/bold yellow]"
                    )

                    shard_count = IntPrompt.ask(
                        "Number of parallel workers (shards)",
                        default=self.config.shard_count,
                        show_default=True,
                    )
                    self.config.shard_count = max(
                        1, min(shard_count, 32)
                    )  # Min 1, Max 32

                    chunk_size = IntPrompt.ask(
                        "Messages per request (chunk size)",
                        default=self.config.shard_chunk_size,
                        show_default=True,
                    )
                    # Takeout API limits
                    self.config.shard_chunk_size = max(100, min(chunk_size, 1000))

                rprint("[green]✓ Sharding configuration updated.[/green]")
                input("\nPress Enter to continue...")

            elif choice == "3":
                break


async def run_interactive_configuration(
    config: Config, telegram_manager: TelegramManager
) -> bool:
    """
    Run interactive configuration process.

    Args:
        config: Configuration object to modify
        telegram_manager: Telegram manager for entity access

    Returns:
        True if user wants to proceed with export, False otherwise
    """
    ui = InteractiveUI(config, telegram_manager)
    return await ui.run_interactive_mode()
