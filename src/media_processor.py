import asyncio
import concurrent.futures
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Union, Any
from PIL import Image, UnidentifiedImageError
import ffmpeg # ffmpeg-python
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument, Message, Photo, Document,
    DocumentAttributeVideo, DocumentAttributeAudio, DocumentAttributeFilename
)
from telethon import TelegramClient # Only for type hint

from src.config import Config
from src.utils import logger, ensure_dir_exists, sanitize_filename, run_in_thread_pool

class MediaProcessor:
    def __init__(self, config: Config, client: TelegramClient):
        self.config = config
        self.client = client # Keep client reference for downloading
        self.download_semaphore = asyncio.Semaphore(config.concurrent_downloads)

        # Thread pool for I/O bound tasks (like non-PIL optimization, file moves) and PIL image saving
        # Use default executor configured in main.py or create one here
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.max_workers, thread_name_prefix="MediaThread"
        )
        # Process pool for CPU-intensive ffmpeg or potentially heavy PIL operations
        # Ensure it's managed properly (e.g., shutdown in main.py) or use context manager if sync
        # Using loop.run_in_executor with global executor is preferred in async app
        # Let's assume global process_executor exists from main.py for ffmpeg if needed,
        # otherwise fallback to thread_pool for simplicity/robustness.

        # Using a simple dict for caching processed files (absolute path -> True)
        # More robust caching could involve checking file size/mtime
        self.processed_cache: Dict[Path, bool] = {}
        # Lock for accessing the processed_cache
        self._cache_lock = asyncio.Lock()

        logger.info(f"Media Processor initialized. Concurrent downloads: {config.concurrent_downloads}")

    async def download_and_optimize_media(
        self,
        message: Message,
        entity_id: Union[str, int],
        entity_media_path: Path # Base path for THIS entity's media
        ) -> List[Path]:
        """
        Downloads, optimizes (if applicable), and saves media for a message to the entity's specific media path.
        Returns a list of absolute Paths to the final media files.
        """
        if not self.config.media_download or not message.media:
            return []

        media_items_to_process: List[Tuple[str, Union[Photo, Document]]] = []
        entity_id_str = str(entity_id) # Consistent string ID

        # --- Gather Media Items from Message ---
        try:
            if isinstance(message.media, MessageMediaPhoto) and hasattr(message, 'photo'):
                if isinstance(message.photo, Photo):
                    media_items_to_process.append(("image", message.photo))
            elif isinstance(message.media, MessageMediaDocument) and hasattr(message.media, 'document'):
                doc = message.media.document
                if isinstance(doc, Document) and hasattr(doc, 'attributes'):
                    media_type = self._get_document_type(doc)
                    media_items_to_process.append((media_type, doc))
            # Add handling for other types like MessageMediaWebPage (thumbnails), Geo, etc. if needed
        except AttributeError as e:
             logger.warning(f"[Entity: {entity_id_str}] Error accessing media attribute for msg {message.id}: {e}. Skipping media.")
             return []


        if not media_items_to_process:
            return []

        # --- Process gathered media items in parallel ---
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
                logger.error(f"[Entity: {entity_id_str}] Failed to process {media_type} (ID: {media_id}) for msg {message.id}: {result}", exc_info=False) # Log exception simple
            elif result and isinstance(result, Path):
                final_paths.append(result)
            # else: result was None (e.g., download failed, skipped) - already logged internally

        return final_paths

    def _get_document_type(self, doc: Document) -> str:
        """Determines the type (video, audio, document, etc.) from Document attributes."""
        is_video = any(isinstance(attr, DocumentAttributeVideo) for attr in doc.attributes)
        is_audio = any(isinstance(attr, DocumentAttributeAudio) for attr in doc.attributes)
        is_round = False
        if is_video:
             # Check for round video attribute specifically
            video_attr = next((attr for attr in doc.attributes if isinstance(attr, DocumentAttributeVideo)), None)
            if video_attr and getattr(video_attr, 'round_message', False):
                 is_round = True

        if is_round: return "round_video"
        if is_video: return "video"
        if is_audio: return "audio"
        # Could add checks for GIF (DocumentAttributeAnimated), Sticker, Voice here if needed
        return "document"


    async def _process_single_item(
        self,
        message_id: int,
        entity_id_str: str,
        media_type: str,
        media_obj: Union[Photo, Document],
        entity_media_path: Path # e.g., /path/to/export/entity_X/_media
    ) -> Optional[Path]:
        """Handles download, optimization, and caching for one media item."""

        # 1. Determine Filename and Paths
        try:
            filename = self._get_filename(media_obj, message_id, media_type, entity_id_str)
            # Subdirectory within entity_media_path based on type
            type_subdir = entity_media_path / f"{media_type}s" # e.g., .../_media/images
            final_path = type_subdir / filename
            raw_download_path = type_subdir / f"raw_{filename}" # Temporary download path
        except Exception as e:
            media_id = getattr(media_obj, 'id', 'unknown')
            logger.error(f"[Entity: {entity_id_str}] Error generating filename for {media_type} ID {media_id}, msg {message_id}: {e}")
            return None

        # Check cache / existence before download
        async with self._cache_lock:
            if final_path in self.processed_cache and final_path.exists():
                # logger.debug(f"[{entity_id_str}] Media already processed (cached): {final_path}")
                return final_path

        # Ensure directory exists (run in thread pool)
        await run_in_thread_pool(ensure_dir_exists, type_subdir)

        # 2. Download with Semaphore
        downloaded_ok = False
        try:
            async with self.download_semaphore:
                logger.info(f"[{entity_id_str}] Downloading {media_type} for msg {message_id} -> {raw_download_path.name}...")
                # Download to raw path
                # Note: client.download_media can be I/O bound, runs within asyncio loop
                download_result = await self.client.download_media(
                    media_obj,
                    file=raw_download_path
                )
                # Check if download actually created the file
                downloaded_ok = download_result is not None and await run_in_thread_pool(raw_download_path.exists)
                if not downloaded_ok:
                     logger.warning(f"[{entity_id_str}] Download completed but raw file missing: {raw_download_path}")

        except asyncio.TimeoutError:
             logger.error(f"[{entity_id_str}] Timeout downloading media for msg {message_id}")
        except Exception as download_err:
            # Catch specific Telethon errors if needed (e.g., FileReferenceExpiredError)
            logger.error(f"[{entity_id_str}] Download failed for msg {message_id} ({media_type}): {download_err}", exc_info=False) # Keep log clean
            # Clean up potentially corrupted raw file
            await self._cleanup_file_async(raw_download_path)
            return None # Exit if download fails

        if not downloaded_ok:
            await self._cleanup_file_async(raw_download_path)
            return None

        # 3. Optimize / Process (if applicable)
        optimization_success = False
        try:
            logger.info(f"[{entity_id_str}] Processing {media_type}: {raw_download_path.name} -> {final_path.name}")
            if media_type == "image":
                # Optimize image using Pillow (runs _sync_optimize_image in thread pool)
                await self._optimize_image(raw_download_path, final_path)
            elif media_type in ["video", "round_video"]:
                # Optimize video using ffmpeg (runs _sync_optimize_video in thread/process pool)
                await self._optimize_video(raw_download_path, final_path)
            else: # Audio, Document - just rename (effectively a move)
                await run_in_thread_pool(lambda: raw_download_path.rename(final_path))

            # Verify final file exists after processing
            optimization_success = await run_in_thread_pool(final_path.exists)

        except Exception as process_err:
            logger.error(f"[{entity_id_str}] Failed to process {media_type} {raw_download_path.name}: {process_err}", exc_info=False)
            # Attempt fallback copy if optimization failed but raw exists
            if await run_in_thread_pool(raw_download_path.exists):
                 logger.warning(f"[{entity_id_str}] Processing failed, attempting direct copy: {raw_download_path.name} -> {final_path.name}")
                 try:
                     await run_in_thread_pool(lambda: raw_download_path.rename(final_path)) # Try renaming first
                     optimization_success = await run_in_thread_pool(final_path.exists)
                 except Exception as move_err:
                     logger.error(f"[{entity_id_str}] Fallback move/copy failed for {raw_download_path.name}: {move_err}")


        # 4. Cleanup Raw File (only if final file exists and paths differ)
        if optimization_success and raw_download_path != final_path:
            await self._cleanup_file_async(raw_download_path)
        elif await run_in_thread_pool(raw_download_path.exists) and not optimization_success:
             logger.warning(f"[{entity_id_str}] Processing failed and final file missing. Raw file kept: {raw_download_path}")
             # Decide if we should return the raw path or None. Returning None is cleaner.
             return None

        # 5. Update Cache and Return Result
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
            # Check existence and remove in thread pool
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
        access_hash = getattr(media_obj, 'access_hash', '') # Sometimes useful for uniqueness if ID repeats

        # Base name structure: entityID_msgID_mediaType_mediaID(_accessHash)
        base_name = f"{entity_id_str}_msg{message_id}_{media_type}_{media_id}"
        # Optionally add part of access hash if needed, but usually ID is unique enough per message
        # if access_hash:
        #     base_name += f"_{access_hash}"

        ext = ".dat" # Default extension

        if isinstance(media_obj, Photo):
            ext = ".jpg" # Optimized photo default
        elif isinstance(media_obj, Document):
            # Try getting original filename
            original_filename = None
            for attr in getattr(media_obj, 'attributes', []):
                if isinstance(attr, DocumentAttributeFilename):
                    original_filename = attr.file_name
                    break

            if original_filename:
                original_path = Path(original_filename)
                if original_path.suffix and len(original_path.suffix) > 1:
                     # Use original extension if valid
                    ext = original_path.suffix
                else:
                    # Fallback to mime type if no valid extension
                     if hasattr(media_obj, 'mime_type') and '/' in media_obj.mime_type:
                         mime_suffix = media_obj.mime_type.split('/')[-1].split(';')[0]
                         if mime_suffix:
                            ext = f".{mime_suffix}"
            elif hasattr(media_obj, 'mime_type') and '/' in media_obj.mime_type:
                # No original filename, use mime type
                mime_suffix = media_obj.mime_type.split('/')[-1].split(';')[0]
                if mime_suffix:
                     ext = f".{mime_suffix}"


            # Refine extension based on type and optimization target
            if media_type in ['video', 'round_video']:
                 # Common video types often end up as mp4 after optimization
                 # Keep original extension unless it's something generic like .bin or missing
                if not ext or ext.lower() in ['.dat', '.bin']:
                    ext = '.mp4'
            elif media_type == 'audio':
                 is_voice = any(getattr(attr, 'voice', False) for attr in media_obj.attributes if isinstance(attr, DocumentAttributeAudio))
                 if is_voice and (not ext or ext.lower() in ['.dat', '.bin', '.oga']):
                     ext = '.ogg' # Common for voice notes


        # Final sanitization
        safe_base = sanitize_filename(base_name, max_length=180, replacement='_') # Slightly shorter max_length for base
        safe_ext = sanitize_filename(ext, max_length=10, replacement='') # Sanitize ext separately, remove invalid chars
        if not safe_ext.startswith('.'): safe_ext = '.' + safe_ext
        if len(safe_ext) <= 1: safe_ext = ".dat" # Fallback if sanitization removed everything

        return f"{safe_base}{safe_ext}"


    async def _optimize_image(self, input_path: Path, output_path: Path):
        """Optimizes image using Pillow. Runs sync logic in thread pool."""
        await run_in_thread_pool(
            self._sync_optimize_image, input_path, output_path
        )

    def _sync_optimize_image(self, input_path: Path, output_path: Path):
        """Synchronous image optimization logic using Pillow."""
        try:
            with Image.open(input_path) as img:
                img_format = img.format # Store original format if needed
                logger.debug(f"Optimizing image {input_path.name} (Format: {img_format}, Mode: {img.mode})")

                # Convert specific modes to RGB for JPEG saving
                # Handle transparency by pasting onto a white background
                if img.mode in ('RGBA', 'P', 'LA'):
                    try:
                         # Check if alpha channel actually has transparent pixels
                        has_alpha = False
                        if img.mode in ('RGBA', 'LA'):
                            alpha = img.getchannel('A')
                            has_alpha = any(p < 255 for p in alpha.getdata())
                        elif img.mode == 'P' and 'transparency' in img.info:
                             # More complex check for palette transparency, assume yes if present
                             has_alpha = True

                        if has_alpha:
                            logger.debug(f"Image {input_path.name} has transparency, converting to RGB with white background.")
                            # Create white background
                            bg = Image.new("RGB", img.size, (255, 255, 255))
                            # Paste using alpha channel as mask
                            img_rgba = img.convert('RGBA') # Ensure RGBA for pasting
                            bg.paste(img_rgba, (0, 0), img_rgba)
                            img_to_save = bg
                        else:
                             # No actual transparency, just convert mode if needed
                            if img.mode != 'RGB':
                                 img_to_save = img.convert('RGB')
                            else:
                                 img_to_save = img
                    except Exception as convert_err:
                         logger.warning(f"Error during transparency handling/conversion for {input_path.name}: {convert_err}. Saving as is.")
                         img_to_save = img # Save original on error
                else:
                     # Already RGB or grayscale without alpha
                     img_to_save = img

                # Ensure final format is RGB if saving as JPEG
                if img_to_save.mode != 'RGB':
                    img_to_save = img_to_save.convert('RGB')

                # Save as JPEG with configured quality
                img_to_save.save(
                    output_path,
                    "JPEG",
                    quality=self.config.image_quality,
                    optimize=True,
                    progressive=True
                )
                logger.debug(f"Saved optimized image to {output_path.name}")

        except UnidentifiedImageError:
             logger.error(f"Cannot identify image file (corrupted or unsupported format): {input_path}. Skipping optimization.")
             raise # Re-raise error to trigger fallback copy in caller
        except Exception as e:
            logger.error(f"Pillow optimization failed for {input_path}: {e}")
            raise # Re-raise error


    async def _optimize_video(self, input_path: Path, output_path: Path):
        """Optimizes video using ffmpeg. Runs sync logic in an executor."""
        # Using run_in_thread_pool as ffmpeg call itself can block waiting for process
        # ProcessPoolExecutor might be slightly better if ffmpeg is purely CPU bound,
        # but ThreadPoolExecutor is often sufficient and avoids potential pickling issues.
        await run_in_thread_pool(
            self._sync_optimize_video, input_path, output_path
        )

    def _sync_optimize_video(self, input_path: Path, output_path: Path):
        """Synchronous video optimization logic using ffmpeg-python."""
        try:
            logger.debug(f"Optimizing video {input_path.name} with CRF={self.config.video_crf}, Preset={self.config.video_preset}")
            stream = ffmpeg.input(str(input_path))

            # Basic optimization: H.264, CRF, preset, copy audio
            ffmpeg_options = {
                'c:v': 'libx264',
                'crf': str(self.config.video_crf), # CRF needs to be string
                'preset': self.config.video_preset,
                'c:a': 'copy', # Copy audio stream without re-encoding
                'pix_fmt': 'yuv420p', # Common pixel format for compatibility
                'threads': '0' # Use number of cores available (ffmpeg default)
            }

            stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)

            # Run ffmpeg, capture output, check errors
            # Setting capture_stdout/stderr=True can consume a lot of memory for verbose output
            # Use run_async=False and check return code, maybe log stderr on error
            stdout, stderr = ffmpeg.run(stream, capture_stdout=False, capture_stderr=True, overwrite_output=True) # Added overwrite

            # ffmpeg.run raises ffmpeg.Error on non-zero exit code
            logger.debug(f"ffmpeg optimization successful for {output_path.name}")

        except ffmpeg.Error as e:
            stderr_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg execution failed for {input_path.name}:\n{stderr_output}")
            raise # Re-raise to trigger fallback copy
        except Exception as e:
            logger.error(f"Video optimization failed unexpectedly for {input_path.name}: {e}")
            raise # Re-raise
