# src/media_processor.py

"""
MediaProcessor: Handles media downloads, progress reporting, throttling, and optimization.
"""

import asyncio
import concurrent.futures
import mimetypes
import os
import time
from pathlib import Path
from typing import List, Optional, Union

import aiofiles
import aiohttp
import ffmpeg
from PIL import Image, UnidentifiedImageError
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
from src.utils import ensure_dir_exists, logger, run_in_thread_pool, sanitize_filename

AIOHTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

class ProgressCallback:
    """
    Reports concise, event-based progress and handles throttling.
    """
    def __init__(self, config: Config, description: str):
        self.config = config
        self.description = description
        self.last_check_time = time.time()
        self.last_downloaded = 0
        self.throttle_counter = 0

    def __call__(self, downloaded_bytes: int, total_bytes: int):
        percent = int((downloaded_bytes / total_bytes) * 100) if total_bytes else 0
        if not hasattr(self, "_last_percent") or percent // 10 != getattr(self, "_last_percent", -1):
            rprint(f"Downloading {self.description} {percent}% ({downloaded_bytes // 1024} kb / {total_bytes // 1024} kb)")
            self._last_percent = percent // 10
        self._check_throttling(downloaded_bytes)

    def _check_throttling(self, downloaded_bytes: int):
        """
        Checks download speed and pauses if too low.
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
            logger.warning(f"Download speed is very low ({speed_kbps:.1f} KB/s). Throttling suspected. Pausing for {pause_duration}s...")
            logger.warning(f"Throttling download: speed={speed_kbps:.1f} KB/s, pausing for {pause_duration}s")
            time.sleep(pause_duration)
            self.throttle_counter = 0

        self.last_check_time = now
        self.last_downloaded = downloaded_bytes


class MediaProcessor:
    def __init__(self, config: Config, client: TelegramClient):
        self.config = config
        self.client = client
        self.download_semaphore = asyncio.Semaphore(config.concurrent_downloads)
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.max_workers, thread_name_prefix="MediaThread"
        )
        self.processed_cache = {}
        self._cache_lock = asyncio.Lock()

    async def download_and_optimize_media(
        self, message: Message, entity_id: Union[str, int], entity_media_path: Path
    ) -> List[Path]:
        if not self.config.media_download:
            return []

        media_items = []
        entity_id_str = str(entity_id)

        try:
            await self._add_media_from_message(message, media_items)
        except Exception as e:
            logger.warning(f"Error extracting media from msg {message.id}: {e}.")
            return []

        if not media_items:
            return []

        tasks = [
            self._process_single_item(msg, entity_id_str, media_type, entity_media_path)
            for media_type, msg in media_items if isinstance(msg, Message)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_paths = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                media_type, media_obj = media_items[i]
                media_id = getattr(media_obj, 'id', 'unknown')
                logger.error(f"Failed to process {media_type} (ID: {media_id}): {result}", exc_info=True)
            elif result and isinstance(result, Path):
                final_paths.append(result)

        return final_paths

    async def download_external_image(self, session: aiohttp.ClientSession, url: str, media_path: Path, max_retries: int = 3) -> Optional[Path]:
        attempt = 0
        while attempt < max_retries:
            try:
                async with self.download_semaphore:
                    async with session.get(url, timeout=60) as response:
                        if response.status != 200:
                            logger.error(f"Failed to download image {url}. Status: {response.status}, Reason: {response.reason}")
                            attempt += 1
                            continue

                        total_size = int(response.headers.get('content-length', 0))
                        content_type = response.headers.get('Content-Type', '')
                        ext = mimetypes.guess_extension(content_type) or '.jpg'

                        base_name = sanitize_filename(Path(url).stem, max_length=50)
                        filename = f"telegraph_{base_name}_{os.urandom(4).hex()}{ext}"

                        images_dir = media_path / "images"
                        await run_in_thread_pool(ensure_dir_exists, images_dir)
                        file_path = images_dir / filename

                        if file_path.exists():
                            return file_path

                        # Always use concise, event-based logging every 10%
                        downloaded = 0
                        last_percent = -1
                        async with aiofiles.open(file_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(1024):
                                await f.write(chunk)
                                downloaded += len(chunk)
                                percent = int((downloaded / total_size) * 100) if total_size else 0
                                if percent // 10 != last_percent:
                                    rprint(f"Downloading {filename} {percent}% ({downloaded // 1024} kb / {total_size // 1024} kb)")
                                    last_percent = percent // 10

                        return file_path
            except Exception as e:
                logger.error(f"Failed to download or save external image {url} (attempt {attempt+1}/{max_retries}): {e}", exc_info=True)
                attempt += 1
        logger.error(f"Giving up on downloading external image {url} after {max_retries} attempts.")
        return None

    async def _add_media_from_message(self, message: Message, media_items: List):
        if not message.media:
            return
        if isinstance(message.media, MessageMediaPhoto) and isinstance(message.media.photo, Photo):
            media_items.append(("image", message))
        elif isinstance(message.media, MessageMediaDocument) and isinstance(message.media.document, Document):
            media_items.append((self._get_document_type(message.media.document), message))

    def _get_document_type(self, doc: Document) -> str:
        if any(isinstance(attr, DocumentAttributeVideo) for attr in doc.attributes):
            return "round_video" if any(getattr(attr, 'round_message', False) for attr in doc.attributes if isinstance(attr, DocumentAttributeVideo)) else "video"
        return "audio" if any(isinstance(attr, DocumentAttributeAudio) for attr in doc.attributes) else "document"

    async def _process_single_item(
            self, message: Message, entity_id_str: str, media_type: str, entity_media_path: Path
    ) -> Optional[Path]:
        try:
            # Determine the media_obj for filename generation
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

            if final_path.exists():
                return final_path

            await run_in_thread_pool(ensure_dir_exists, type_subdir)

            downloaded_ok = await self._download_media(message, raw_download_path, filename)
            if not downloaded_ok:
                await self._cleanup_file_async(raw_download_path)
                return None

            optimization_success = await self._optimize_media(raw_download_path, final_path, media_type)
            if optimization_success:
                if raw_download_path != final_path:
                    await self._cleanup_file_async(raw_download_path)
                return final_path
            else:
                logger.error(f"Media processing failed for msg {message.id}, type {media_type}")
                return None
        except Exception as e:
            logger.error(f"Error in media processing pipeline for msg {getattr(message, 'id', 'unknown')}: {e}", exc_info=True)
            return None

    async def _download_media(self, message: Message, raw_download_path: Path, filename: str) -> bool:
        try:
            async with self.download_semaphore:
                progress_callback = ProgressCallback(self.config, filename)
                await self.client.download_media(
                    message=message,
                    file=str(raw_download_path),
                    progress_callback=progress_callback
                )
                return await run_in_thread_pool(raw_download_path.exists)
        except Exception as e:
            logger.error(f"Download failed for {filename}: {e}", exc_info=True)
            return False

    async def _optimize_media(self, raw_path: Path, final_path: Path, media_type: str) -> bool:
        # Minimize debug logs: only log on error or if verbose
        try:
            if media_type == "image":
                await self._optimize_image(raw_path, final_path)
            elif media_type in ["video", "round_video"]:
                await self._optimize_video(raw_path, final_path)
            elif media_type == "audio":
                await self._optimize_audio(raw_path, final_path)
            else:
                await run_in_thread_pool(lambda: raw_path.rename(final_path))
            return await run_in_thread_pool(final_path.exists)
        except Exception as e:
            logger.error(f"Failed to process {media_type} {raw_path.name}: {e}")
            try:
                if await run_in_thread_pool(raw_path.exists):
                    logger.warning(f"Processing failed, attempting direct copy for {raw_path.name}")
                    await run_in_thread_pool(lambda: raw_path.rename(final_path))
                    return await run_in_thread_pool(final_path.exists)
            except Exception as e2:
                logger.error(f"Direct copy also failed for {raw_path.name}: {e2}")
            return False

    async def _cleanup_file_async(self, file_path: Path):
        try:
            if await run_in_thread_pool(file_path.exists):
                await run_in_thread_pool(file_path.unlink)
        except Exception as e:
            logger.warning(f"Could not clean up file {file_path}: {e}")

    def _get_filename(self, media_obj: Union[Photo, Document], message_id: int, media_type: str, entity_id_str: str) -> str:
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

    async def _optimize_image(self, input_path: Path, output_path: Path):
        await run_in_thread_pool(self._sync_optimize_image, input_path, output_path)

    def _sync_optimize_image(self, input_path: Path, output_path: Path):
        try:
            with Image.open(input_path) as img:
                img_to_save = img.convert('RGB')
                if img.mode in ('RGBA', 'P', 'LA'):
                    if 'transparency' in img.info or (img.mode in ('RGBA', 'LA') and any(p < 255 for p in img.getchannel('A').getdata())):
                        img_to_save = img.convert('RGBA')

                output_path = output_path.with_suffix('.webp')
                img_to_save.save(
                    output_path, "WEBP",
                    quality=self.config.image_quality, method=6
                )
        except UnidentifiedImageError:
            logger.error(f"Cannot identify image file: {input_path}")
            raise
        except Exception as e:
            logger.error(f"Image optimization failed: {e}")
            raise

    async def _optimize_video(self, input_path: Path, output_path: Path):
        await run_in_thread_pool(self._sync_optimize_video, input_path, output_path)

    def _sync_optimize_video(self, input_path: Path, output_path: Path):
        try:
            hw_acceleration = getattr(self.config, 'hw_acceleration', 'none').lower()
            use_h265 = getattr(self.config, 'use_h265', True)
            probe = ffmpeg.probe(str(input_path))
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                logger.warning(f"No video stream in {input_path.name}, copying directly.")
                ffmpeg.input(str(input_path)).output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True)
                return

            width = int(video_stream.get('width', 0))
            height = int(video_stream.get('height', 0))
            optimal_bitrate = self._calculate_optimal_bitrate(width, height)
            stream = ffmpeg.input(str(input_path))
            ffmpeg_options = {'pix_fmt': 'yuv420p', 'threads': '0', 'movflags': '+faststart'}
            base_crf = getattr(self.config, 'video_crf', 23)
            compression_crf = min(base_crf + 5, 35)

            if hw_acceleration == 'nvidia':
                self._configure_nvidia_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)
            elif hw_acceleration == 'amd':
                self._configure_amd_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)
            elif hw_acceleration == 'intel':
                self._configure_intel_encoder(ffmpeg_options, use_h265, optimal_bitrate)
            else:
                self._configure_software_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)

            if audio_stream := next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None):
                self._configure_audio_options(ffmpeg_options, audio_stream, float(video_stream.get('duration', 0)), 'voice' in input_path.name.lower())

            ffmpeg.output(stream, str(output_path), **ffmpeg_options).global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True)

            if output_path.exists() and input_path.exists() and output_path.stat().st_size >= input_path.stat().st_size:
                ffmpeg.input(str(input_path)).output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True)
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg failed for video {input_path.name}: {stderr}")
            print(f"❌ Failed to convert video {input_path.name} (see exporter.log for details)")
            print("   Try updating ffmpeg or check codec support.")
            return
        except Exception as e:
            logger.error(f"Video optimization failed for {input_path.name}: {e}")
            print(f"❌ Failed to process video {input_path.name} (see exporter.log for details)")
            return

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
        await run_in_thread_pool(self._sync_optimize_audio, input_path, output_path)

    def _sync_optimize_audio(self, input_path: Path, output_path: Path):
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
                ffmpeg.input(str(input_path)).audio.output(str(output_path), c='copy').global_args('-hide_banner', '-loglevel', 'fatal', '-nostats').run(capture_stderr=True, overwrite_output=True, quiet=True)
            except Exception as copy_err:
                logger.error(f"Fallback copy also failed: {copy_err}")
                import shutil
                shutil.copy2(input_path, output_path)
                print(f"❌ Failed to convert audio {input_path.name} (see exporter.log for details)")
                print("   Try updating ffmpeg or check codec support.")
                return
        except Exception as e:
            logger.error(f"Audio optimization failed for {input_path.name}: {e}")
            print(f"❌ Failed to process audio {input_path.name} (see exporter.log for details)")
            return
