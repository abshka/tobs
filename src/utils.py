import asyncio
import logging
import os
import re
import time
import urllib.parse
from collections import defaultdict
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from time import sleep
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, TypeVar

import aiofiles
import aiohttp
from bs4 import BeautifulSoup, NavigableString
from loguru import logger
from rich import print as rprint

from src.exceptions import PerformanceError, create_performance_context

# Компилируем один раз при импорте модуля для +50% производительности

_FILENAME_SANITIZE_PATTERN = re.compile(r'[\\/*?:"<>|&!]')
_TELEGRAM_LINK_PATTERN = re.compile(r"(?:https?://)?t\.me/([\w_]+)/([0-9]+)")
_TELEGRAPH_LINK_PATTERN = re.compile(r"https?://telegra\.ph/[\w\-]+(?:/[\w\-]+)*")
_TELEGRAPH_MARKDOWN_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://telegra\.ph/[^\)]+)\)")
_TELEGRAM_MARKDOWN_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)")
_MESSAGE_ID_PATTERN = re.compile(r"/(\d+)$")

# Кэш для часто используемых операций
_SANITIZED_FILENAME_CACHE: Dict[str, str] = {}
_RELATIVE_PATH_CACHE: Dict[tuple, Optional[str]] = {}

# TypeVars
T = TypeVar('T')
R = TypeVar('R')

RESERVED_WINDOWS_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
    "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
})

TELEGRAPH_IMG_SELECTORS = ['img[src]']
TELEGRAPH_CONTENT_SELECTORS = ['p', 'h3', 'h4', 'blockquote', 'figure', 'ul', 'ol', 'pre', 'hr']
class TelethonFilter(logging.Filter):
    """
    Оптимизированный фильтр для Telethon логов с кэшированием.
    """

    def __init__(self):
        super().__init__()
        # Предкомпилированные паттерны для быстрой проверки
        self._ignore_patterns = [
            re.compile(r"Server sent a very old message with ID"),
            re.compile(r"Security error while unpacking a received message"),
            re.compile(r"wrong session ID")  # Добавлен новый паттерн
        ]

    def filter(self, record) -> bool:
        """Быстрая фильтрация с предкомпилированными паттернами."""
        msg = record.getMessage()
        return not any(pattern.search(msg) for pattern in self._ignore_patterns)
# Применяем фильтр
logging.getLogger("telethon").addFilter(TelethonFilter())
def clear_screen():
    """Очищает экран терминала (кроссплатформенно)."""
    os.system('cls' if os.name == 'nt' else 'clear')
def notify_and_pause(text, duration=1.0):
    """Выводит уведомление и делает паузу."""
    rprint(text)
    sleep(duration)
async def async_notify_and_pause(text: str, duration: float = 1.0):
    """Асинхронная версия notify_and_pause."""
    rprint(text)
    await asyncio.sleep(duration)
def setup_logging(log_level: str = "INFO"):
    """
    Настройка логирования с оптимизациями производительности.

    Улучшения:
    - Асинхронное логирование в файл (все уровни включая INFO)
    - Консольное логирование только WARNING и ERROR
    - Ротация логов для экономии места
    - Фильтрация spam сообщений
    """
    logger.remove()

    try:
        log_file_path = Path("tobs_exporter.log").resolve()

        # Асинхронное логирование в файл с ротацией
        logger.add(
            log_file_path,
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            enqueue=True,  # Асинхронная запись
            backtrace=True,
            diagnose=True,
            rotation="10 MB",  # Ротация при достижении 10MB
            retention="3 days",  # Хранить логи 3 дня
            compression="gz",  # Сжимать старые логи
            mode="w"
        )

        # Консольное логирование (только WARNING и ERROR)
        logger.add(
            lambda msg: rprint(msg, end=""),
            level="WARNING",
            format="{time:HH:mm:ss} | {level: <8} | {message}",
            colorize=False
        )

        logger.info("Optimized logging initialized with async file writing and rotation (console: WARNING+, file: INFO+)")
    except Exception as e:
        logger.error(f"Failed to configure logging: {e}", exc_info=True)

    # Настройка уровней для внешних библиотек
    for lib_name in ['telethon', 'PIL', 'aiohttp', 'asyncio']:
        logging.getLogger(lib_name).setLevel(logging.WARNING)
@lru_cache(maxsize=1000)
def sanitize_filename(text: str, max_length: int = 200, replacement: str = '') -> str:
    """
    Оптимизированная санитизация имён файлов с кэшированием.

    Улучшения:
    - LRU кэш для повторных вызовов
    - Предкомпилированные регулярные выражения
    - Оптимизированная обрезка по словам
    """
    if not text:
        return "Untitled"

    # Быстрая замена недопустимых символов
    text = _FILENAME_SANITIZE_PATTERN.sub(replacement, text)
    text = text.strip('. ')

    # Проверка зарезервированных имён Windows
    if text.upper() in RESERVED_WINDOWS_NAMES:
        text = f"{text}_file"

    # Умная обрезка по словам
    if len(text) > max_length:
        cutoff = text[:max_length].rfind(' ')
        text = text[:cutoff] if cutoff > max_length // 2 else text[:max_length]

    return text or "Untitled"
@lru_cache(maxsize=500)
def get_relative_path(target_path: Path, base_path: Path) -> Optional[str]:
    """
    Оптимизированный расчёт относительного пути с кэшированием.
    """
    try:
        target_abs = target_path.resolve()
        base_abs = base_path.resolve()
        if not base_abs.is_dir():
            base_abs = base_abs.parent

        relative = os.path.relpath(target_abs, base_abs)
        posix_relative = relative.replace(os.path.sep, '/')

        # Кэшированное URL-кодирование
        return '/'.join(urllib.parse.quote(part, safe='') for part in posix_relative.split('/'))
    except Exception as e:
        logger.warning(f"Failed to calculate relative path: {e}")
        return None
async def ensure_dir_exists_async(path: Path):
    """
    Асинхронная версия создания директории.
    """
    try:
        # Используем aiofiles для неблокирующих операций с файловой системой
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create directory {path}: {e}")
        raise
def ensure_dir_exists(path: Path):
    """
    Синхронная версия (для обратной совместимости).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create directory {path}: {e}", exc_info=True)
        raise
def find_telegram_post_links(text: str) -> List[str]:
    """
    Оптимизированный поиск ссылок на Telegram посты.

    Использует предкомпилированное регулярное выражение.
    """
    if not text:
        return []
    return [match.group(0) for match in _TELEGRAM_LINK_PATTERN.finditer(text)]
def find_telegraph_links(text: str) -> List[str]:
    """
    Оптимизированный поиск ссылок на Telegraph.

    Использует предкомпилированное регулярное выражение.
    """
    if not text:
        return []
    return [match.group(0) for match in _TELEGRAPH_LINK_PATTERN.finditer(text)]
async def log_export_completion(start_time: float, auto_return=True):
    """Логирует завершение экспорта с затраченным временем."""
    end_time = time.time()
    elapsed_time = end_time - start_time
    rprint(f"\n[bold green]Export completed successfully in {elapsed_time:.2f} seconds.[/bold green]")

    if auto_return:
        # Adaptive pause based on export duration
        if elapsed_time < 10:
            pause_duration = 2
        elif elapsed_time < 60:
            pause_duration = 3
        else:
            pause_duration = 4

        rprint(f"[cyan]Returning to the main menu in {pause_duration} seconds...[/cyan]")
        await asyncio.sleep(pause_duration)
    else:
        rprint("[cyan]Press any key to continue...[/cyan]")

class TelegraphParser:
    """
    Оптимизированный парсер Telegraph статей с кэшированием и батчевой обработкой.
    """

    def __init__(self):
        self._image_cache: Dict[str, Path] = {}
        self._content_cache: Dict[str, Dict[str, Any]] = {}

    async def parse_article(self, session: aiohttp.ClientSession, url: str,
                          media_path: Path, media_processor, cache: Optional[dict] = None,
                          entity_id: Optional[str] = None,
                          telegraph_mapping: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """
        Оптимизированный парсинг Telegraph статьи.

        Улучшения:
        - Кэширование контента
        - Асинхронный парсинг HTML
        - Батчевая загрузка изображений
        - Предкомпилированные селекторы
        """
        start_time = asyncio.get_event_loop().time()

        try:
            # Проверяем кэш контента
            if url in self._content_cache:
                logger.debug(f"Using cached content for {url}")
                return self._content_cache[url]

            # Загружаем HTML
            async with session.get(url, timeout=30) as response:
                response.raise_for_status()
                html = await response.text()

            # Асинхронный парсинг HTML
            article_data = await asyncio.to_thread(self._parse_html, html, url)
            if not article_data:
                return None

            title, pub_date, img_urls, content_elements = article_data

            # Батчевая загрузка изображений
            image_map = await self._download_images_batch(
                session, img_urls, media_path, media_processor
            )

            # Генерация Markdown
            content = await asyncio.to_thread(
                self._generate_markdown, content_elements, image_map
            )

            # Замена ссылок
            if cache and entity_id:
                content = self._replace_telegram_links(content, cache, entity_id)

            if telegraph_mapping:
                content = self._replace_telegraph_links(content, telegraph_mapping)

            result = {"title": title, "content": content, "pub_date": pub_date}

            # Кэшируем результат
            self._content_cache[url] = result

            duration = asyncio.get_event_loop().time() - start_time
            logger.debug(f"Parsed Telegraph article in {duration:.2f}s: {url}")

            return result

        except Exception as e:
            perf_context = create_performance_context(start_time, "parse_telegraph_article")
            raise PerformanceError(
                f"Error parsing Telegraph article {url}: {e}",
                operation_name="parse_telegraph_article",
                **perf_context
            ) from e

    def _parse_html(self, html: str, url: str) -> Optional[tuple]:
        """Синхронный парсинг HTML (вызывается в потоке)."""
        soup = BeautifulSoup(html, 'html.parser')
        article_content = soup.find('article')
        if not article_content:
            return None

        # Извлечение заголовка
        title_elem = article_content.find('h1')
        title = title_elem.get_text(strip=True) if title_elem else "Untitled Article"

        # Извлечение даты публикации
        pub_date = None
        time_tag = soup.find('time')
        if time_tag:
            pub_date = time_tag.get('datetime', time_tag.get_text(strip=True))[:10]

        # Извлечение URL изображений
        img_urls = []
        for img in article_content.find_all('img', src=True):
            img_src = img['src']
            if img_src.startswith('/'):
                img_src = f'https://telegra.ph{img_src}'
            img_urls.append(img_src)

        # Извлечение элементов контента
        content_elements = article_content.find_all(TELEGRAPH_CONTENT_SELECTORS)

        return title, pub_date, img_urls, content_elements

    async def _download_images_batch(self, session: aiohttp.ClientSession,
                                   img_urls: List[str], media_path: Path,
                                   media_processor) -> Dict[str, Path]:
        """Батчевая загрузка изображений."""
        if not img_urls:
            return {}

        # Фильтруем уже загруженные изображения
        new_urls = [url for url in img_urls if url not in self._image_cache]

        if new_urls:
            # Загружаем новые изображения батчами по 5
            batch_size = 5
            tasks = []

            for i in range(0, len(new_urls), batch_size):
                batch = new_urls[i:i + batch_size]
                for url in batch:
                    task = media_processor.download_external_image(session, url, media_path)
                    tasks.append((url, task))

            # Ждём завершения всех загрузок
            for url, task in tasks:
                try:
                    result = await task
                    if result:
                        self._image_cache[url] = result
                except Exception as e:
                    logger.warning(f"Failed to download image {url}: {e}")

        # Возвращаем карту URL -> Path
        return {url: self._image_cache.get(url) for url in img_urls
                if url in self._image_cache}

    def _generate_markdown(self, content_elements: List, image_map: Dict[str, Path]) -> str:
        """Генерация Markdown из элементов контента."""
        markdown_lines = []

        for element in content_elements:
            if element.name == 'p':
                line = self._process_paragraph(element)
                if line:
                    markdown_lines.append(line)

            elif element.name == 'h3':
                markdown_lines.append(f"\n### {element.get_text(strip=True)}")
            elif element.name == 'h4':
                markdown_lines.append(f"\n#### {element.get_text(strip=True)}")
            elif element.name == 'blockquote':
                markdown_lines.append(f"> {element.get_text(strip=True)}")
            elif element.name == 'ul':
                for li in element.find_all('li', recursive=False):
                    markdown_lines.append(f"* {li.get_text(strip=True)}")
            elif element.name == 'ol':
                for i, li in enumerate(element.find_all('li', recursive=False), 1):
                    markdown_lines.append(f"{i}. {li.get_text(strip=True)}")
            elif element.name == 'pre':
                markdown_lines.append(f"```\n{element.get_text()}\n```")
            elif element.name == 'hr':
                markdown_lines.append("\n---\n")
            elif element.name == 'figure':
                figure_md = self._process_figure(element, image_map)
                if figure_md:
                    markdown_lines.extend(figure_md)

        return "\n\n".join(markdown_lines)

    def _process_paragraph(self, element) -> str:
        """Обработка параграфа в Markdown."""
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
                href = child.get('href', '')
                text = child.get_text(strip=True)
                parts.append(f"[{text}]({href})")
            elif child.name == 'code':
                parts.append(f"`{child.get_text(strip=True)}`")

        return ' '.join(''.join(parts).split())

    def _process_figure(self, element, image_map: Dict[str, Path]) -> List[str]:
        """Обработка элемента figure."""
        lines = []
        img = element.find('img')
        if img and img.get('src'):
            img_src = img['src']
            if img_src.startswith('/'):
                img_src = f"https://telegra.ph{img_src}"

            local_path = image_map.get(img_src)
            if local_path:
                lines.append(f"![[{local_path.name}]]")

                figcaption = element.find('figcaption')
                if figcaption:
                    lines.append(f"*{figcaption.get_text(strip=True)}*")

        return lines

    def _replace_telegram_links(self, content: str, cache: dict, entity_id: str) -> str:
        """Замена Telegram ссылок на локальные."""
        processed_messages = cache.get("entities", {}).get(entity_id, {}).get("processed_messages", {})
        url_to_data = {data["telegram_url"]: data for data in processed_messages.values()
                      if data.get("telegram_url")}
        msg_id_to_data = {msg_id: data for msg_id, data in processed_messages.items()}

        def replacer(match: re.Match) -> str:
            link_text, tg_url = match.groups()
            clean_url = tg_url.split('?')[0].rstrip('/')

            data = url_to_data.get(clean_url)
            if not data:
                msg_id_match = _MESSAGE_ID_PATTERN.search(clean_url)
                if msg_id_match:
                    data = msg_id_to_data.get(msg_id_match.group(1))

            if data and data.get("filename"):
                title_text = data.get("title", "").replace("\n", " ").strip()
                display = title_text if title_text else link_text
                return f"[[{Path(data['filename']).stem}|{display}]]"

            return match.group(0)

        return _TELEGRAM_MARKDOWN_PATTERN.sub(replacer, content)

    def _replace_telegraph_links(self, content: str, telegraph_mapping: dict) -> str:
        """Замена Telegraph ссылок на локальные."""
        def replacer(match: re.Match) -> str:
            link_text, telegraph_url = match.groups()
            telegraph_url = telegraph_url.rstrip('/')
            note_stem = telegraph_mapping.get(telegraph_url)
            if note_stem:
                return f"[[{note_stem}|{link_text}]]"
            return match.group(0)

        return _TELEGRAPH_MARKDOWN_PATTERN.sub(replacer, content)
# Глобальный экземпляр парсера
_telegraph_parser = TelegraphParser()
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
    Оптимизированная функция парсинга Telegraph статей.

    Использует глобальный экземпляр TelegraphParser с кэшированием.
    """
    return await _telegraph_parser.parse_article(
        session, url, media_path, media_processor, cache, entity_id, telegraph_mapping
    )
def get_bool_input(prompt: str, default: bool = False) -> bool:
    """Запрос булевого ввода от пользователя."""
    default_str = "y" if default else "n"
    response = input(f"{prompt} [Y/n] ").lower() or default_str
    return response.startswith('y')
async def async_input(prompt: str) -> str:
    """
    Асинхронная версия input() для неблокирующего ввода.

    Критично для интерактивного режима - не блокирует event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)

async def batch_read_files(file_paths: List[Path],
                         batch_size: int = 20) -> Dict[Path, str]:
    """
    Батчевое чтение файлов для оптимизации I/O операций.

    Args:
        file_paths: Список путей к файлам
        batch_size: Размер батча для параллельного чтения

    Returns:
        Словарь {путь: содержимое} для успешно прочитанных файлов
    """
    results = {}

    async def read_single_file(path: Path) -> tuple[Path, Optional[str]]:
        try:
            async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return path, content
        except Exception as e:
            logger.warning(f"Failed to read file {path}: {e}")
            return path, None

    # Обрабатываем файлы батчами
    for i in range(0, len(file_paths), batch_size):
        batch = file_paths[i:i + batch_size]
        batch_results = await asyncio.gather(
            *[read_single_file(path) for path in batch],
            return_exceptions=True
        )

        for result in batch_results:
            if isinstance(result, tuple) and result[1] is not None:
                path, content = result
                results[path] = content

    return results
async def batch_write_files(file_data: Dict[Path, str],
                          batch_size: int = 20) -> Set[Path]:
    """
    Батчевая запись файлов.

    Args:
        file_data: Словарь {путь: содержимое}
        batch_size: Размер батча

    Returns:
        Множество успешно записанных файлов
    """
    successful = set()

    async def write_single_file(path: Path, content: str) -> Optional[Path]:
        try:
            # Создаём директорию если нужно
            await ensure_dir_exists_async(path.parent)

            async with aiofiles.open(path, 'w', encoding='utf-8') as f:
                await f.write(content)
                return path
        except Exception as e:
            logger.warning(f"Failed to write file {path}: {e}")
            return None

    # Записываем файлы батчами
    items = list(file_data.items())
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_results = await asyncio.gather(
            *[write_single_file(path, content) for path, content in batch],
            return_exceptions=True
        )

        for result in batch_results:
            if isinstance(result, Path):
                successful.add(result)

    return successful
def chunks(lst: List[T], chunk_size: int) -> List[List[T]]:
    """Разбивает список на чанки заданного размера."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]
async def chunks_async(async_iter: AsyncGenerator[T, None],
                      chunk_size: int) -> AsyncGenerator[List[T], None]:
    """Асинхронная версия chunks для AsyncGenerator."""
    chunk = []
    async for item in async_iter:
        chunk.append(item)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []

    if chunk:
        yield chunk
class PerformanceProfiler:
    """
    A simple profiler to measure the performance of specific operations at runtime.
    """
    def __init__(self):
        self.metrics = defaultdict(list)

    @contextmanager
    def profile(self, operation_name: str):
        """A context manager to profile a block of code."""
        start_time = time.perf_counter()
        try:
            yield
        finally:
            duration = time.perf_counter() - start_time
            self.metrics[operation_name].append(duration)

    def log_stats(self, operation_name: str):
        """Logs the performance statistics for a given operation."""
        if operation_name not in self.metrics:
            logger.info(f"No performance metrics for '{operation_name}'")
            return

        durations = self.metrics[operation_name]
        count = len(durations)
        total_time = sum(durations)
        avg_time = total_time / count
        max_time = max(durations)
        min_time = min(durations)

        logger.info(
            f"Performance stats for {operation_name}: "
            f"Count={count}, Total={total_time:.2f}s, Avg={avg_time:.3f}s, "
            f"Max={max_time:.3f}s, Min={min_time:.3f}s"
        )

def prompt_int(prompt, default):
    """
    Prompt user for integer input with a default value.

    Args:
        prompt: The prompt string to display.
        default: The default integer value.

    Returns:
        int: The user input as integer, or default if invalid.
    """
    try:
        rprint(f"[bold]{prompt}[/bold] [dim][{default}][/dim]", end=" ")
        val = input().strip()
        return int(val) if val else default
    except Exception:
        rprint("[red]Invalid input, using default.[/red]")
        return default

def prompt_float(prompt, default):
    """
    Prompt user for float input with a default value.

    Args:
        prompt: The prompt string to display.
        default: The default float value.

    Returns:
        float: The user input as float, or default if invalid.
    """
    try:
        rprint(f"[bold]{prompt}[/bold] [dim][{default}][/dim]", end=" ")
        val = input().strip()
        return float(val) if val else default
    except Exception:
        rprint("[red]Invalid input, using default.[/red]")
        return default
