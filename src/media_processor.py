import asyncio
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional, Union

import aiofiles
import aiofiles.os
import aiohttp
import ffmpeg
from PIL import ImageFile
from rich import print as rprint
from telethon import TelegramClient
from telethon.tl.types import (
    Document,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
)

from src.config import Config
from src.utils import logger, sanitize_filename

ImageFile.LOAD_TRUNCATED_IMAGES = True

AIOHTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

async def async_ensure_dir_exists(path: Path):
    """Асинхронно убеждается, что директория существует."""
    await aiofiles.os.makedirs(path, exist_ok=True)


class ProgressCallback:
    """
    Callback for tracking and logging download progress, and handling throttling.

    Args:
        config (Config): Configuration object.
        description (str): Description of the file being downloaded.
    """
    def __init__(self, config: Config, description: str):
        self.config = config
        self.description = description
        self.last_check_time = time.time()
        self.last_downloaded = 0
        self.throttle_counter = 0

    async def __call__(self, downloaded_bytes: int, total_bytes: int):
        """
        Called during download to update progress and check for throttling.

        Args:
            downloaded_bytes (int): Number of bytes downloaded so far.
            total_bytes (int): Total bytes to download.
        """
        await self._check_throttling(downloaded_bytes)

    async def _check_throttling(self, downloaded_bytes: int):
        """
        Checks download speed and pauses if throttling is suspected.

        Args:
            downloaded_bytes (int): Number of bytes downloaded so far.
        """
        now = time.time()
        elapsed = now - self.last_check_time
        if elapsed < 3:
            return

        speed_kbps = ((downloaded_bytes - self.last_downloaded) / elapsed) / 1024
        threshold = getattr(self.config, 'throttle_threshold_kbps', 50)
        pause_duration = getattr(self.config, 'throttle_pause_s', 30)

        if 0 < speed_kbps < threshold:
            self.throttle_counter += 1
        else:
            self.throttle_counter = 0

        if self.throttle_counter >= 2:
            rprint(f"[yellow]Скорость загрузки очень низкая ({speed_kbps:.1f} KB/s). Подозрение на throttling. Пауза {pause_duration} сек...[/yellow]")
            await asyncio.sleep(pause_duration)
            self.throttle_counter = 0

        self.last_check_time = now
        self.last_downloaded = downloaded_bytes


class MediaProcessor:
    """
    Handles downloading, optimizing, and managing media files from Telegram messages.

    Args:
        config (Config): Configuration object.
        client (TelegramClient): Telethon client instance.
    """
    def __init__(self, config: Config, client: TelegramClient):
        self.config = config
        self.client = client
        self.download_semaphore = asyncio.Semaphore(
            getattr(config, "download_workers", int(getattr(config, "workers", 8) * 1.5))
        )
        self.processed_cache = {}
        self._cache_lock = asyncio.Lock()

    async def download_and_optimize_media(
        self, message: Message, entity_id: Union[str, int], entity_media_path: Path,
        progress_queue=None, task_id=None
    ) -> List[Path]:
        """
        Downloads and optimizes all media from a Telegram message.

        Args:
            message (Message): Telegram message object.
            entity_id (Union[str, int]): ID of the entity (chat/user).
            entity_media_path (Path): Path to store media files.
            progress_queue: asyncio.Queue for progress updates (optional).
            task_id: Rich Progress task id (optional).

        Returns:
            List[Path]: List of paths to processed media files.
        """
        if not self.config.media_download:
            return []

        media_items = []
        try:
            await self._add_media_from_message(message, media_items)
        except Exception as e:
            logger.warning(f"Error extracting media from msg {message.id}: {e}.")
            return []

        if not media_items:
            return []


        entity_id_str = str(entity_id)
        tasks = []
        for media_type, msg in media_items:
            tasks.append(self._process_single_item(
                msg, entity_id_str, media_type, entity_media_path, progress_queue, task_id
            ))

        results = []
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            rprint(f"[red]Ошибка при обработке группы медиа для сообщения {getattr(message, 'id', 'unknown')}: {e}[/red]")
            results = []

        final_paths = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                media_type, msg = media_items[i]
                media_id = getattr(msg, 'id', 'unknown')
                rprint(f"[red]Ошибка обработки {media_type} (ID: {media_id}): {result}[/red]")
            elif result and isinstance(result, Path):
                final_paths.append(result)

        return final_paths

    async def download_external_image(self, session: aiohttp.ClientSession, url: str, media_path: Path, max_retries: int = 3) -> Optional[Path]:
        """
        Downloads an external image from a URL and saves it to the specified path.

        Args:
            session (aiohttp.ClientSession): Aiohttp session for HTTP requests.
            url (str): URL of the image to download.
            media_path (Path): Directory to save the image.
            max_retries (int): Maximum number of download attempts.

        Returns:
            Optional[Path]: Path to the downloaded image, or None if failed.
        """
        attempt = 0
        while attempt < max_retries:
            try:
                async with self.download_semaphore:
                    async with session.get(url, timeout=60, headers=AIOHTTP_HEADERS) as response:
                        if response.status != 200:
                            rprint(f"[red]Ошибка скачивания изображения {url}. Status: {response.status}, Reason: {response.reason}[/red]")
                            attempt += 1
                            continue

                        content_type = response.headers.get('Content-Type', '')
                        ext = mimetypes.guess_extension(content_type) or '.jpg'

                        base_name = sanitize_filename(Path(url).stem, max_length=50)
                        filename = f"telegraph_{base_name}_{os.urandom(4).hex()}{ext}"

                        images_dir = media_path / "images"
                        await async_ensure_dir_exists(images_dir)
                        file_path = images_dir / filename

                        if await aiofiles.os.path.exists(file_path):
                            return file_path

                        async with aiofiles.open(file_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(1024):
                                await f.write(chunk)

                        return file_path
            except Exception as e:
                rprint(f"[red]Ошибка скачивания или сохранения внешнего изображения {url} (попытка {attempt+1}/{max_retries}): {e}[/red]")
                attempt += 1
        rprint(f"[red]Отказ от скачивания внешнего изображения {url} после {max_retries} попыток.[/red]")
        return None

    async def _add_media_from_message(self, message: Message, media_items: List):
        """
        Adds media items from a Telegram message to the provided list.

        Args:
            message (Message): Telegram message object.
            media_items (List): List to append found media items.
        """
        if not message.media:
            return
        if isinstance(message.media, MessageMediaPhoto) and isinstance(message.media.photo, Photo):
            media_items.append(("image", message))
        elif isinstance(message.media, MessageMediaDocument) and isinstance(message.media.document, Document):
            media_items.append((self._get_document_type(message.media.document), message))

    def _get_document_type(self, doc: Document) -> str:
        """
        Determines the type of a Telegram document.

        Args:
            doc (Document): Telegram document object.

        Returns:
            str: Type of the document ('video', 'round_video', 'audio', or 'document').
        """
        if any(isinstance(attr, DocumentAttributeVideo) for attr in doc.attributes):
            return "round_video" if any(getattr(attr, 'round_message', False) for attr in doc.attributes if isinstance(attr, DocumentAttributeVideo)) else "video"
        return "audio" if any(isinstance(attr, DocumentAttributeAudio) for attr in doc.attributes) else "document"

    async def _process_single_item(
            self, message: Message, entity_id_str: str, media_type: str, entity_media_path: Path, progress_queue=None, task_id=None
    ) -> Optional[Path]:
        """
        Processes a single media item: downloads and optimizes it.

        Args:
            message (Message): Telegram message object.
            entity_id_str (str): Entity ID as string.
            media_type (str): Type of media ('image', 'video', etc.).
            entity_media_path (Path): Path to store media files.
            progress_queue: asyncio.Queue for progress updates (optional).
            task_id: Progress task id (optional).

        Returns:
            Optional[Path]: Path to the processed media file, or None if failed.
        """
        try:
            if isinstance(message.media, MessageMediaPhoto) and isinstance(message.media.photo, Photo):
                media_obj = message.media.photo
            elif isinstance(message.media, MessageMediaDocument) and isinstance(message.media.document, Document):
                media_obj = message.media.document
            else:
                logger.error(f"Unsupported media type in message {message.id}")
                return None

            filename = self._get_filename(media_obj, message.id, media_type, entity_id_str)
            type_subdir = entity_media_path / f"{media_type}s"
            final_path = type_subdir / filename
            raw_download_path = type_subdir / f"raw_{filename}"

            cache_manager = self.config.cache_manager if hasattr(self.config, "cache_manager") else None
            all_media_ok = False
            if cache_manager:
                all_media_ok = await cache_manager.all_media_files_present(
                    entity_id_str, message.id, type_subdir
                )
            if all_media_ok and await aiofiles.os.path.exists(final_path):
                # If file exists, we should still advance the progress bar by its size
                if progress_queue and task_id:
                    total_size = getattr(message.file, 'size', 0) if message.file else 0
                    if total_size > 0:
                         await progress_queue.put({
                            "type": "update", "task_id": task_id, "data": {"advance": total_size}
                        })
                return final_path

            await async_ensure_dir_exists(type_subdir)

            downloaded_ok = await self._download_media(message, raw_download_path, filename, progress_queue=progress_queue, task_id=task_id)
            if not downloaded_ok:
                await self._cleanup_file_async(raw_download_path)
                return None

            optimization_success = await self._optimize_media(raw_download_path, final_path, media_type)
            if optimization_success:
                if raw_download_path != final_path:
                    await self._cleanup_file_async(raw_download_path)
                if cache_manager:
                    stat_result = await aiofiles.os.stat(final_path)
                    media_size = stat_result.st_size if stat_result else 0
                    await cache_manager.add_media_file_to_message(
                        entity_id_str, message.id, final_path.name, media_size
                    )
                return final_path
            else:
                logger.error(f"Media processing failed for msg {message.id}, type {media_type}")
                await self._cleanup_file_async(raw_download_path)
                return None
        except Exception as e:
            logger.error(f"Error in media processing pipeline for msg {getattr(message, 'id', 'unknown')}: {e}", exc_info=True)
            return None

    async def _download_media(self, message: Message, raw_download_path: Path, filename: str, progress_queue=None, task_id=None) -> bool:
        """
        Downloads media from a Telegram message.

        Args:
            message (Message): Telegram message object.
            raw_download_path (Path): Path to save the downloaded file.
            filename (str): Name of the file being downloaded.
            progress_queue: asyncio.Queue for progress updates (optional).
            task_id: Progress task id (optional).

        Returns:
            bool: True if download succeeded, False otherwise.
        """
        try:
            async with self.download_semaphore:
                last_reported_bytes = 0

                async def callback(current, total):
                    nonlocal last_reported_bytes
                    if progress_queue is not None and task_id is not None:
                        advanced = current - last_reported_bytes
                        if advanced > 0:
                            await progress_queue.put({
                                "type": "update",
                                "task_id": task_id,
                                "data": {"advance": advanced}
                            })
                            last_reported_bytes = current

                await self.client.download_media(
                    message=message,
                    file=str(raw_download_path),
                    progress_callback=callback
                )

                # After download, ensure the progress is complete by advancing the remainder.
                if progress_queue is not None and task_id is not None:
                    total_size = getattr(message.file, 'size', 0) if message.file else 0
                    if total_size > 0:
                        remaining = total_size - last_reported_bytes
                        if remaining > 0:
                            await progress_queue.put({
                                "type": "update",
                                "task_id": task_id,
                                "data": {"advance": remaining}
                            })

                return await aiofiles.os.path.exists(raw_download_path)
        except Exception as e:
            logger.error(f"Download failed for {filename}: {e}", exc_info=True)
            return False

    async def _optimize_media(self, raw_path: Path, final_path: Path, media_type: str) -> bool:
        """
        Optimizes a downloaded media file based on its type.

        Args:
            raw_path (Path): Path to the raw downloaded file.
            final_path (Path): Path to save the optimized file.
            media_type (str): Type of media ('image', 'video', etc.).

        Returns:
            bool: True if optimization succeeded, False otherwise.
        """
        try:
            if media_type == "image":
                # shutil.copy is blocking, run in an executor thread
                await asyncio.to_thread(shutil.copy, raw_path, final_path)
            elif media_type in ["video", "round_video"]:
                await self._optimize_video(raw_path, final_path)
            elif media_type == "audio":
                await self._optimize_audio(raw_path, final_path)
            else:
                # Use aiofiles for async rename
                await aiofiles.os.rename(raw_path, final_path)
            return await aiofiles.os.path.exists(final_path)
        except Exception as e:
            logger.error(f"Failed to process {media_type} {raw_path.name}: {e}")
            return False

    async def _cleanup_file_async(self, file_path: Path):
        """
        Asynchronously deletes a file if it exists.

        Args:
            file_path (Path): Path to the file to delete.
        """
        try:
            if await aiofiles.os.path.exists(file_path):
                await aiofiles.os.remove(file_path)
        except Exception as e:
            logger.warning(f"Could not clean up file {file_path}: {e}")

    def _get_filename(self, media_obj: Union[Photo, Document], message_id: int, media_type: str, entity_id_str: str) -> str:
        """
        Generates a safe filename for a media object.

        Args:
            media_obj (Union[Photo, Document]): Media object (Photo or Document).
            message_id (int): Telegram message ID.
            media_type (str): Type of media.
            entity_id_str (str): Entity ID as string.

        Returns:
            str: Safe filename for the media.
        """
        media_id = getattr(media_obj, 'id', 'no_id')
        base_name = f"{entity_id_str}_msg{message_id}_{media_type}_{media_id}"
        ext = ".dat"
        if isinstance(media_obj, Photo):
            ext = ".jpg"
        elif isinstance(media_obj, Document):
            original_filename = next((attr.file_name for attr in getattr(media_obj, 'attributes', []) if isinstance(attr, DocumentAttributeFilename)), None)
            if original_filename and Path(original_filename).suffix:
                ext = Path(original_filename).suffix
            elif hasattr(media_obj, 'mime_type') and '/' in media_obj.mime_type:
                ext = mimetypes.guess_extension(media_obj.mime_type) or ext
            if media_type in ['video', 'round_video'] and ext.lower() in ['.dat', '.bin']:
                ext = '.mp4'
            elif media_type == 'audio' and any(getattr(attr, 'voice', False) for attr in media_obj.attributes if isinstance(attr, DocumentAttributeAudio)):
                ext = '.ogg'

        safe_base = sanitize_filename(base_name, max_length=180, replacement='_')
        safe_ext = sanitize_filename(ext, max_length=10, replacement='')
        if not safe_ext.startswith('.'):
            safe_ext = '.' + safe_ext
        return f"{safe_base}{safe_ext}"

    async def _optimize_video(self, input_path: Path, output_path: Path):
        """
        Asynchronously optimizes a video file by running the sync version in a separate thread.
        """
        await asyncio.to_thread(self._sync_optimize_video, input_path, output_path)

    def _sync_optimize_video(self, input_path: Path, output_path: Path):
        """
        Synchronously optimizes a video file using ffmpeg. This is a blocking function.
        """
        try:
            hw_acceleration = getattr(self.config, 'hw_acceleration', 'none').lower()
            use_h265 = getattr(self.config, 'use_h265', True)
            probe = ffmpeg.probe(str(input_path))
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                logger.warning(f"No video stream in {input_path.name}, copying directly.")
                ffmpeg.input(str(input_path)).output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'error', '-nostats').run(capture_stderr=True, overwrite_output=True)
                return

            width = int(video_stream.get('width', 0))
            height = int(video_stream.get('height', 0))
            optimal_bitrate = self._calculate_optimal_bitrate(width, height)
            stream = ffmpeg.input(str(input_path))
            ffmpeg_options = {'pix_fmt': 'yuv420p', 'threads': '0', 'movflags': '+faststart'}
            base_crf = getattr(self.config, 'video_crf', 23)
            compression_crf = min(base_crf + 5, 35)

            output_ext = output_path.suffix.lower()

            # --- Video Codec Selection ---
            if output_ext == ".webm":
                # WebM requires compatible codecs. Use libvpx-vp9 for video.
                ffmpeg_options['c:v'] = 'libvpx-vp9'
                ffmpeg_options.update({'crf': str(compression_crf), 'b:v': optimal_bitrate})
            else:
                # For other formats, use the existing hardware acceleration logic.
                if hw_acceleration == 'nvidia':
                    self._configure_nvidia_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)
                elif hw_acceleration == 'amd':
                    self._configure_amd_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)
                elif hw_acceleration == 'intel':
                    self._configure_intel_encoder(ffmpeg_options, use_h265, optimal_bitrate)
                else:
                    self._configure_software_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)

            # --- Audio Codec Selection ---
            if audio_stream := next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None):
                if output_ext == ".webm":
                    # WebM requires Opus or Vorbis. Use libopus for audio.
                    ffmpeg_options.update({'c:a': 'libopus', 'b:a': '64k'})
                else:
                    # Original audio configuration for other formats.
                    self._configure_audio_options(ffmpeg_options, audio_stream, float(video_stream.get('duration', 0)), 'voice' in input_path.name.lower())


            process = ffmpeg.output(stream, str(output_path), **ffmpeg_options).global_args('-hide_banner', '-loglevel', 'error', '-nostats')

            try:
                # Правильный вызов без check=True
                process.run(capture_stderr=True, overwrite_output=True)
            except ffmpeg.Error as e:
                args = process.get_args()
                stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
                logger.error(f"ffmpeg failed for video {input_path.name}. Command: `ffmpeg {' '.join(args)}`. Stderr: {stderr}")
                # Копируем исходный файл, если оптимизация провалилась
                if not output_path.exists() or output_path.stat().st_size == 0:
                    shutil.copy(input_path, output_path)
                return

            # Если оптимизированный файл больше исходного, копируем исходный
            if output_path.exists() and input_path.exists() and output_path.stat().st_size >= input_path.stat().st_size:
                shutil.copy(input_path, output_path)

        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg setup failed for video {input_path.name}: {stderr}")
            if not output_path.exists() or output_path.stat().st_size == 0:
                shutil.copy(input_path, output_path)
        except Exception as e:
            logger.error(f"Video optimization failed for {input_path.name}: {e}")
            if not output_path.exists() or output_path.stat().st_size == 0:
                shutil.copy(input_path, output_path)

    async def _optimize_audio(self, input_path: Path, output_path: Path):
        """
        Asynchronously optimizes an audio file by running the sync version in a separate thread.
        """
        await asyncio.to_thread(self._sync_optimize_audio, input_path, output_path)

    def _sync_optimize_audio(self, input_path: Path, output_path: Path):
        """
        Synchronously optimizes an audio file using ffmpeg. This is a blocking function.
        """
        try:
            probe = ffmpeg.probe(str(input_path))
            audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
            if not audio_stream:
                logger.warning(f"No audio stream in {input_path.name}, copying directly.")
                ffmpeg.input(str(input_path)).output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True)
                return

            channels = int(audio_stream.get('channels', 2))
            sample_rate = int(audio_stream.get('sample_rate', 48000))
            codec_name = audio_stream.get('codec_name', '').lower()
            optimal_bitrate = self._calculate_audio_bitrate(audio_stream.get('bit_rate'), channels)
            output_format = output_path.suffix.lower().lstrip('.') or 'mp3'

            stream = ffmpeg.input(str(input_path)).audio
            ffmpeg_options = {'b:a': optimal_bitrate}

            if output_format in ['ogg', 'oga'] and codec_name == 'opus':
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate in [8000, 12000, 16000, 24000, 48000] else '48000'
            else:
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate else '44100'

            if output_format == 'mp3':
                ffmpeg_options.update({'c:a': 'libmp3lame', 'q:a': '4', 'compression_level': '9'})
            elif output_format in ['ogg', 'oga']:
                if codec_name == 'opus':
                    ffmpeg_options.update({'c:a': 'libopus', 'b:a': '32k' if channels == 1 else '64k', 'vbr': 'on', 'compression_level': '10'})
                else:
                    ffmpeg_options.update({'c:a': 'libvorbis', 'q:a': '3'})
            elif output_format in ['m4a', 'aac']:
                ffmpeg_options.update({'c:a': 'aac', 'q:a': '1'})

            if 'voice' in input_path.name.lower() or codec_name in ['opus', 'speex']:
                if output_format == 'ogg':
                    ffmpeg_options.update({'c:a': 'libopus', 'b:a': '32k', 'vbr': 'on', 'compression_level': '10', 'application': 'voip'})
                else:
                    ffmpeg_options.update({'b:a': '48k', 'ac': '1'})

            try:
                ffmpeg.output(stream, str(output_path), **ffmpeg_options).global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
            except ffmpeg.Error as e:
                if 'incorrect codec parameters' in e.stderr.decode('utf-8', errors='ignore'):
                    logger.warning(f"Codec parameter issue, trying simpler encoding for {input_path.name}")
                    ffmpeg.input(str(input_path)).audio.output(
                        str(output_path),
                        c='aac' if output_format in ['m4a', 'aac'] else 'libmp3lame'
                    ).global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
                else:
                    raise

            if output_path.exists() and input_path.exists() and output_path.stat().st_size >= input_path.stat().st_size:
                ffmpeg.input(str(input_path)).audio.output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg failed for audio {input_path.name}: {stderr}")
            try:
                # Fallback to simple copy if optimization fails
                shutil.copy2(input_path, output_path)
            except Exception as copy_err:
                logger.error(f"Audio optimization and fallback copy failed for {input_path.name}: {copy_err}")
        except Exception as e:
            logger.error(f"Audio optimization failed for {input_path.name}: {e}")

    def _configure_nvidia_encoder(self, options, use_h265, crf, bitrate):
        codec = 'hevc_nvenc' if use_h265 else 'h264_nvenc'
        options.update({'c:v': codec, 'preset': 'p6', 'rc:v': 'vbr_hq', 'cq': str(crf), 'b:v': bitrate, 'spatial-aq': '1', 'temporal-aq': '1'})

    def _configure_amd_encoder(self, options, use_h265, crf, bitrate):
        codec = 'hevc_amf' if use_h265 else 'h264_amf'
        options.update({'c:v': codec, 'quality': 'quality', 'qp_i': str(crf), 'qp_p': str(crf + 2), 'b:v': bitrate.replace('k', '000')})

    def _configure_intel_encoder(self, options, use_h265, bitrate):
        codec = 'hevc_qsv' if use_h265 else 'h264_qsv'
        options.update({'c:v': codec, 'preset': 'slower', 'b:v': bitrate, 'look_ahead': '1'})

    def _configure_software_encoder(self, options, use_h265, crf, bitrate):
        if use_h265:
            options.update({'c:v': 'libx265', 'crf': str(crf), 'preset': self.config.video_preset, 'x265-params': "profile=main:level=5.1:no-sao=1:bframes=8:rd=4:psy-rd=1.0:rect=1:aq-mode=3:aq-strength=0.8:deblock=-1:-1", 'maxrate': bitrate, 'bufsize': f"{int(bitrate.replace('k', '')) * 2}k"})
        else:
            options.update({'c:v': 'libx264', 'crf': str(crf), 'preset': self.config.video_preset, 'profile:v': 'high', 'level': '4.1', 'tune': 'film', 'subq': '9', 'trellis': '2', 'partitions': 'all', 'direct-pred': 'auto', 'me_method': 'umh', 'g': '250', 'maxrate': bitrate, 'bufsize': f"{int(bitrate.replace('k', '')) * 2}k"})

    def _configure_audio_options(self, options, audio_stream, duration, is_voice_hint):
        audio_bitrate = self._calculate_audio_bitrate(audio_stream.get('bit_rate'), audio_stream.get('channels', 2))
        options.update({'c:a': 'aac', 'b:a': audio_bitrate, 'ar': '44100', 'ac': '2'})
        if is_voice_hint or duration > 0:
            options['b:a'] = '64k'
            options['ac'] = '1'

    def _calculate_optimal_bitrate(self, width: int, height: int) -> str:
        pixels = width * height
        if pixels <= 0:
            return "500k"
        if pixels >= 2073600:
            return "1500k"
        if pixels >= 921600:
            return "800k"
        if pixels >= 409920:
            return "500k"
        return "400k"

    def _calculate_audio_bitrate(self, current_bitrate, channels: int) -> str:
        if not current_bitrate:
            return "96k" if channels > 1 else "64k"
        try:
            bitrate = int(current_bitrate)
            if bitrate > 320000:
                return "128k"
            if bitrate > 128000:
                return "96k"
            return "64k"
        except (ValueError, TypeError):
            return "96k"

    async def _optimize_audio(self, input_path: Path, output_path: Path):
        """
        Asynchronously optimizes an audio file by running the sync version in a separate thread.
        """
        await asyncio.to_thread(self._sync_optimize_audio, input_path, output_path)

    def _sync_optimize_audio(self, input_path: Path, output_path: Path):
        """
        Synchronously optimizes an audio file using ffmpeg. This is a blocking function.
        """
        try:
            probe = ffmpeg.probe(str(input_path))
            audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
            if not audio_stream:
                logger.warning(f"No audio stream in {input_path.name}, copying directly.")
                ffmpeg.input(str(input_path)).output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True)
                return

            channels = int(audio_stream.get('channels', 2))
            sample_rate = int(audio_stream.get('sample_rate', 48000))
            codec_name = audio_stream.get('codec_name', '').lower()
            optimal_bitrate = self._calculate_audio_bitrate(audio_stream.get('bit_rate'), channels)
            output_format = output_path.suffix.lower().lstrip('.') or 'mp3'

            stream = ffmpeg.input(str(input_path)).audio
            ffmpeg_options = {'b:a': optimal_bitrate}

            if output_format in ['ogg', 'oga'] and codec_name == 'opus':
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate in [8000, 12000, 16000, 24000, 48000] else '48000'
            else:
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate else '44100'

            if output_format == 'mp3':
                ffmpeg_options.update({'c:a': 'libmp3lame', 'q:a': '4', 'compression_level': '9'})
            elif output_format in ['ogg', 'oga']:
                if codec_name == 'opus':
                    ffmpeg_options.update({'c:a': 'libopus', 'b:a': '32k' if channels == 1 else '64k', 'vbr': 'on', 'compression_level': '10'})
                else:
                    ffmpeg_options.update({'c:a': 'libvorbis', 'q:a': '3'})
            elif output_format in ['m4a', 'aac']:
                ffmpeg_options.update({'c:a': 'aac', 'q:a': '1'})

            if 'voice' in input_path.name.lower() or codec_name in ['opus', 'speex']:
                if output_format == 'ogg':
                    ffmpeg_options.update({'c:a': 'libopus', 'b:a': '32k', 'vbr': 'on', 'compression_level': '10', 'application': 'voip'})
                else:
                    ffmpeg_options.update({'b:a': '48k', 'ac': '1'})

            try:
                ffmpeg.output(stream, str(output_path), **ffmpeg_options).global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
            except ffmpeg.Error as e:
                if 'incorrect codec parameters' in e.stderr.decode('utf-8', errors='ignore'):
                    logger.warning(f"Codec parameter issue, trying simpler encoding for {input_path.name}")
                    ffmpeg.input(str(input_path)).audio.output(
                        str(output_path),
                        c='aac' if output_format in ['m4a', 'aac'] else 'libmp3lame'
                    ).global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
                else:
                    raise

            if output_path.exists() and input_path.exists() and output_path.stat().st_size >= input_path.stat().st_size:
                ffmpeg.input(str(input_path)).audio.output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg failed for audio {input_path.name}: {stderr}")
            try:
                # Fallback to simple copy if optimization fails
                shutil.copy2(input_path, output_path)
            except Exception as copy_err:
                logger.error(f"Audio optimization and fallback copy failed for {input_path.name}: {copy_err}")
        except Exception as e:
            logger.error(f"Audio optimization failed for {input_path.name}: {e}")
