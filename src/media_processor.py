import asyncio
import concurrent.futures
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Union
from PIL import Image, UnidentifiedImageError
import ffmpeg
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument, Message, Photo, Document,
    DocumentAttributeVideo, DocumentAttributeAudio, DocumentAttributeFilename
)
from telethon import TelegramClient

from src.config import Config
from src.utils import logger, ensure_dir_exists, sanitize_filename, run_in_thread_pool

class MediaProcessor:
    def __init__(self, config: Config, client: TelegramClient):
        self.config = config
        self.client = client
        self.download_semaphore = asyncio.Semaphore(config.concurrent_downloads)

        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.max_workers, thread_name_prefix="MediaThread"
        )

        self.processed_cache: Dict[Path, bool] = {}
        self._cache_lock = asyncio.Lock()

        logger.info(f"Media Processor initialized. Concurrent downloads: {config.concurrent_downloads}")

    async def download_and_optimize_media(
        self,
        message: Message,
        entity_id: Union[str, int],
        entity_media_path: Path
        ) -> List[Path]:
        """
        Downloads, optimizes (if applicable), and saves media for a message to the entity's specific media path.
        Returns a list of absolute Paths to the final media files.
        """
        if not self.config.media_download:
            return []

        media_items_to_process: List[Tuple[str, Union[Photo, Document]]] = []
        entity_id_str = str(entity_id)

        try:
            # Process this message's media
            await self._add_media_from_message(message, media_items_to_process, entity_id_str)

        except Exception as e:
            logger.warning(f"[Entity: {entity_id_str}] Error processing media for msg {message.id}: {e}.")
            return []

        if not media_items_to_process:
            return []

        tasks = []
        for media_type, media_obj in media_items_to_process:
            tasks.append(
                asyncio.create_task(
                    self._process_single_item(
                        message.id, entity_id_str, media_type, media_obj, entity_media_path
                    )
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_paths = []
        for i, result in enumerate(results):
            media_type, media_obj = media_items_to_process[i]
            media_id = getattr(media_obj, 'id', 'unknown')
            if isinstance(result, Exception):
                logger.error(f"[Entity: {entity_id_str}] Failed to process {media_type} (ID: {media_id}) for msg {message.id}: {result}", exc_info=False)
            elif result and isinstance(result, Path):
                final_paths.append(result)

        return final_paths

    async def _add_media_from_message(self, message: Message, media_items: List[Tuple[str, Union[Photo, Document]]], entity_id_str: str):
        """Helper method to extract and add media items from a message to the processing list"""
        if not hasattr(message, 'media') or not message.media:
            return

        # Handle single photo
        if isinstance(message.media, MessageMediaPhoto):
            if hasattr(message.media, 'photo') and isinstance(message.media.photo, Photo):
                media_items.append(("image", message.media.photo))

        # Handle single document (video, audio, etc)
        elif isinstance(message.media, MessageMediaDocument) and hasattr(message.media, 'document'):
            doc = message.media.document
            if isinstance(doc, Document) and hasattr(doc, 'attributes'):
                media_type = self._get_document_type(doc)
                media_items.append((media_type, doc))

        # Log what we found
        if media_items:
            logger.debug(f"[Entity: {entity_id_str}] Found {len(media_items)} media items in message {message.id}")

    def _get_document_type(self, doc: Document) -> str:
        """Determines the type (video, audio, document, etc.) from Document attributes."""
        is_video = any(isinstance(attr, DocumentAttributeVideo) for attr in doc.attributes)
        is_audio = any(isinstance(attr, DocumentAttributeAudio) for attr in doc.attributes)
        is_round = False
        if is_video:
            video_attr = next((attr for attr in doc.attributes if isinstance(attr, DocumentAttributeVideo)), None)
            if video_attr and getattr(video_attr, 'round_message', False):
                 is_round = True

        if is_round: return "round_video"
        if is_video: return "video"
        if is_audio: return "audio"
        return "document"

    async def _process_single_item(
        self,
        message_id: int,
        entity_id_str: str,
        media_type: str,
        media_obj: Union[Photo, Document],
        entity_media_path: Path
    ) -> Optional[Path]:
        """Handles download, optimization, and caching for one media item."""

        try:
            filename = self._get_filename(media_obj, message_id, media_type, entity_id_str)
            type_subdir = entity_media_path / f"{media_type}s"
            final_path = type_subdir / filename
            raw_download_path = type_subdir / f"raw_{filename}"
        except Exception as e:
            media_id = getattr(media_obj, 'id', 'unknown')
            logger.error(f"[Entity: {entity_id_str}] Error generating filename for {media_type} ID {media_id}, msg {message_id}: {e}")
            return None

        async with self._cache_lock:
            if final_path in self.processed_cache and final_path.exists():
                return final_path

        await run_in_thread_pool(ensure_dir_exists, type_subdir)

        downloaded_ok = False
        try:
            async with self.download_semaphore:
                logger.info(f"[{entity_id_str}] Downloading {media_type} for msg {message_id} -> {raw_download_path.name}...")

                # Don't create dummy message - simply use the media object directly

                # Convert Path to string for telethon
                download_result = await self.client.download_media(
                    message=media_obj,
                    file=str(raw_download_path)
                )
                downloaded_ok = download_result is not None and await run_in_thread_pool(raw_download_path.exists)
                if not downloaded_ok:
                     logger.warning(f"[{entity_id_str}] Download completed but raw file missing: {raw_download_path}")

        except asyncio.TimeoutError:
             logger.error(f"[{entity_id_str}] Timeout downloading media for msg {message_id}")
        except Exception as download_err:
            logger.error(f"[{entity_id_str}] Download failed for msg {message_id} ({media_type}): {download_err}", exc_info=False)
            await self._cleanup_file_async(raw_download_path)
            return None

        if not downloaded_ok:
            await self._cleanup_file_async(raw_download_path)
            return None

        optimization_success = False
        try:
            logger.info(f"[{entity_id_str}] Processing {media_type}: {raw_download_path.name} -> {final_path.name}")
            if media_type == "image":
                await self._optimize_image(raw_download_path, final_path)
            elif media_type in ["video", "round_video"]:
                await self._optimize_video(raw_download_path, final_path)
            else:
                await run_in_thread_pool(lambda: raw_download_path.rename(final_path))

            optimization_success = await run_in_thread_pool(final_path.exists)

        except Exception as process_err:
            logger.error(f"[{entity_id_str}] Failed to process {media_type} {raw_download_path.name}: {process_err}", exc_info=False)
            if await run_in_thread_pool(raw_download_path.exists):
                 logger.warning(f"[{entity_id_str}] Processing failed, attempting direct copy: {raw_download_path.name} -> {final_path.name}")
                 try:
                     await run_in_thread_pool(lambda: raw_download_path.rename(final_path))
                     optimization_success = await run_in_thread_pool(final_path.exists)
                 except Exception as move_err:
                     logger.error(f"[{entity_id_str}] Fallback move/copy failed for {raw_download_path.name}: {move_err}")

        if optimization_success and raw_download_path != final_path:
            await self._cleanup_file_async(raw_download_path)
        elif await run_in_thread_pool(raw_download_path.exists) and not optimization_success:
             logger.warning(f"[{entity_id_str}] Processing failed and final file missing. Raw file kept: {raw_download_path}")
             return None

        if optimization_success:
             logger.info(f"[{entity_id_str}] Finished processing media: {final_path}")
             async with self._cache_lock:
                 self.processed_cache[final_path] = True
             return final_path
        else:
            logger.error(f"[{entity_id_str}] Media processing ultimately failed for msg {message_id}, type {media_type}. No file available.")
            return None

    async def _cleanup_file_async(self, file_path: Path):
        """Safely removes a file asynchronously if it exists."""
        try:
            def _remove_if_exists(p: Path):
                if p.exists():
                    p.unlink()
                    logger.debug(f"Cleaned up temporary file: {p}")
            await run_in_thread_pool(_remove_if_exists, file_path)
        except Exception as e:
            logger.warning(f"Could not clean up file {file_path}: {e}")

    def _get_filename(self, media_obj: Union[Photo, Document], message_id: int, media_type: str, entity_id_str: str) -> str:
        """Generates a unique and sanitized filename for the media."""
        media_id = getattr(media_obj, 'id', 'no_id')

        base_name = f"{entity_id_str}_msg{message_id}_{media_type}_{media_id}"

        ext = ".dat"

        if isinstance(media_obj, Photo):
            ext = ".jpg"
        elif isinstance(media_obj, Document):
            original_filename = None
            for attr in getattr(media_obj, 'attributes', []):
                if isinstance(attr, DocumentAttributeFilename):
                    original_filename = attr.file_name
                    break

            if original_filename:
                original_path = Path(original_filename)
                if original_path.suffix and len(original_path.suffix) > 1:
                    ext = original_path.suffix
                else:
                     if hasattr(media_obj, 'mime_type') and '/' in media_obj.mime_type:
                         mime_suffix = media_obj.mime_type.split('/')[-1].split(';')[0]
                         if mime_suffix:
                            ext = f".{mime_suffix}"
            elif hasattr(media_obj, 'mime_type') and '/' in media_obj.mime_type:
                mime_suffix = media_obj.mime_type.split('/')[-1].split(';')[0]
                if mime_suffix:
                     ext = f".{mime_suffix}"

            if media_type in ['video', 'round_video']:
                if not ext or ext.lower() in ['.dat', '.bin']:
                    ext = '.mp4'
            elif media_type == 'audio':
                 is_voice = any(getattr(attr, 'voice', False) for attr in media_obj.attributes if isinstance(attr, DocumentAttributeAudio))
                 if is_voice and (not ext or ext.lower() in ['.dat', '.bin', '.oga']):
                     ext = '.ogg'

        safe_base = sanitize_filename(base_name, max_length=180, replacement='_')
        safe_ext = sanitize_filename(ext, max_length=10, replacement='')
        if not safe_ext.startswith('.'): safe_ext = '.' + safe_ext
        if len(safe_ext) <= 1: safe_ext = ".dat"

        return f"{safe_base}{safe_ext}"

    async def _optimize_image(self, input_path: Path, output_path: Path):
        """Optimizes image using Pillow. Runs sync logic in thread pool."""
        await run_in_thread_pool(
            self._sync_optimize_image, input_path, output_path
        )

    def _sync_optimize_image(self, input_path: Path, output_path: Path):
        """Synchronous image optimization logic using Pillow with advanced optimization."""
        try:
            with Image.open(input_path) as img:
                img_format = img.format
                logger.debug(f"Optimizing image {input_path.name} (Format: {img_format}, Mode: {img.mode})")

                # Process image based on its mode
                has_alpha = False
                if img.mode in ('RGBA', 'P', 'LA'):
                    try:
                        if img.mode in ('RGBA', 'LA'):
                            alpha = img.getchannel('A')
                            has_alpha = any(p < 255 for p in alpha.getdata())
                        elif img.mode == 'P' and 'transparency' in img.info:
                            has_alpha = True

                        if has_alpha:
                            logger.debug(f"Image {input_path.name} has transparency, preserving alpha channel.")
                            img_to_save = img.convert('RGBA')
                        else:
                            if img.mode != 'RGB':
                                img_to_save = img.convert('RGB')
                            else:
                                img_to_save = img
                    except Exception as convert_err:
                        logger.warning(f"Error during transparency handling/conversion for {input_path.name}: {convert_err}. Saving as is.")
                        img_to_save = img
                else:
                    img_to_save = img

                # Create a temporary WebP path
                webp_path = output_path.with_suffix('.webp')

                # Save as WebP for better compression
                img_to_save.save(
                    webp_path,
                    "WEBP",
                    quality=self.config.image_quality,
                    method=6  # Higher quality compression (0-6)
                )
                logger.debug(f"Saved optimized WebP image to {webp_path.name}")

                # Rename the WebP file back to the original format extension
                webp_path.rename(output_path)
                logger.debug(f"Renamed WebP to original format {output_path.name} for compatibility")

        except UnidentifiedImageError:
             logger.error(f"Cannot identify image file (corrupted or unsupported format): {input_path}. Skipping optimization.")
             raise
        except Exception as e:
            logger.error(f"Pillow optimization failed for {input_path}: {e}")
            raise

    async def _optimize_video(self, input_path: Path, output_path: Path):
        """Optimizes video using ffmpeg. Runs sync logic in an executor."""
        await run_in_thread_pool(
            self._sync_optimize_video, input_path, output_path
        )

    def _sync_optimize_video(self, input_path: Path, output_path: Path):
        """Synchronous video optimization logic using ffmpeg-python."""
        try:
            logger.debug(f"Optimizing video {input_path.name} with CRF={self.config.video_crf}, Preset={self.config.video_preset}")

            # Get video information first to make intelligent decisions
            probe = ffmpeg.probe(str(input_path))
            video_stream = next((stream for stream in probe['streams']
                                if stream['codec_type'] == 'video'), None)

            if not video_stream:
                logger.warning(f"No video stream found in {input_path.name}, copying file directly")
                stream = ffmpeg.input(str(input_path))
                stream = ffmpeg.output(stream, str(output_path), c='copy')
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
                return


            # Input stream
            stream = ffmpeg.input(str(input_path))

            # Base optimization options
            ffmpeg_options = {
                'c:v': 'libx264',
                'crf': str(self.config.video_crf),
                'preset': self.config.video_preset,
                'pix_fmt': 'yuv420p',
                'threads': '0'
            }

            # Add advanced encoding parameters
            ffmpeg_options.update({
                # These flags help with compression efficiency
                'profile:v': 'high',
                'level': '4.1',
                'tune': 'film',  # Optimize for general film content (most Telegram videos)
                'maxrate': '2M',
                'bufsize': '4M'
            })

            # Detect if video has audio stream
            audio_stream = next((stream for stream in probe['streams']
                               if stream['codec_type'] == 'audio'), None)

            if audio_stream:
                # Audio optimization - re-encode only if it would save space
                audio_codec = audio_stream.get('codec_name', '').lower()
                if audio_codec in ['pcm_s16le', 'pcm_s24le', 'pcm_f32le', 'flac']:
                    # Convert lossless audio to AAC with good quality
                    ffmpeg_options.update({
                        'c:a': 'aac',
                        'b:a': '128k',
                    })
                else:
                    # Copy audio stream for already compressed formats
                    ffmpeg_options['c:a'] = 'copy'

            # Apply all options
            stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)

            # Execute ffmpeg
            stdout, stderr = ffmpeg.run(stream, capture_stdout=False, capture_stderr=True, overwrite_output=True)

            logger.debug(f"ffmpeg optimization successful for {output_path.name}")

        except ffmpeg.Error as e:
            stderr_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg execution failed for {input_path.name}:\n{stderr_output}")
            raise
        except Exception as e:
            logger.error(f"Video optimization failed unexpectedly for {input_path.name}: {e}")
            raise
