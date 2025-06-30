# src/utils.py

"""
Utility functions for Telegram-to-Obsidian exporter.
Includes logging setup, filename sanitization, async helpers, and Telegra.ph parsing.
"""

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
from bs4 import BeautifulSoup, NavigableString
from loguru import logger

# Минимизируем логи: только ошибки в консоль, всё остальное — в файл на уровне WARNING
logger.remove()  # Удалить все обработчики
logger.add("exporter.log", level="WARNING", rotation="1 MB")  # Только в файл, только важное


class TelethonFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        ignore_phrases = [
            "Server sent a very old message with ID",
            "Security error while unpacking a received message",
            "Server replied with a wrong session ID"
        ]
        return not any(phrase in msg for phrase in ignore_phrases)

# Применить фильтр ко всем логгерам Telethon
logging.getLogger("telethon").addFilter(TelethonFilter())


def clear_screen():
    """
    Clears the terminal screen (cross-platform).
    """
    os.system('cls' if os.name == 'nt' else 'clear')

T = TypeVar('T')
R = TypeVar('R')

def setup_logging(log_level: str = "INFO"):
    """
    Configures logging using Loguru.
    Sets up both console and file logging with custom formatting.
    """
    logger.remove()

    def formatter(record):
        level_icon = {
            "INFO": "ℹ️",
            "SUCCESS": "✅",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "❌",
        }.get(record["level"].name, "➡️")

        if record["level"].name == "DEBUG":
            return "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>\n"

        return f"{level_icon} <level>{record['message']}</level>\n"

    logger.add(
        sys.stderr,
        level=log_level.upper(),
        format=formatter,
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
            diagnose=True
        )
        logger.info(f"Logging initialized. Console level: {log_level.upper()}, File level: DEBUG")
    except Exception as e:
        logger.error(f"Failed to configure file logging: {e}", exc_info=True)

    logging.getLogger('telethon').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)

def sanitize_filename(text: str, max_length: int = 200, replacement: str = '') -> str:
    """
    Sanitizes a filename for safe use on all platforms.
    """
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
    """
    Returns a URL-encoded relative path from base_path to target_path.
    """
    try:
        target_abs = target_path.resolve()
        base_abs = base_path.resolve()
        if not base_abs.is_dir():
            base_abs = base_abs.parent
        relative = os.path.relpath(target_abs, base_abs)
        posix_relative = relative.replace(os.path.sep, '/')
        return '/'.join(urllib.parse.quote(part) for part in posix_relative.split('/'))
    except Exception as e:
        logger.warning(f"Failed to calculate relative path: {e}", exc_info=True)
        return None

def ensure_dir_exists(path: Path):
    """
    Ensures the given directory exists, creating it if necessary.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create directory {path}: {e}", exc_info=True)
        raise

async def run_in_thread_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """
    Runs a blocking function in a thread pool asynchronously.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

def find_telegram_post_links(text: str) -> List[str]:
    """
    Finds all Telegram post links in the given text.
    """
    if not text:
        return []
    pattern = r"(?:https?://)?t\.me/([\w_]+)/([0-9]+)"
    return [match.group(0) for match in re.finditer(pattern, text)]

def find_telegraph_links(text: str) -> List[str]:
    """
    Finds all Telegra.ph links in the given text.
    """
    if not text:
        return []
    pattern = r"https?://telegra\.ph/[\w\-]+(?:/[\w\-]+)*"
    return [match.group(0) for match in re.finditer(pattern, text)]

async def fetch_and_parse_telegraph_to_markdown(
    session: aiohttp.ClientSession,
    url: str,
    media_path: Path,
    media_processor,
    cache: Optional[dict] = None,
    entity_id: Optional[str] = None,
    telegraph_mapping: Optional[dict] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetches and parses a Telegra.ph article to Markdown, downloads images, and replaces links.
    """
    try:
        logger.debug(f"Fetching Telegra.ph article: {url}")
        async with session.get(url, timeout=30) as response:
            response.raise_for_status()
            html = await response.text()

        soup = BeautifulSoup(html, 'html.parser')
        article_content = soup.find('article')
        if not article_content:
            return None

        title = (article_content.find('h1').get_text(strip=True) if article_content.find('h1') else "Untitled Article")

        pub_date = None
        if time_tag := soup.find('time'):
            pub_date = time_tag.get('datetime', time_tag.get_text(strip=True))[:10]

        img_urls = [f'https://telegra.ph{img["src"]}' if (img_src := img.get("src", "")).startswith('/') else img_src
                    for img in article_content.find_all('img') if img.get("src")]

        tasks = [media_processor.download_external_image(session, img_url, media_path) for img_url in img_urls]
        results = await asyncio.gather(*tasks)
        image_map = {url: path for url, path in zip(img_urls, results) if path}

        markdown_lines = []
        for element in article_content.find_all(['p', 'h3', 'h4', 'blockquote', 'figure', 'ul', 'ol', 'pre', 'hr']):
            if element.name == 'p':
                parts = []
                for child in element.children:
                    if isinstance(child, NavigableString):
                        parts.append(str(child))
                    elif child.name == 'br':
                        parts.append('\n')
                    elif child.name in ('strong', 'b'):
                        parts.append(f"**{child.get_text(strip=True)}**")
                    elif child.name in ('em', 'i'):
                        parts.append(f"*{child.get_text(strip=True)}*")
                    elif child.name == 'a':
                        parts.append(f"[{child.get_text(strip=True)}]({child.get('href', '')})")
                    elif child.name == 'code':
                        parts.append(f"`{child.get_text(strip=True)}`")

                line = ' '.join(''.join(parts).split())
                if line:
                    markdown_lines.append(line)

            elif element.name == 'h3':
                markdown_lines.append(f"\n### {element.get_text(strip=True)}")
            elif element.name == 'h4':
                markdown_lines.append(f"\n#### {element.get_text(strip=True)}")
            elif element.name == 'blockquote':
                markdown_lines.append(f"> {element.get_text(strip=True)}")
            elif element.name == 'ul':
                markdown_lines.extend(f"* {li.get_text(strip=True)}" for li in element.find_all('li', recursive=False))
            elif element.name == 'ol':
                markdown_lines.extend(f"{i}. {li.get_text(strip=True)}" for i, li in enumerate(element.find_all('li', recursive=False), 1))
            elif element.name == 'pre':
                markdown_lines.append(f"```\n{element.get_text()}\n```")
            elif element.name == 'hr':
                markdown_lines.append("\n---\n")
            elif element.name == 'figure':
                if img := element.find('img'):
                    img_src = f"https://telegra.ph{img.get('src')}" if img.get('src', '').startswith('/') else img.get('src')
                    if local_path := image_map.get(img_src):
                        markdown_lines.append(f"![[{local_path.name}]]")
                        if figcaption := element.find('figcaption'):
                            markdown_lines.append(f"*{figcaption.get_text(strip=True)}*")

        raw_markdown = "\n\n".join(markdown_lines)
        content = raw_markdown

        if cache and entity_id:
            processed_messages = cache.get("entities", {}).get(entity_id, {}).get("processed_messages", {})
            url_to_data = {data["telegram_url"]: data for data in processed_messages.values() if data.get("telegram_url")}
            msg_id_to_data = {msg_id: data for msg_id, data in processed_messages.items()}

            def tg_replacer(match: re.Match) -> str:
                link_text, tg_url = match.groups()
                clean_url = tg_url.split('?')[0].rstrip('/')

                data = url_to_data.get(clean_url)
                if not data:
                    if msg_id_match := re.search(r"/(\d+)$", clean_url):
                        data = msg_id_to_data.get(msg_id_match.group(1))

                if data and (fname := data.get("filename")):
                    title_text = data.get("title", "").replace("\n", " ").strip()
                    display = title_text if title_text else link_text
                    logger.debug(f"Found link in Telegra.ph '{tg_url}' -> {fname}")
                    return f"[[{Path(fname).stem}|{display}]]"

                logger.warning(f"[Telegra.ph Parser] No local file found for link: '{tg_url}'")
                return match.group(0)

            tg_pattern = re.compile(r"\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)")
            content = tg_pattern.sub(tg_replacer, content)

        if telegraph_mapping:
            def telegraph_replacer(match: re.Match) -> str:
                link_text, telegraph_url = match.groups()
                telegraph_url = telegraph_url.rstrip('/')
                if note_stem := telegraph_mapping.get(telegraph_url):
                    logger.debug(f"Replacing telegra.ph link '{telegraph_url}' with local note [[{note_stem}]]")
                    return f"[[{note_stem}|{link_text}]]"
                return match.group(0)

            telegraph_pattern = re.compile(r"\[([^\]]+)\]\((https?://telegra\.ph/[^\)]+)\)")
            content = telegraph_pattern.sub(telegraph_replacer, content)

        return {"title": title, "content": content, "pub_date": pub_date}

    except Exception as e:
        logger.error(f"Error parsing Telegra.ph article {url}: {e}", exc_info=True)
        return None
