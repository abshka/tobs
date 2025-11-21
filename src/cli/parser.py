"""
CLI argument parsing module for TOBS.
Handles command-line interface, argument validation, and help text.
Provides modular command-line parsing functionality.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from ..config import Config, ExportTarget
from ..utils import logger


class TOBSArgumentParser:
    """
    Command-line argument parser for TOBS.
    Provides structured argument parsing with validation and help.
    """

    def __init__(self):
        self.parser = self._create_parser()

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create and configure the argument parser."""
        parser = argparse.ArgumentParser(
            prog="tobs",
            description="TOBS - Telegram Exporter to Markdown",
            epilog="""
Examples:
  tobs --interactive                    # Interactive mode
  tobs --target-id 123456 --export-path ./exports
  tobs --channel @mychannel --media-download
  tobs --forum-id 789012 --all-topics

For more information, visit: https://github.com/your-repo/tobs
            """,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        # Mode selection
        mode_group = parser.add_mutually_exclusive_group()
        mode_group.add_argument(
            "--interactive",
            "-i",
            action="store_true",
            help="Run in interactive mode with guided setup",
        )
        mode_group.add_argument(
            "--batch",
            action="store_true",
            help="Run in batch mode with command-line arguments only",
        )

        # Target selection
        target_group = parser.add_argument_group("Target Selection")
        target_group.add_argument(
            "--target-id", type=int, help="Telegram entity ID to export"
        )
        target_group.add_argument(
            "--channel", type=str, help="Channel username (e.g., @mychannel)"
        )
        target_group.add_argument("--chat-id", type=int, help="Chat ID to export")
        target_group.add_argument("--forum-id", type=int, help="Forum ID to export")
        target_group.add_argument(
            "--user-id", type=int, help="User ID for direct messages"
        )

        # Export options
        export_group = parser.add_argument_group("Export Options")
        export_group.add_argument(
            "--export-path",
            type=Path,
            default=None,
            help="Directory to save exported files (default: from .env or ./exports)",
        )
        export_group.add_argument(
            "--media-download", action="store_true", help="Download media files"
        )
        export_group.add_argument(
            "--no-media", action="store_true", help="Skip media download"
        )
        export_group.add_argument(
            "--all-topics", action="store_true", help="Export all topics from a forum"
        )
        export_group.add_argument(
            "--topic-id", type=int, help="Specific forum topic ID to export"
        )

        # Message filtering
        filter_group = parser.add_argument_group("Message Filtering")
        filter_group.add_argument(
            "--limit", type=int, help="Maximum number of messages to export"
        )
        filter_group.add_argument(
            "--offset-id", type=int, help="Start from specific message ID"
        )
        filter_group.add_argument(
            "--min-id", type=int, help="Minimum message ID to include"
        )
        filter_group.add_argument(
            "--max-id", type=int, help="Maximum message ID to include"
        )
        filter_group.add_argument(
            "--reverse",
            action="store_true",
            help="Export messages in reverse chronological order",
        )

        # Performance settings
        perf_group = parser.add_argument_group("Performance")
        perf_group.add_argument(
            "--performance",
            choices=["conservative", "balanced", "aggressive"],
            default="balanced",
            help="Performance profile (default: balanced)",
        )
        perf_group.add_argument(
            "--max-workers",
            type=int,
            default=4,
            help="Maximum worker threads (default: 4)",
        )
        perf_group.add_argument(
            "--chunk-size",
            type=int,
            default=100,
            help="Message processing chunk size (default: 100)",
        )
        perf_group.add_argument(
            "--memory-limit", type=str, help="Memory limit (e.g., 2GB, 512MB)"
        )

        # Media processing
        media_group = parser.add_argument_group("Media Processing")
        media_group.add_argument(
            "--compress-video", action="store_true", help="Compress video files"
        )
        media_group.add_argument(
            "--compress-images", action="store_true", help="Compress image files"
        )
        media_group.add_argument(
            "--max-video-size",
            type=str,
            help="Maximum video resolution (e.g., 1920x1080)",
        )
        media_group.add_argument(
            "--video-quality",
            type=int,
            choices=range(1, 101),
            metavar="1-100",
            help="Video quality percentage (1-100)",
        )

        # Authentication
        auth_group = parser.add_argument_group("Authentication")
        auth_group.add_argument("--api-id", type=int, help="Telegram API ID")
        auth_group.add_argument("--api-hash", type=str, help="Telegram API hash")
        auth_group.add_argument("--session-file", type=Path, help="Session file path")

        # Output and logging
        output_group = parser.add_argument_group("Output & Logging")
        output_group.add_argument(
            "--verbose",
            "-v",
            action="count",
            default=0,
            help="Increase verbosity (use multiple times for more detail)",
        )
        output_group.add_argument(
            "--quiet", "-q", action="store_true", help="Suppress non-error output"
        )
        output_group.add_argument("--log-file", type=Path, help="Log file path")
        output_group.add_argument(
            "--no-color", action="store_true", help="Disable colored output"
        )

        # Additional options
        options_group = parser.add_argument_group("Options")
        options_group.add_argument(
            "--config-file", type=Path, help="Configuration file path"
        )
        options_group.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be exported without actually doing it",
        )
        options_group.add_argument(
            "--skip-existing", action="store_true", help="Skip files that already exist"
        )
        options_group.add_argument(
            "--resume", action="store_true", help="Resume interrupted export"
        )

        # Development options
        dev_group = parser.add_argument_group("Development")
        dev_group.add_argument("--debug", action="store_true", help="Enable debug mode")
        dev_group.add_argument(
            "--profile", action="store_true", help="Enable performance profiling"
        )
        dev_group.add_argument(
            "--benchmark", action="store_true", help="Run performance benchmarks"
        )

        return parser

    def parse_args(self, args: Optional[List[str]] = None) -> argparse.Namespace:
        """
        Parse command-line arguments.

        Args:
            args: Optional list of arguments to parse (defaults to sys.argv)

        Returns:
            Parsed arguments namespace
        """
        parsed = self.parser.parse_args(args)

        # Validate arguments
        self._validate_args(parsed)

        return parsed

    def _validate_args(self, args: argparse.Namespace):
        """Validate parsed arguments for consistency and requirements."""

        # Check for conflicting media options
        if args.media_download and args.no_media:
            self.parser.error("--media-download and --no-media are mutually exclusive")

        # Validate target specification
        target_specified = any(
            [args.target_id, args.channel, args.chat_id, args.forum_id, args.user_id]
        )

        if args.batch and not target_specified:
            self.parser.error("Batch mode requires at least one target specification")

        # Validate forum-specific options
        if args.all_topics and not args.forum_id:
            self.parser.error("--all-topics requires --forum-id")

        if args.topic_id and not args.forum_id:
            self.parser.error("--topic-id requires --forum-id")

        # Validate message filtering
        if args.min_id and args.max_id and args.min_id >= args.max_id:
            self.parser.error("--min-id must be less than --max-id")

        # Validate performance settings
        if args.max_workers and (args.max_workers < 1 or args.max_workers > 32):
            self.parser.error("--max-workers must be between 1 and 32")

        if args.chunk_size and (args.chunk_size < 1 or args.chunk_size > 1000):
            self.parser.error("--chunk-size must be between 1 and 1000")

        # Validate memory limit format
        if args.memory_limit:
            if not self._validate_memory_limit(args.memory_limit):
                self.parser.error("Invalid memory limit format (use: 2GB, 512MB, etc.)")

        # Validate video resolution format
        if args.max_video_size:
            if not self._validate_video_resolution(args.max_video_size):
                self.parser.error("Invalid video resolution format (use: 1920x1080)")

    def _validate_memory_limit(self, limit: str) -> bool:
        """Validate memory limit format."""
        import re

        pattern = r"^\d+(?:\.\d+)?[KMGT]?B$"
        return bool(re.match(pattern, limit.upper()))

    def _validate_video_resolution(self, resolution: str) -> bool:
        """Validate video resolution format."""
        import re

        pattern = r"^\d+x\d+$"
        return bool(re.match(pattern, resolution))

    def create_config_from_args(self, args: argparse.Namespace) -> Config:
        """
        Create a Config object from parsed arguments.

        Args:
            args: Parsed command-line arguments

        Returns:
            Configured Config object
        """
        # Check if API credentials are provided via command line
        api_id = getattr(args, "api_id", None)
        api_hash = getattr(args, "api_hash", None)

        # If credentials are provided via CLI, create config directly
        if api_id and api_hash:
            config = Config(api_id=api_id, api_hash=api_hash)
        else:
            # Try to load from environment variables
            try:
                config = Config.from_env()
            except Exception as e:
                # If both CLI and env fail, provide helpful error message
                raise ValueError(
                    "Missing required Telegram API credentials. Please either:\n"
                    "1. Create a .env file with API_ID and API_HASH, or\n"
                    "2. Use --api-id and --api-hash command line arguments\n"
                    f"Error: {e}"
                )

        # Update with command line arguments if provided
        if hasattr(args, "api_id") and args.api_id:
            config.api_id = args.api_id
        if hasattr(args, "api_hash") and args.api_hash:
            config.api_hash = args.api_hash
        if hasattr(args, "session_file") and args.session_file:
            config.session_name = str(args.session_file)

        # Set basic options - only override if explicitly provided
        if args.export_path is not None:
            config.export_path = args.export_path
        # config.media_download = args.media_download and not args.no_media
        # config.interactive_mode = args.interactive

        # Add export targets
        if args.target_id:
            config.export_targets.append(
                ExportTarget(id=args.target_id, name=f"Entity_{args.target_id}")
            )

        if args.channel:
            # Remove @ if present
            channel_name = args.channel.lstrip("@")
            config.export_targets.append(
                ExportTarget(id=channel_name, name=channel_name, type="channel")
            )

        if args.chat_id:
            config.export_targets.append(
                ExportTarget(id=args.chat_id, name=f"Chat_{args.chat_id}", type="chat")
            )

        if args.forum_id:
            target = ExportTarget(
                id=args.forum_id, name=f"Forum_{args.forum_id}", type="forum"
            )
            if args.topic_id:
                target.topic_id = args.topic_id
            # elif args.all_topics:
            #     target.all_topics = True
            config.export_targets.append(target)

        if args.user_id:
            config.export_targets.append(
                ExportTarget(id=args.user_id, name=f"User_{args.user_id}", type="user")
            )

        # Performance settings
        config.performance_profile = args.performance

        # Update performance settings if provided
        if hasattr(args, "max_workers") and args.max_workers:
            config.performance.workers = args.max_workers
        if hasattr(args, "chunk_size") and args.chunk_size:
            # config.performance.chunk_size = args.chunk_size
            pass

        # Additional options
        # config.dry_run = getattr(args, 'dry_run', False)
        # config.skip_existing = getattr(args, 'skip_existing', False)
        # config.resume = getattr(args, 'resume', False)
        # config.debug = getattr(args, 'debug', False)

        return config


def parse_command_line_args(args: Optional[List[str]] = None) -> Config:
    """
    Parse command-line arguments and return a Config object.

    Args:
        args: Optional list of arguments to parse

    Returns:
        Configured Config object

    Raises:
        SystemExit: If argument parsing fails
    """
    parser = TOBSArgumentParser()

    try:
        parsed_args = parser.parse_args(args)
        config = parser.create_config_from_args(parsed_args)

        logger.debug(
            f"Parsed command-line arguments: {len(config.export_targets)} targets"
        )

        return config

    except Exception as e:
        logger.error(f"Failed to parse command-line arguments: {e}")
        sys.exit(1)


def print_usage_examples():
    """Print helpful usage examples."""
    examples = """
TOBS Usage Examples:

Basic Usage:
  tobs --interactive                           # Start interactive mode
  tobs --target-id 123456789                   # Export specific entity by ID
  tobs --channel @mychannel                    # Export public channel

Export Options:
  tobs --target-id 123 --export-path ./my_exports --media-download
  tobs --forum-id 456 --all-topics            # Export entire forum
  tobs --forum-id 456 --topic-id 789          # Export specific forum topic

Performance Tuning:
  tobs --target-id 123 --performance aggressive --max-workers 8
  tobs --target-id 123 --memory-limit 4GB --chunk-size 200

Media Processing:
  tobs --target-id 123 --compress-video --max-video-size 1280x720
  tobs --target-id 123 --no-media             # Skip media download

Additional Features:
  tobs --target-id 123 --limit 1000 --reverse # Export last 1000 messages
  tobs --target-id 123 --dry-run              # Preview what would be exported
  tobs --target-id 123 --resume               # Resume interrupted export

For more help: tobs --help
    """
    print(examples)


if __name__ == "__main__":
    # Allow running as standalone module for testing
    parser = TOBSArgumentParser()
    args = parser.parse_args()
    config = parser.create_config_from_args(args)
    print(f"Config created with {len(config.export_targets)} targets")
