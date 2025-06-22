import asyncio
import concurrent.futures
import mimetypes
import os
from pathlib import Path
from typing import List, Optional, Union

import aiofiles
import aiohttp
import ffmpeg
from PIL import Image, UnidentifiedImageError
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
        logger.info(f"Media Processor initialized. Concurrent downloads: {config.concurrent_downloads}")

    async def download_and_optimize_media(
            self, message: Message, entity_id: Union[str, int], entity_media_path: Path
    ) -> List[Path]:
        if not self.config.media_download:
            return []

        media_items = []
        entity_id_str = str(entity_id)

        try:
            await self._add_media_from_message(message, media_items, entity_id_str)
        except Exception as e:
            logger.warning(f"[Entity: {entity_id_str}] Error processing media for msg {message.id}: {e}.")
            return []

        if not media_items:
            return []

        tasks = [
            asyncio.create_task(self._process_single_item(
                message.id, entity_id_str, media_type, media_obj, entity_media_path
            ))
            for media_type, media_obj in media_items
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_paths = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                media_type, media_obj = media_items[i]
                media_id = getattr(media_obj, 'id', 'unknown')
                logger.error(f"[Entity: {entity_id_str}] Failed to process {media_type} (ID: {media_id}): {result}")
            elif result and isinstance(result, Path):
                final_paths.append(result)

        return final_paths

    async def download_external_image(self, session: aiohttp.ClientSession, url: str, media_path: Path) -> Optional[Path]:
            """Асинхронно скачивает внешнее изображение (например, из Telegra.ph)."""
            try:
                async with self.download_semaphore:
                    logger.debug(f"Downloading external image from: {url}")
                    async with session.get(url, timeout=30) as response:
                        if response.status != 200:
                            logger.error(f"Failed to download image {url}. Status: {response.status}, Reason: {response.reason}")
                            return None

                        content_type = response.headers.get('Content-Type', '')
                        ext = mimetypes.guess_extension(content_type) or '.jpg'

                        base_name = sanitize_filename(Path(url).stem)
                        filename = f"telegraph_{base_name}_{os.urandom(4).hex()}{ext}"

                        images_dir = media_path / "images"
                        await run_in_thread_pool(ensure_dir_exists, images_dir)

                        file_path = images_dir / filename

                        async with aiofiles.open(file_path, 'wb') as f:
                            await f.write(await response.read())

                        logger.info(f"Saved external image to: {file_path}")
                        return file_path
            except Exception as e:
                logger.error(f"Failed to download or save external image {url}: {e}", exc_info=True)
                return None

    async def _add_media_from_message(self, message: Message, media_items: List, entity_id_str: str):
        if not hasattr(message, 'media') or not message.media:
            return

        if isinstance(message.media, MessageMediaPhoto) and hasattr(message.media, 'photo') and isinstance(message.media.photo, Photo):
            media_items.append(("image", message.media.photo))
        elif isinstance(message.media, MessageMediaDocument) and hasattr(message.media, 'document'):
            doc = message.media.document
            if isinstance(doc, Document) and hasattr(doc, 'attributes'):
                media_type = self._get_document_type(doc)
                media_items.append((media_type, doc))

    def _get_document_type(self, doc: Document) -> str:
        is_video = any(isinstance(attr, DocumentAttributeVideo) for attr in doc.attributes)
        is_audio = any(isinstance(attr, DocumentAttributeAudio) for attr in doc.attributes)

        if is_video:
            video_attr = next((attr for attr in doc.attributes if isinstance(attr, DocumentAttributeVideo)), None)
            return "round_video" if video_attr and getattr(video_attr, 'round_message', False) else "video"
        return "audio" if is_audio else "document"

    async def _process_single_item(
            self, message_id: int, entity_id_str: str, media_type: str,
            media_obj: Union[Photo, Document], entity_media_path: Path
    ) -> Optional[Path]:
        try:
            filename = self._get_filename(media_obj, message_id, media_type, entity_id_str)
            type_subdir = entity_media_path / f"{media_type}s"
            final_path = type_subdir / filename
            raw_download_path = type_subdir / f"raw_{filename}"

            async with self._cache_lock:
                if final_path in self.processed_cache and final_path.exists():
                    return final_path

            await run_in_thread_pool(ensure_dir_exists, type_subdir)

            downloaded_ok = await self._download_media(
                message_id, entity_id_str, media_type, media_obj, raw_download_path
            )
            if not downloaded_ok:
                await self._cleanup_file_async(raw_download_path)
                return None

            optimization_success = await self._optimize_media(
                raw_download_path, final_path, media_type, entity_id_str
            )

            if optimization_success:
                if raw_download_path != final_path:
                    await self._cleanup_file_async(raw_download_path)
                async with self._cache_lock:
                    self.processed_cache[final_path] = True
                logger.info(f"Finished processing media: {final_path}")
                return final_path
            else:
                logger.error(f"[{entity_id_str}] Media processing failed for msg {message_id}, type {media_type}")
                return None
        except Exception as e:
            logger.error(f"[{entity_id_str}] Error in media processing pipeline: {e}")
            return None

    async def _download_media(
            self, message_id: int, entity_id_str: str, media_type: str,
            media_obj: Union[Photo, Document], raw_download_path: Path
    ) -> bool:
        try:
            async with self.download_semaphore:
                logger.info(f"[{entity_id_str}] Downloading {media_type} for msg {message_id} -> {raw_download_path.name}...")
                await run_in_thread_pool(ensure_dir_exists, raw_download_path.parent)
                download_result = await self.client.download_media(media_obj, file=str(raw_download_path))
                return download_result is not None and await run_in_thread_pool(raw_download_path.exists)
        except Exception as e:
            logger.error(f"[{entity_id_str}] Download failed for msg {message_id} ({media_type}): {e}")
            return False

    async def _optimize_media(
            self, raw_path: Path, final_path: Path, media_type: str, entity_id_str: str
    ) -> bool:
        logger.info(f"[{entity_id_str}] Processing {media_type}: {raw_path.name} -> {final_path.name}")
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
            logger.error(f"[{entity_id_str}] Failed to process {media_type} {raw_path.name}: {e}")
            try:
                if await run_in_thread_pool(raw_path.exists):
                    logger.warning(f"[{entity_id_str}] Processing failed, attempting direct copy")
                    await run_in_thread_pool(lambda: raw_path.rename(final_path))
                    return await run_in_thread_pool(final_path.exists)
            except Exception as move_err:
                logger.error(f"[{entity_id_str}] Fallback move/copy failed: {move_err}")
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
            original_filename = next((
                attr.file_name for attr in getattr(media_obj, 'attributes', [])
                if isinstance(attr, DocumentAttributeFilename)
            ), None)
            if original_filename and Path(original_filename).suffix:
                ext = Path(original_filename).suffix
            elif hasattr(media_obj, 'mime_type') and '/' in media_obj.mime_type:
                ext = mimetypes.guess_extension(media_obj.mime_type) or ext

            if media_type in ['video', 'round_video'] and ext.lower() in ['.dat', '.bin']:
                ext = '.mp4'
            elif media_type == 'audio':
                is_voice = any(getattr(attr, 'voice', False) for attr in media_obj.attributes if isinstance(attr, DocumentAttributeAudio))
                if is_voice and ext.lower() in ['.dat', '.bin', '.oga']:
                    ext = '.ogg'

        safe_base = sanitize_filename(base_name, max_length=180, replacement='_')
        safe_ext = sanitize_filename(ext, max_length=10, replacement='')
        if not safe_ext.startswith('.'): safe_ext = '.' + safe_ext
        return f"{safe_base}{safe_ext}"


    async def _optimize_image(self, input_path: Path, output_path: Path):
        await run_in_thread_pool(self._sync_optimize_image, input_path, output_path)

    def _sync_optimize_image(self, input_path: Path, output_path: Path):
        try:
            with Image.open(input_path) as img:
                has_alpha = False
                if img.mode in ('RGBA', 'P', 'LA'):
                    try:
                        if img.mode in ('RGBA', 'LA'):
                            alpha = img.getchannel('A')
                            has_alpha = any(p < 255 for p in alpha.getdata())
                        elif img.mode == 'P' and 'transparency' in img.info:
                            has_alpha = True

                        img_to_save = img.convert('RGBA') if has_alpha else img.convert('RGB')
                    except Exception:
                        img_to_save = img
                else:
                    img_to_save = img

                webp_path = output_path.with_suffix('.webp')
                img_to_save.save(
                    webp_path,
                    "WEBP",
                    quality=self.config.image_quality,
                    method=6
                )

                webp_path.rename(output_path)

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
            # Get video configuration
            hw_acceleration = getattr(self.config, 'hw_acceleration', 'none').lower()
            use_h265 = getattr(self.config, 'use_h265', True)

            # Get video information
            probe = ffmpeg.probe(str(input_path))
            video_stream = next((
                stream for stream in probe['streams']
                if stream['codec_type'] == 'video'
            ), None)

            # If no video stream, just copy the file
            if not video_stream:
                logger.warning(f"No video stream found in {input_path.name}, copying file directly")
                stream = ffmpeg.input(str(input_path))
                stream = ffmpeg.output(stream, str(output_path), c='copy')
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
                return

            # Calculate optimal bitrate based on resolution
            width = int(video_stream.get('width', 0))
            height = int(video_stream.get('height', 0))
            optimal_bitrate = self._calculate_optimal_bitrate(width, height)

            # Create input stream
            stream = ffmpeg.input(str(input_path))

            # Base ffmpeg options
            ffmpeg_options = {
                'pix_fmt': 'yuv420p',
                'threads': '0',
                'movflags': '+faststart',
            }

            # Compression quality settings
            base_crf = getattr(self.config, 'video_crf', 23)
            compression_crf = min(base_crf + 5, 35)

            # Configure encoder based on hardware acceleration option
            if hw_acceleration == 'nvidia':
                self._configure_nvidia_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)
            elif hw_acceleration == 'amd':
                self._configure_amd_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)
            elif hw_acceleration == 'intel':
                self._configure_intel_encoder(ffmpeg_options, use_h265, optimal_bitrate)
            else:
                self._configure_software_encoder(ffmpeg_options, use_h265, compression_crf, optimal_bitrate)

            # Handle audio stream if present
            audio_stream = next((
                stream for stream in probe['streams']
                if stream['codec_type'] == 'audio'
            ), None)

            if audio_stream:
                self._configure_audio_options(
                    ffmpeg_options,
                    audio_stream,
                    float(video_stream.get('duration', 0)),
                    'voice' in input_path.name.lower()
                )

            # Run ffmpeg
            stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)
            ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)

            # If output is not smaller than input, just use the original
            if output_path.exists() and input_path.exists():
                input_size = input_path.stat().st_size
                output_size = output_path.stat().st_size

                if output_size >= input_size:
                    logger.info(f"Optimized file {output_size} not smaller than original {input_size}. Using original.")
                    ffmpeg.input(str(input_path)).output(str(output_path), c='copy').run(
                        capture_stderr=True, overwrite_output=True
                    )

        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg failed: {stderr}")
            raise
        except Exception as e:
            logger.error(f"Video optimization failed: {e}")
            raise

    def _configure_nvidia_encoder(self, options, use_h265, crf, bitrate):
        if use_h265:
            options.update({
                'c:v': 'hevc_nvenc',
                'preset': 'p6',
                'rc:v': 'vbr_hq',
                'cq': str(crf),
                'b:v': bitrate,
                'spatial-aq': '1',
                'temporal-aq': '1'
            })
        else:
            options.update({
                'c:v': 'h264_nvenc',
                'preset': 'p7',
                'rc:v': 'vbr_hq',
                'cq': str(crf),
                'b:v': bitrate,
                'spatial-aq': '1',
                'temporal-aq': '1'
            })

    def _configure_amd_encoder(self, options, use_h265, crf, bitrate):
        if use_h265:
            options.update({
                'c:v': 'hevc_amf',
                'quality': 'quality',
                'qp_i': str(crf),
                'qp_p': str(crf + 2),
                'bitrate': bitrate.replace('k', '000')
            })
        else:
            options.update({
                'c:v': 'h264_amf',
                'quality': 'quality',
                'qp_i': str(crf),
                'qp_p': str(crf + 2),
                'bitrate': bitrate.replace('k', '000')
            })

    def _configure_intel_encoder(self, options, use_h265, bitrate):
        if use_h265:
            options.update({
                'c:v': 'hevc_qsv',
                'preset': 'slower',
                'b:v': bitrate,
                'look_ahead': '1'
            })
        else:
            options.update({
                'c:v': 'h264_qsv',
                'preset': 'slower',
                'b:v': bitrate,
                'look_ahead': '1'
            })

    def _configure_software_encoder(self, options, use_h265, crf, bitrate):
        if use_h265:
            options.update({
                'c:v': 'libx265',
                'crf': str(crf),
                'preset': self.config.video_preset,
                'x265-params': "profile=main:level=5.1:no-sao=1:bframes=8:rd=4:psy-rd=1.0:"
                               "rect=1:aq-mode=3:aq-strength=0.8:deblock=-1:-1",
                'maxrate': bitrate,
                'bufsize': f"{int(bitrate.replace('k', '')) * 2}k"
            })
        else:
            options.update({
                'c:v': 'libx264',
                'crf': str(crf),
                'preset': self.config.video_preset,
                'profile:v': 'high',
                'level': '4.1',
                'tune': 'film',
                'subq': '9',
                'trellis': '2',
                'partitions': 'all',
                'direct-pred': 'auto',
                'me_method': 'umh',
                'g': '250',
                'maxrate': bitrate,
                'bufsize': f"{int(bitrate.replace('k', '')) * 2}k"
            })

    def _configure_audio_options(self, options, audio_stream, duration, is_voice_hint):
        audio_bitrate = self._calculate_audio_bitrate(
            audio_stream.get('bit_rate'),
            audio_stream.get('channels', 2)
        )

        options.update({
            'c:a': 'aac',
            'b:a': audio_bitrate,
            'ar': '44100',
            'ac': '2'
        })

        # Adjust for voice audio
        is_voice = is_voice_hint or duration > 0
        if is_voice:
            options['b:a'] = '64k'
            options['ac'] = '1'

    def _calculate_optimal_bitrate(self, width: int, height: int) -> str:
        pixels = width * height

        if pixels <= 0:
            return "500k"
        elif pixels >= 2073600:  # 1080p
            return "1500k"
        elif pixels >= 921600:   # 720p
            return "800k"
        elif pixels >= 409920:   # 480p
            return "500k"
        else:
            return "400k"

    def _calculate_audio_bitrate(self, current_bitrate, channels: int) -> str:
        if not current_bitrate:
            return "96k" if channels > 1 else "64k"

        try:
            if isinstance(current_bitrate, str):
                current_bitrate = int(current_bitrate)

            if current_bitrate > 320000:
                return "128k"
            elif current_bitrate > 128000:
                return "96k"
            else:
                return "64k"
        except Exception:
            return "96k"

    async def _optimize_audio(self, input_path: Path, output_path: Path):
        await run_in_thread_pool(self._sync_optimize_audio, input_path, output_path)

    def _sync_optimize_audio(self, input_path: Path, output_path: Path):
        try:
            # Get audio information
            probe = ffmpeg.probe(str(input_path))
            audio_stream = next((
                stream for stream in probe['streams']
                if stream['codec_type'] == 'audio'
            ), None)

            # If no audio stream, just copy the file
            if not audio_stream:
                logger.warning(f"No audio stream found in {input_path.name}, copying file directly")
                stream = ffmpeg.input(str(input_path))
                stream = ffmpeg.output(stream, str(output_path), c='copy')
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
                return

            # Get audio parameters
            channels = int(audio_stream.get('channels', 2))
            sample_rate = int(audio_stream.get('sample_rate', 48000))
            codec_name = audio_stream.get('codec_name', '').lower()

            # Calculate optimal bitrate
            optimal_bitrate = self._calculate_audio_bitrate(
                audio_stream.get('bit_rate'),
                channels
            )

            # Determine output format
            output_format = output_path.suffix.lower().lstrip('.')
            if not output_format or output_format not in ['mp3', 'ogg', 'm4a', 'aac']:
                output_format = 'mp3'

            # Create input stream with audio only to avoid attachment issues
            stream = ffmpeg.input(str(input_path)).audio

            # Base ffmpeg options
            ffmpeg_options = {'b:a': optimal_bitrate}

            # Sample rate handling
            if output_format in ['ogg', 'oga'] and codec_name == 'opus':
                opus_supported_rates = [8000, 12000, 16000, 24000, 48000]
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate in opus_supported_rates else '48000'
            else:
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate else '44100'

            # Format-specific encoding options
            if output_format == 'mp3':
                ffmpeg_options.update({
                    'c:a': 'libmp3lame',
                    'q:a': '4',
                    'compression_level': '9'
                })
            elif output_format in ['ogg', 'oga']:
                if codec_name == 'opus':
                    ffmpeg_options.update({
                        'c:a': 'libopus',
                        'b:a': '32k' if channels == 1 else '64k',
                        'vbr': 'on',
                        'compression_level': '10'
                    })
                else:
                    ffmpeg_options.update({
                        'c:a': 'libvorbis',
                        'q:a': '3'
                    })
            elif output_format in ['m4a', 'aac']:
                ffmpeg_options.update({
                    'c:a': 'aac',
                    'q:a': '1'
                })

            # Voice detection and optimization
            is_voice = (
                'voice' in input_path.name.lower() or
                codec_name in ['opus', 'speex'] or
                any(getattr(attr, 'voice', False) for attr in getattr(audio_stream, 'disposition', []))
            )

            if is_voice:
                if output_format == 'ogg':
                    ffmpeg_options.update({
                        'c:a': 'libopus',
                        'b:a': '32k',
                        'vbr': 'on',
                        'compression_level': '10',
                        'application': 'voip'
                    })
                else:
                    ffmpeg_options.update({
                        'b:a': '48k',
                        'ac': '1'
                    })

            # Run ffmpeg
            try:
                stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
            except ffmpeg.Error as e:
                # Check if the error might be related to container/codec incompatibility
                if 'incorrect codec parameters' in e.stderr.decode('utf-8', errors='ignore'):
                    logger.warning(f"Codec parameter issue detected, trying simpler encoding for {input_path.name}")
                    stream = ffmpeg.input(str(input_path)).audio
                    stream = ffmpeg.output(stream, str(output_path), c='aac' if output_format in ['m4a', 'aac'] else 'libmp3lame')
                    ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
                else:
                    raise

            # If output is not smaller than input, just use the original
            if output_path.exists() and input_path.exists():
                input_size = input_path.stat().st_size
                output_size = output_path.stat().st_size

                if output_size >= input_size:
                    logger.info(f"Optimized file {output_size} not smaller than original {input_size}. Using original.")
                    stream = ffmpeg.input(str(input_path)).audio
                    stream = ffmpeg.output(stream, str(output_path), c='copy')
                    ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)

        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg failed for audio: {stderr}")
            try:
                logger.info(f"Falling back to direct copy for {input_path.name}")
                stream = ffmpeg.input(str(input_path)).audio
                stream = ffmpeg.output(stream, str(output_path), c='copy')
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
            except Exception as copy_err:
                logger.error(f"Fallback copy also failed: {copy_err}")
                # Last-ditch effort - try raw copy without ffmpeg
                try:
                    import shutil
                    shutil.copy2(input_path, output_path)
                    logger.info(f"Used direct file copy as last resort for {input_path.name}")
                except Exception:
                    raise e
        except Exception as e:
            logger.error(f"Audio optimization failed: {e}")
            raise
