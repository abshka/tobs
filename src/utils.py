# src/utils.py

import asyncio
import logging
import os
import re
import sys
import urllib.parse
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger

T = TypeVar('T')
R = TypeVar('R')

def setup_logging(verbose: bool):
    """Configures logging using Loguru."""
    log_level = "DEBUG" if verbose else "INFO"
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
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            enqueue=True,
            backtrace=True,
            diagnose=verbose
        )
        logger.info(f"Logging initialized. Console: {log_level}, File: {log_file_path}")
    except Exception as e:
        logger.error(f"Failed to configure file logging: {e}")

    logging.getLogger('telethon').setLevel(logging.WARNING if not verbose else logging.INFO)
    logging.getLogger('PIL').setLevel(logging.INFO)

def sanitize_filename(text: str, max_length: int = 200, replacement: str = '') -> str:
    """Sanitizes text to be used as part of a filename."""
    if not text:
        return "Untitled"
    text = re.sub(r'[\\/*?:"<>|&!]', replacement, text)
    text = text.strip('. ')
    reserved_names = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
                      "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
                      "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if text.upper() in reserved_names:
        text = f"{text}_file"
    if len(text) > max_length:
        cutoff = text[:max_length].rfind(' ')
        text = text[:cutoff] if cutoff != -1 else text[:max_length]
    return text or "Untitled"

def get_relative_path(target_path: Path, base_path: Path) -> Optional[str]:
    """Calculate relative path from base_path to target_path for Markdown links."""
    try:
        target_abs = target_path.resolve()
        base_abs = base_path.resolve()
        if not base_abs.is_dir():
            base_abs = base_abs.parent
        relative = os.path.relpath(target_abs, base_abs)
        posix_relative = relative.replace(os.path.sep, '/')
        return '/'.join(urllib.parse.quote(part) for part in posix_relative.split('/'))
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
    """Runs a synchronous function in the default thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

def find_telegram_post_links(text: str) -> List[str]:
    """Finds all links to Telegram posts in text."""
    if not text: return []
    pattern = r"(?:https?://)?t\.me/([\w_]+)/([0-9]+)"
    return [match.group(0) for match in re.finditer(pattern, text)]

def find_telegraph_links(text: str) -> List[str]:
    """Finds all links to Telegra.ph articles in text."""
    if not text: return []
    pattern = r"https?://telegra\.ph/[\w\-]+(?:/[\w\-]+)*"
    return [match.group(0) for match in re.finditer(pattern, text)]

async def fetch_and_parse_telegraph_to_markdown(
    session: aiohttp.ClientSession, url: str, media_path: Path, media_processor
) -> Optional[Dict[str, Any]]:
    """
    Asynchronously fetches and parses a Telegra.ph article into Markdown,
    including downloading all images.
    """
    try:
        logger.debug(f"Fetching Telegra.ph article: {url}")
        async with session.get(url, timeout=30) as response:
            response.raise_for_status()
            html = await response.text()

        soup = BeautifulSoup(html, 'html.parser')
        article_content = soup.find('article')
        if not article_content: return None

        title = (article_content.find('h1').get_text(strip=True) if article_content.find('h1') else "Untitled Article")

        img_urls = []
        for img in article_content.find_all('img'):
            if img_src := img.get('src'):
                img_urls.append(f'https://telegra.ph{img_src}' if img_src.startswith('/') else img_src)

        tasks = [media_processor.download_external_image(session, img_url, media_path) for img_url in img_urls]
        results = await asyncio.gather(*tasks)
        image_map = {url: path for url, path in zip(img_urls, results) if path}

        markdown_lines = []
        for element in article_content.find_all(['p', 'h3', 'h4', 'blockquote', 'figure', 'ul', 'ol', 'pre', 'hr']):
            if element.name == 'p':
                line = ''.join(
                    f"**{child.get_text(strip=True)}**" if child.name in ('strong', 'b') else
                    f"*{child.get_text(strip=True)}*" if child.name in ('em', 'i') else
                    f"[{child.get_text(strip=True)}]({child.get('href', '')})" if child.name == 'a' else
                    f"`{child.get_text(strip=True)}`" if child.name == 'code' else
                    str(child)
                    for child in element.children
                )
                if line.strip(): markdown_lines.append(line)
            elif element.name == 'h3': markdown_lines.append(f"### {element.get_text(strip=True)}")
            elif element.name == 'h4': markdown_lines.append(f"#### {element.get_text(strip=True)}")
            elif element.name == 'blockquote': markdown_lines.append(f"> {element.get_text(strip=True)}")
            elif element.name == 'ul':
                markdown_lines.extend(f"* {li.get_text(strip=True)}" for li in element.find_all('li', recursive=False))
            elif element.name == 'ol':
                markdown_lines.extend(f"{i}. {li.get_text(strip=True)}" for i, li in enumerate(element.find_all('li', recursive=False), 1))
            elif element.name == 'pre': markdown_lines.append(f"```\n{element.get_text()}\n```")
            elif element.name == 'hr': markdown_lines.append("---")
            elif element.name == 'figure':
                if img := element.find('img'):
                    img_src = f"https://telegra.ph{img.get('src')}" if img.get('src', '').startswith('/') else img.get('src')
                    if local_path := image_map.get(img_src):
                        markdown_lines.append(f"![[{local_path.name}]]")
                        if figcaption := element.find('figcaption'):
                            markdown_lines.append(f"*{figcaption.get_text(strip=True)}*")

        return {"title": title, "content": "\n\n".join(markdown_lines)}

    except Exception as e:
        logger.error(f"Error parsing Telegra.ph article {url}: {e}", exc_info=True)
        return None
