import re
import os
import logging
import concurrent.futures
import asyncio
from functools import partial
from pathlib import Path
from loguru import logger
import sys

def setup_logging(verbose: bool):
    """Configures logging using Loguru."""
    log_level = "DEBUG" if verbose else "INFO"
    logger.remove() # Remove default handler
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    logger.add(
        "exporter.log",
        level="DEBUG", # Log everything to file
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
    )
    logger.info("Logging initialized.")
    # Suppress excessive logging from libraries if needed
    logging.getLogger('telethon').setLevel(logging.WARNING if not verbose else logging.INFO)


def sanitize_filename(text: str, max_length: int = 40) -> str:
    """Sanitizes text to be used as part of a filename."""
    if not text:
        return "Untitled"
    # Remove invalid characters
    sanitized = re.sub(r'[\\/*?:"<>|]', "", text)
    # Replace multiple spaces/newlines with a single underscore
    sanitized = re.sub(r'\s+', '_', sanitized).strip('_')
    # Limit length
    sanitized = sanitized[:max_length]
    # Ensure it's not empty after sanitization
    return sanitized or "Sanitized"

def get_relative_path(target_path: Path, base_path: Path) -> str:
    """Calculates the relative path suitable for Markdown links."""
    try:
        # Use os.path.relpath for robust relative path calculation
        relative = os.path.relpath(target_path, base_path.parent) # Relative from the note's *directory*
        # Replace backslashes with forward slashes for Markdown compatibility
        return relative.replace(os.path.sep, '/')
    except ValueError:
        logger.warning(f"Could not determine relative path between {target_path} and {base_path}")
        # Fallback to absolute path or a placeholder if relative fails (e.g., different drives on Windows)
        return target_path.as_posix() # Use POSIX path for Markdown

def ensure_dir_exists(path: Path):
    """Creates a directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)

async def run_in_thread_pool(func, *args, **kwargs):
    """Run a CPU-bound function in a thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, partial(func, *args, **kwargs)
    )

def process_files_in_parallel(file_processor_func, files, max_workers=None):
    """Process multiple files in parallel using a thread pool.

    Args:
        file_processor_func: Function that processes a single file
        files: List of files to process
        max_workers: Maximum number of worker threads (None = auto)

    Returns:
        List of results from processing each file
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(file_processor_func, files))
    return results
