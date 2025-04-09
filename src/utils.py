import re
import os
import logging
import asyncio
from functools import partial
from pathlib import Path
from loguru import logger
import sys
from typing import List, Callable, Any, TypeVar, Coroutine, Optional, Union
import urllib.parse

# Define type variables for better typing hints
T = TypeVar('T')
R = TypeVar('R')

# --- Logging Setup ---

def setup_logging(verbose: bool):
    """Configures logging using Loguru."""
    log_level = "DEBUG" if verbose else "INFO"
    log_file_level = "DEBUG" # Always log debug to file

    logger.remove() # Remove default handler to avoid duplicate outputs

    # Console Handler
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        colorize=True # Enable console colors
    )

    # File Handler
    try:
        log_file_path = Path("telegram_exporter.log").resolve()
        logger.add(
            log_file_path,
            level=log_file_level,
            rotation="10 MB", # Rotate when file reaches 10 MB
            retention="7 days", # Keep logs for 7 days
            compression="zip", # Compress rotated files
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {process.id} | {thread.name: <10} | {name}:{function}:{line} - {message}",
            enqueue=True, # Make logging asynchronous, important for performance
            backtrace=True, # Log stack traces on exceptions
            diagnose=verbose # Add variable values to exception tracebacks if verbose
        )
        logger.info(f"Logging initialized. Console Level: {log_level}. Log file: {log_file_path} (Level: {log_file_level})")
    except Exception as e:
         logger.error(f"Failed to configure file logging: {e}. Logging to console only.")


    # Suppress excessive logging from libraries
    logging.getLogger('telethon').setLevel(logging.WARNING if not verbose else logging.INFO)
    # Suppress Pillow debug messages unless verbose
    logging.getLogger('PIL').setLevel(logging.INFO if not verbose else logging.DEBUG)


# --- Filesystem Utilities ---

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

    # 1. Normalize unicode characters (optional, can help with some edge cases)
    # text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

    # 2. Remove or replace potentially problematic characters
    # Characters invalid in Windows/Linux/MacOS filenames: <>:"/\|?*
    # Also replace control characters (0x00-0x1F) and characters like `..`
    # Replace sequences of whitespace with a single replacement character
    text = re.sub(r'[\s]+', replacement, text)
    # Remove invalid characters
    text = re.sub(r'[\\/*?:"<>|&!]', '', text)
     # Remove leading/trailing dots or replacement chars
    text = text.strip('.' + replacement)

    # 3. Handle reserved filenames (Windows) - rudimentary check
    reserved_names = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if text.upper() in reserved_names:
        text = text + replacement + "file"

    # 4. Limit length (operating on bytes can be safer, but complex)
    # Simple truncation for now:
    if len(text) > max_length:
        # Try to truncate respecting the last replacement char if possible
        cutoff = text[:max_length].rfind(replacement)
        if cutoff > max_length - 20: # Only cut at replacement if it's near the end
             text = text[:cutoff]
        else:
            text = text[:max_length]
        text = text.strip(replacement) # Clean up potential trailing replacement

    # 5. Ensure not empty after sanitization
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
        # Ensure paths are absolute and resolved
        target_abs = target_path.resolve()
        base_abs = base_path.resolve()

        # Check if base_path is actually a directory
        if not base_abs.is_dir():
             logger.warning(f"Base path '{base_abs}' for relative calculation is not a directory. Using its parent.")
             base_abs = base_abs.parent

        relative = os.path.relpath(target_abs, base_abs)
        # Replace backslashes with forward slashes for cross-platform/URL compatibility
        posix_relative = relative.replace(os.path.sep, '/')
        # URL-encode path components (safer for spaces, special chars in links)
        encoded_relative = '/'.join(urllib.parse.quote(part) for part in posix_relative.split('/'))
        return encoded_relative
    except ValueError as e:
        # Happens if paths are on different drives (Windows)
        logger.warning(f"Could not determine relative path from '{base_path}' to '{target_path}' (possibly different drives?): {e}")
        # Fallback: maybe return absolute POSIX path? Or just filename? Returning None signals error.
        # return target_path.name # Option: return filename only
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
        raise # Re-raise the error


# --- Async/Concurrency Helpers ---

async def run_in_thread_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """Runs a synchronous function in the default asyncio event loop's thread pool."""
    loop = asyncio.get_running_loop()
    # functools.partial keeps args/kwargs associated with the function
    func_partial = partial(func, *args, **kwargs)
    return await loop.run_in_executor(
        None, # Use default executor (usually ThreadPoolExecutor)
        func_partial
    )

# Note: Running in ProcessPoolExecutor from async code requires careful handling.
# The `main.py` setup with a global process_executor and using loop.run_in_executor
# is generally the way to go for CPU-bound tasks within an async application.
# Defining a separate `run_in_process_pool` here might be redundant or confusing.

async def process_items_parallel(
    processor_func: Union[Callable[[T], R], Callable[[T], Coroutine[Any, Any, R]]],
    items: List[T],
    max_concurrency: int,
    desc: str = "items"
) -> List[R]:
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

    async def process_with_semaphore(item: T) -> R:
        async with semaphore:
            try:
                if asyncio.iscoroutinefunction(processor_func):
                    return await processor_func(item)
                else:
                    # Run sync function in thread pool
                    return await run_in_thread_pool(processor_func, item)
            except Exception as e:
                 # Log error here or let gather handle exceptions
                 logger.error(f"Error processing {desc} item '{str(item)[:50]}...': {e}", exc_info=True)
                 raise # Re-raise to be caught by gather

    for item in items:
        tasks.append(asyncio.create_task(process_with_semaphore(item)))

    logger.info(f"Processing {len(items)} {desc} with max concurrency {max_concurrency}...")
    # return_exceptions=True ensures gather doesn't stop on the first error
    results = await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"Finished processing {len(items)} {desc}.")
    return results
