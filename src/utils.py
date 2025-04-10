import re
import os
import logging
import asyncio
from functools import partial
from pathlib import Path
from loguru import logger
import sys
from typing import List, Callable, Any, TypeVar, Coroutine, Optional, Union, Sequence
import urllib.parse

T = TypeVar('T')
R = TypeVar('R')

def setup_logging(verbose: bool):
    """Configures logging using Loguru."""
    log_level = "DEBUG" if verbose else "INFO"
    log_file_level = "DEBUG"

    logger.remove()

    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        colorize=True
    )

    try:
        log_file_path = Path("telegram_exporter.log").resolve()
        logger.add(
            log_file_path,
            level=log_file_level,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {process.id} | {thread.name: <10} | {name}:{function}:{line} - {message}",
            enqueue=True,
            backtrace=True,
            diagnose=verbose
        )
        logger.info(f"Logging initialized. Console Level: {log_level}. Log file: {log_file_path} (Level: {log_file_level})")
    except Exception as e:
         logger.error(f"Failed to configure file logging: {e}. Logging to console only.")

    logging.getLogger('telethon').setLevel(logging.WARNING if not verbose else logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO if not verbose else logging.DEBUG)

def sanitize_filename(text: str, max_length: int = 200, replacement: str = '_') -> str:
    """
    Sanitizes text to be used as part of a filename. Replaces invalid chars,
    controls length, and handles edge cases.
    Args:
        text: The input string.
        max_length: Maximum allowed length of the sanitized string.
        replacement: Character used to replace invalid sequences.
    Returns:
        A filesystem-safe string.
    """
    if not text:
        return "Untitled"

    text = re.sub(r'[\s]+', replacement, text)
    text = re.sub(r'[\\/*?:"<>|&!]', '', text)
    text = text.strip('.' + replacement)

    reserved_names = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if text.upper() in reserved_names:
        text = text + replacement + "file"

    if len(text) > max_length:
        cutoff = text[:max_length].rfind(replacement)
        if cutoff > max_length - 20:
             text = text[:cutoff]
        else:
            text = text[:max_length]
        text = text.strip(replacement)

    return text or "Sanitized"

def get_relative_path(target_path: Path, base_path: Path) -> Optional[str]:
    """
    Calculates the relative path from base_path to target_path suitable for Markdown links.

    Args:
        target_path: Absolute path to the target file (e.g., media file).
        base_path: Absolute path to the directory containing the source file (e.g., note file's directory).

    Returns:
        A relative path string (using forward slashes) or None if calculation fails.
    """
    try:
        target_abs = target_path.resolve()
        base_abs = base_path.resolve()

        if not base_abs.is_dir():
             logger.warning(f"Base path '{base_abs}' for relative calculation is not a directory. Using its parent.")
             base_abs = base_abs.parent

        relative = os.path.relpath(target_abs, base_abs)
        posix_relative = relative.replace(os.path.sep, '/')
        encoded_relative = '/'.join(urllib.parse.quote(part) for part in posix_relative.split('/'))
        return encoded_relative
    except ValueError as e:
        logger.warning(f"Could not determine relative path from '{base_path}' to '{target_path}' (possibly different drives?): {e}")
        return None
    except Exception as e:
        logger.error(f"Error calculating relative path from {base_path} to {target_path}: {e}")
        return None

def ensure_dir_exists(path: Path):
    """Creates a directory if it doesn't exist. Raises OSError on failure."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create directory {path}: {e}")
        raise

async def run_in_thread_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """Runs a synchronous function in the default asyncio event loop's thread pool."""
    loop = asyncio.get_running_loop()
    func_partial = partial(func, *args, **kwargs)
    return await loop.run_in_executor(
        None,
        func_partial
    )

async def process_items_parallel(
    processor_func: Union[Callable[[T], R], Callable[[T], Coroutine[Any, Any, R]]],
    items: List[T],
    max_concurrency: int,
    desc: str = "items"
) -> List[Union[R, BaseException]]:
    """
    Processes a list of items in parallel using asyncio.Semaphore and asyncio.gather.

    Args:
        processor_func: A sync or async function to process each item.
        items: The list of items to process.
        max_concurrency: Maximum number of items to process concurrently.
        desc: Description for logging purposes.

    Returns:
        A list containing the results for each item (or Exceptions on failure).
    """
    if not items:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)
    tasks = []

    async def process_with_semaphore(item: T) -> Union[R, BaseException]:
        async with semaphore:
            try:
                if asyncio.iscoroutinefunction(processor_func):
                    return await processor_func(item)
                else:
                    return await run_in_thread_pool(processor_func, item)
            except Exception as e:
                 logger.error(f"Error processing {desc} item '{str(item)[:50]}...': {e}", exc_info=True)
                 raise e

    for item in items:
        tasks.append(asyncio.create_task(process_with_semaphore(item)))

    logger.info(f"Processing {len(items)} {desc} with max concurrency {max_concurrency}...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"Finished processing {len(items)} {desc}.")
    return results