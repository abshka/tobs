"""
Interactive UI module for TOBS.
Handles user interaction, menu systems, and interactive configuration.
Provides modular user interface functionality.
"""

import asyncio
from pathlib import Path
from typing import List, Literal, cast

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
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
                entity_type = type(entity).__name__
                entity_id = getattr(entity, "id", "Unknown")
                entity_title = getattr(
                    entity, "title", getattr(entity, "username", str(entity_id))
                )

                table.add_row(str(i), entity_title, entity_type, str(entity_id))
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
                                target = ExportTarget(
                                    id=entity.id,
                                    name=get_entity_display_name(entity),
                                    type=self._get_entity_type(entity),
                                )
                                # Check if already added and add using proper method
                                if not any(
                                    t.id == target.id
                                    for t in self.config.export_targets
                                ):
                                    self.config.add_export_target(target)
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

            # Attempt to resolve the entity
            entity = await self.telegram_manager.resolve_entity(link_input)

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

            # Create and add target
            target = ExportTarget(
                id=entity_id,
                name=entity_name,
                type=self._get_entity_type(entity),
            )

            self.config.add_export_target(target)

            rprint(
                f"\n[green]✓ Successfully added target:[/green]\n"
                f"  Name: {entity_name}\n"
                f"  ID: {entity_id}\n"
                f"  Type: {target.type}"
            )
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

        return type_mapping.get(entity_type, "unknown")

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
            rprint("1. Toggle media download")
            rprint("2. Set export path")
            rprint("3. Configure media settings")
            rprint("4. Return to main menu")

            choice = Prompt.ask(
                "Choose option", choices=["1", "2", "3", "4"], default="4"
            )

            if choice == "1":
                self.config.media_download = not self.config.media_download
                status = "enabled" if self.config.media_download else "disabled"
                rprint(f"[green]Media download {status}.[/green]")
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
        table.add_row("Media Download", "✓" if self.config.media_download else "✗")
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

        self.console.print(table)

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

            rprint("\n[bold]Current Settings:[/bold]")
            rprint(f"1. Toggle Transcription: {transcription_status}")
            rprint(f"2. Set Language: {language}")
            rprint(f"3. Set Device: {device}")
            rprint(f"4. Set Compute Type: {compute_type}")
            rprint(f"5. Toggle Cache: {cache_status}")
            rprint("6. Return to main menu")

            rprint("\n[dim]Note: Transcription converts voice messages to text.[/dim]")
            rprint(
                "[dim]Using Whisper Large V3 (multi-language, CPU/GPU support)[/dim]"
            )

            choice = Prompt.ask(
                "Choose option",
                choices=["1", "2", "3", "4", "5", "6"],
                default="6",
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
                break

    async def _configure_performance(self):
        """Configure performance settings."""
        clear_screen()
        rprint("[bold cyan]Performance Configuration[/bold cyan]")
        rprint("=" * 40)

        # Show available profiles
        profiles = ["conservative", "balanced", "aggressive"]

        rprint("\n[bold]Performance Profiles:[/bold]")
        for i, profile in enumerate(profiles, 1):
            rprint(f"{i}. {profile.title()}")

        choice = Prompt.ask("Select profile", choices=["1", "2", "3"], default="3")

        profile_map = {"1": "conservative", "2": "balanced", "3": "aggressive"}
        selected_profile = cast(
            Literal["conservative", "balanced", "aggressive", "custom"],
            profile_map[choice],
        )

        # Create performance profile (simplified)
        self.config.performance_profile = selected_profile

        rprint(f"[green]Performance profile set to: {selected_profile}[/green]")
        input("\nPress Enter to continue...")


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
