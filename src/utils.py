import asyncio
import logging
import os
import re
import sys
import urllib.parse
from functools import partial
from pathlib import Path
from typing import Any, Callable, List, Optional, TypeVar

from loguru import logger

T = TypeVar('T')
R = TypeVar('R')

def setup_logging(verbose: bool):
    """Configures logging using Loguru."""
    # Configure console logging
    log_level = "DEBUG" if verbose else "INFO"
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        colorize=True
    )

    # Configure file logging
    try:
        log_file_path = Path("telegram_exporter.log").resolve()
        logger.add(
            log_file_path,
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {process.id} | {thread.name: <10} | {name}:{function}:{line} - {message}",
            enqueue=True,
            backtrace=True,
            diagnose=verbose
        )
        logger.info(f"Logging initialized. Console: {log_level}, File: {log_file_path}")
    except Exception as e:
        logger.error(f"Failed to configure file logging: {e}")

    # Configure third-party loggers
    logging.getLogger('telethon').setLevel(logging.WARNING if not verbose else logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO if not verbose else logging.DEBUG)

def sanitize_filename(text: str, max_length: int = 200, replacement: str = '_') -> str:
    """Sanitizes text to be used as part of a filename."""
    if not text:
        return "Untitled"

    # Replace spaces and invalid characters
    text = re.sub(r'[\s]+', replacement, text)
    text = re.sub(r'[\\/*?:"<>|&!]', '', text)
    text = text.strip('.' + replacement)

    # Handle Windows reserved names
    reserved_names = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
                      "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
                      "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if text.upper() in reserved_names:
        text = f"{text}{replacement}file"

    # Truncate if too long
    if len(text) > max_length:
        cutoff = text[:max_length].rfind(replacement)
        if cutoff > max_length - 20:
            text = text[:cutoff]
        else:
            text = text[:max_length]
        text = text.strip(replacement)

    return text or "Sanitized"

def get_relative_path(target_path: Path, base_path: Path) -> Optional[str]:
    """Calculate relative path from base_path to target_path for Markdown links."""
    try:
        target_abs = target_path.resolve()
        base_abs = base_path.resolve()

        # Ensure base is a directory
        if not base_abs.is_dir():
            base_abs = base_abs.parent

        # Calculate relative path
        relative = os.path.relpath(target_abs, base_abs)

        # Convert to forward slashes and URL-encode path components
        posix_relative = relative.replace(os.path.sep, '/')
        encoded_relative = '/'.join(urllib.parse.quote(part) for part in posix_relative.split('/'))

        return encoded_relative
    except Exception as e:
        logger.warning(f"Failed to calculate relative path: {e}")
        return None

def ensure_dir_exists(path: Path):
    """Creates a directory if it doesn't exist."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create directory {path}: {e}")
        raise

async def run_in_thread_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """Runs a synchronous function in the thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

async def process_items_parallel(
    processor_func: Callable,
    items: List[T],
    max_concurrency: int,
    desc: str = "items"
) -> List[Any]:
    """Process items in parallel with concurrency limit."""
    if not items:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)
    results = []

    async def process_one(item: T) -> Any:
        async with semaphore:
            try:
                # Handle both coroutine and regular functions
                if asyncio.iscoroutinefunction(processor_func):
                    return await processor_func(item)
                else:
                    return await run_in_thread_pool(processor_func, item)
            except Exception as e:
                logger.error(f"Error processing {desc} item: {e}")
                return e  # Return exception object

    # Create all tasks with semaphore control
    tasks = [asyncio.create_task(process_one(item)) for item in items]

    # Wait for all tasks to complete
    logger.info(f"Processing {len(items)} {desc} (max concurrency: {max_concurrency})")
    for task in tasks:
        results.append(await task)
    logger.info(f"Finished processing {len(items)} {desc}")

    return results
