import re
import os
import logging
import concurrent.futures
import asyncio
from functools import partial, wraps
from pathlib import Path
from loguru import logger
import sys
from typing import List, Callable, Any, TypeVar, Coroutine

# Define type variables for better typing hints
T = TypeVar('T')
R = TypeVar('R')

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
    # Configure for concurrent environments
    logger.configure(extra={"thread_id": lambda: "Main"})
    # Suppress excessive logging from libraries if needed
    logging.getLogger('telethon').setLevel(logging.WARNING if not verbose else logging.INFO)


async def async_sanitize_filename(text: str, max_length: int = 40) -> str:
    """Асинхронная версия sanitize_filename"""
    return await run_in_thread_pool(sanitize_filename, text, max_length)


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


async def async_get_relative_path(target_path: Path, base_path: Path) -> str:
    """Асинхронная версия get_relative_path"""
    return await run_in_thread_pool(get_relative_path, target_path, base_path)


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


async def async_ensure_dir_exists(path: Path):
    """Асинхронная версия ensure_dir_exists."""
    await run_in_thread_pool(ensure_dir_exists, path)


def ensure_dir_exists(path: Path):
    """Creates a directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)


def to_async(func: Callable[..., T]) -> Callable[..., Coroutine[Any, Any, T]]:
    """Декоратор для преобразования синхронной функции в асинхронную."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await run_in_thread_pool(func, *args, **kwargs)
    return wrapper


async def run_in_thread_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a CPU-bound function in a thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, partial(func, *args, **kwargs)
    )


def run_in_process_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a CPU-bound function in a process pool for parallelism."""
    with concurrent.futures.ProcessPoolExecutor() as executor:
        return executor.submit(func, *args, **kwargs).result()


async def process_files_in_parallel_async(file_processor_func: Callable[[T], R],
                                         files: List[T],
                                         max_workers: int = 0,
                                         use_processes: bool = False) -> List[R]:
    """Asynchronously process multiple files in parallel.

    Args:
        file_processor_func: Function that processes a single file
        files: List of files to process
        max_workers: Maximum number of worker threads/processes (0 = auto)
        use_processes: If True, use ProcessPoolExecutor instead of ThreadPoolExecutor

    Returns:
        List of results from processing each file
    """
    # Determine optimal worker count
    if max_workers == 0:
        max_workers = min(32, (os.cpu_count() or 1) * 2)

    tasks = []
    executor_class = concurrent.futures.ProcessPoolExecutor if use_processes else concurrent.futures.ThreadPoolExecutor

    with executor_class(max_workers=max_workers) as executor:
        loop = asyncio.get_running_loop()
        for file in files:
            future = loop.run_in_executor(executor, file_processor_func, file)
            tasks.append(future)

    return await asyncio.gather(*tasks)


def process_files_in_parallel(file_processor_func: Callable[[T], R],
                             files: List[T],
                             max_workers: int = 0,
                             use_processes: bool = False) -> List[R]:
    """Process multiple files in parallel using a thread or process pool.

    Args:
        file_processor_func: Function that processes a single file
        files: List of files to process
        max_workers: Maximum number of worker threads/processes (0 = auto)
        use_processes: If True, use ProcessPoolExecutor instead of ThreadPoolExecutor

    Returns:
        List of results from processing each file
    """
    # Determine optimal worker count
    if max_workers == 0:
        max_workers = min(32, (os.cpu_count() or 1) * (4 if not use_processes else 1))

    executor_class = concurrent.futures.ProcessPoolExecutor if use_processes else concurrent.futures.ThreadPoolExecutor

    with executor_class(max_workers=max_workers) as executor:
        # Submit all tasks and gather futures
        futures = [executor.submit(file_processor_func, file) for file in files]
        # Process results as they complete (more efficient than using map)
        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                logger.exception(f"Error processing file: {e}")
                results.append(None)

    # Sort results back to the original order
    return results


def run_async(coroutine):
    """Helper function to run an async function in a synchronous context."""
    return asyncio.run(coroutine)
