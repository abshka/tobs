"""
CLI module for TOBS - Telegram Exporter.
Contains command-line interface components and argument parsing.

This module provides:
- Command-line argument parsing and validation
- Help text and usage examples
- Configuration creation from CLI arguments
- Support for batch and interactive modes
"""

from .parser import TOBSArgumentParser, parse_command_line_args, print_usage_examples

__all__ = ["TOBSArgumentParser", "parse_command_line_args", "print_usage_examples"]
