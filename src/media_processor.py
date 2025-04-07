import asyncio
# import os # Marked as unused, can be removed if not needed later
from pathlib import Path
from typing import Optional, Tuple, List
from PIL import Image
import ffmpeg
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Message, DocumentAttributeVideo, DocumentAttributeAudio, DocumentAttributeFilename
from src.config import Config
from src.utils import logger, ensure_dir_exists, get_relative_path
import aiofiles

class MediaProcessor:
    def __init__(self, config: Config, client):
        self.config = config
        self.client = client # Telethon client instance
        self.semaphore = asyncio.Semaphore(config.concurrent_downloads)

        # Ensure media subdirectories exist
        self._create_media_dirs()

    def _create_media_dirs(self):
        """Creates the necessary media subdirectories."""
        subdirs = ["images", "videos", "round_videos", "audios", "documents"]
        for subdir in subdirs:
            ensure_dir_exists(self.config.media_base_path / subdir)

    async def process_media(self, message: Message, note_base_path: Path) -> List[Tuple[str, Optional[str]]]:
        """Downloads, optimizes (if applicable), and returns Markdown links for media."""
        if not self.config.media_download or not message.media:
            return []

        media_links = []
        media_items = []

        # --- Handle different media types ---
        if isinstance(message.media, MessageMediaPhoto):
             # Accessing message.photo is potentially problematic according to diagnostics,
             # but it's the standard way in Telethon for MessageMediaPhoto. Assuming it works in context.
            media_items.append(message.photo)
        elif isinstance(message.media, MessageMediaDocument):
            # Check attributes to determine type (video, audio, round, document)
            doc = message.media.document
             # Diagnostics point out potential issues if doc is None or DocumentEmpty. Add checks.
            if doc and hasattr(doc, 'attributes'):
                is_video = any(isinstance(attr, DocumentAttributeVideo) for attr in doc.attributes)
                is_audio = any(isinstance(attr, DocumentAttributeAudio) for attr in doc.attributes)
                is_round = is_video and doc.mime_type == 'video/mp4' and any(getattr(attr, 'round_message', False) for attr in doc.attributes if isinstance(attr, DocumentAttributeVideo))

                if is_round:
                    media_items.append(("round_video", doc))
                elif is_video:
                    media_items.append(("video", doc))
                elif is_audio:
                    media_items.append(("audio", doc))
                else:
                    media_items.append(("document", doc))
            else:
                logger.warning(f"Message {message.id} has document media without document data or attributes. Skipping.")

        # Add handling for other potential MessageMedia types if needed
        # Note: Telethon often groups media under the first message's media attribute if fetched correctly.
        # If message.media is a list (e.g., album), iterate through it.
        elif isinstance(message.media, list):
             # This case might occur with older telethon versions or specific fetches.
             # Modern telethon usually attaches grouped media to the first message.
             # Let's assume standard handling covers groups via the first message.
             logger.warning(f"Message {message.id} has unexpected list media type: {type(message.media)}. Skipping complex list handling for now.")
             # If needed, iterate through message.media and call process_media recursively or adapt logic.


        # --- Process gathered media items ---
        download_tasks = []
        for item in media_items:
            media_type = "unknown"
            media_obj = None
            if hasattr(item, 'id'): # Likely a Photo object
                media_type = "image"
                media_obj = item
            elif isinstance(item, tuple): # Our classification for documents
                media_type, media_obj = item

            if media_obj:
                 # Pass the directory where the note will reside, needed for relative path calculation later
                 # Assuming note_base_path is the *root* of the export for this chat/channel.
                 download_tasks.append(self._download_and_optimize(message.id, media_type, media_obj, note_base_path))

        # Run downloads concurrently, respecting the semaphore limit
        # Note: Semaphore is now handled within _download_and_optimize
        results = await asyncio.gather(*download_tasks)

        # Format results into Markdown links with captions
        # Diagnostics indicate message.text, .photo, .video, .document might be unknown.
        # This suggests the Message object definition might be incomplete or assumed.
        # Using getattr for safer access.
        caption_text = getattr(message, 'text', None)
        has_photo = getattr(message, 'photo', None) is not None
        has_video = getattr(message, 'video', None) is not None
        has_document = getattr(message, 'document', None) is not None
        caption = caption_text if caption_text and (has_photo or has_video or has_document) else None # Basic caption logic
        # More robust caption logic might be needed if text is separate from media

        for result_path in results:
            if result_path:
                try:
                    # Calculate path relative to the note's base directory (assumed to be the export root).
                    # Example:
                    # note_base_path = /path/to/output/Test5/ (Export root)
                    # result_path = /path/to/output/Test5/media/videos/file.mp4
                    # We want the path relative to note_base_path.
                    # This should result in "media/videos/file.mp4"
                    # --- CHANGE HERE based on prompt analysis ---
                    # common_ancestor = note_base_path.parent # Original: Assumed note_base_path was notes/ subdir
                    common_ancestor = note_base_path # New: Assume note_base_path is export root (e.g., Test5/)
                    # --- End CHANGE ---

                    relative_media_path_obj = result_path.relative_to(common_ancestor)
                    # Convert to posix string (forward slashes) for Markdown URL compatibility
                    relative_media_path = relative_media_path_obj.as_posix()

                except ValueError:
                    # Fallback if the paths don't share the expected common ancestor (e.g., different drives, or structure mismatch)
                    logger.warning(f"Could not determine relative path from {common_ancestor} to {result_path}. Media might be stored outside the expected export directory. Using filename as fallback.")
                    # Attempt original method as fallback - this might be incorrect if the common_ancestor logic was the issue
                    # relative_media_path = get_relative_path(result_path, note_base_path) # Original fallback call - commented out as it might be wrong too
                    relative_media_path = result_path.name # Fallback to just filename if relative path fails
                except Exception as e:
                    logger.error(f"Error calculating relative path for {result_path}: {e}. Using filename as fallback.")
                    relative_media_path = result_path.name # Fallback to just filename if other errors occur

                # Ensure no leading slash if it's meant to be relative from the containing folder
                # This shouldn't be necessary if relative_to works correctly, but keep as safety.
                relative_media_path = relative_media_path.lstrip('/')

                md_link = f"![]({relative_media_path})"
                media_links.append((md_link, caption)) # Append caption once per message if desired, or per media item
                caption = None # Only add caption to the first media item in a group/message
            else:
                media_links.append(("[media missed]", None)) # Placeholder for failed downloads

        return media_links



    async def _download_and_optimize(self, message_id: int, media_type: str, media_obj, note_base_path: Path) -> Optional[Path]:
        """Handles download and optimization for a single media item."""
        raw_download_path = None # Initialize to prevent unbound error
        downloaded_path_obj = None # Initialize
        final_path = None # Initialize
        try:
            filename = self._get_filename(media_obj, message_id, media_type)
            subdir_map = {
                "image": "images",
                "video": "videos",
                "round_video": "round_videos",
                "audio": "audios",
                "document": "documents",
            }
            # Use media_base_path from config for download location
            target_subdir = self.config.media_base_path / subdir_map.get(media_type, "documents")
            ensure_dir_exists(target_subdir) # Ensure target subdir exists before proceeding
            raw_download_path = target_subdir / f"raw_{filename}"
            final_path = target_subdir / filename # This is the absolute/full path to the final media

            # Check if final file already exists (simple caching)
            if final_path.exists():
                logger.debug(f"Media already exists: {final_path}. Skipping download/optimization.")
                return final_path

            # Acquire semaphore before downloading
            async with self.semaphore:
                logger.info(f"Downloading {media_type} for message {message_id} to {raw_download_path}...")
                try:
                    # Download directly using await
                    downloaded_path_obj = await self.client.download_media(
                        media_obj,
                        file=raw_download_path
                    )
                except Exception as download_err:
                    logger.error(f"Download failed for message {message_id} ({media_type}): {download_err}", exc_info=self.config.verbose)
                    # Clean up potentially corrupted raw file
                    if raw_download_path and raw_download_path.exists():
                        try:
                            raw_download_path.unlink()
                        except OSError:
                            pass # Ignore cleanup error
                    return None # Exit early if download fails

            # Check download result *after* releasing semaphore
            if not downloaded_path_obj or not raw_download_path.exists():
                 logger.error(f"Failed to download media for message {message_id} (downloaded_path_obj empty or file not found).")
                 # Ensure raw file is removed if download seemed to succeed but file is missing
                 if raw_download_path and raw_download_path.exists():
                     try:
                         raw_download_path.unlink()
                     except OSError:
                         pass
                 return None

            logger.info(f"Optimizing {media_type} {raw_download_path} -> {final_path}")

            # Run optimizations outside the download semaphore
            optimization_successful = False # Keep track if optimization/move worked
            if media_type == "image":
                # _optimize_image handles its own exceptions and fallback copy
                await self._optimize_image(raw_download_path, final_path)
                optimization_successful = final_path.exists() # Check if output exists after attempt
            elif media_type in ["video", "round_video"]:
                # _optimize_video handles its own exceptions and fallback copy
                await self._optimize_video(raw_download_path, final_path)
                optimization_successful = final_path.exists() # Check if output exists after attempt
            else: # Audio, Document - just move
                try:
                    # Use awaitable move for consistency if aiofiles is needed elsewhere,
                    # otherwise Path.rename is fine in a thread.
                    await asyncio.to_thread(raw_download_path.rename, final_path)
                    optimization_successful = True # Rename is the "optimization" here
                except Exception as move_err:
                    logger.error(f"Failed to move raw file {raw_download_path} to {final_path}: {move_err}")
                    # Attempt to copy as fallback if move failed? Or just report error?
                    # For now, just log error and let cleanup handle raw file.
                    optimization_successful = False


            # Clean up raw file if processing completed (successfully or with fallback copy)
            # and the raw file still exists (meaning optimization created a new file or move failed)
            if raw_download_path.exists():
                 if final_path.exists() and raw_download_path != final_path:
                     # This means optimization/move likely created a new file (or fallback copy succeeded)
                     try:
                         logger.debug(f"Removing raw file {raw_download_path} after processing.")
                         raw_download_path.unlink()
                     except OSError as unlink_err:
                         logger.warning(f"Could not remove raw file {raw_download_path}: {unlink_err}")
                 elif not final_path.exists():
                     # This case indicates something went wrong (e.g., optimization failed without fallback, move failed)
                     logger.warning(f"Raw file {raw_download_path} exists but final path {final_path} does not. Leaving raw file.")
                 # If raw_download_path == final_path (e.g., move happened in place, although unlikely with raw_ prefix), do nothing


            if final_path and final_path.exists():
                logger.info(f"Finished processing media: {final_path}")
                return final_path # Return the full path object
            else:
                 logger.warning(f"Processing finished, but final media file {final_path} not found.")
                 return None # Return None if the final file doesn't exist

        except Exception as e:
            logger.error(f"Error processing media for message {message_id} ({media_type}): {e}", exc_info=self.config.verbose)
            # Clean up potentially corrupted raw file
            if raw_download_path and raw_download_path.exists():
                try:
                    if not final_path or not final_path.exists() or raw_download_path != final_path:
                        raw_download_path.unlink()
                except OSError:
                    pass # Ignore cleanup error
            # Clean up potentially incomplete final file
            if final_path and final_path.exists():
                 # Decide if we should remove potentially incomplete final file
                 # If optimization failed and copied, it might be complete.
                 # If move failed, it won't exist.
                 # Let's be cautious and leave it if it exists, unless we are sure it's incomplete.
                 pass
            return None


    def _get_filename(self, media_obj, message_id: int, media_type: str) -> str:
        """Generates a unique filename for the media, avoiding redundancy for images."""
        ext = ".dat" # Default extension
        base_name_part = f"msg{message_id}"
        media_id_part = getattr(media_obj, 'id', '') # Get media ID early

        if media_type == "image": # Photo object
            ext = ".jpg" # Default to JPG after optimization
            # Use photo ID for uniqueness if available
            base_name_part = f"msg{message_id}_photo_{media_id_part}" if media_id_part else f"msg{message_id}_photo"
            # For images, the base name already includes all necessary info (msg_id, type, media_id)
            final_base = base_name_part # Use base_name_part directly for images

        elif hasattr(media_obj, 'mime_type'): # Document types (video, audio, document, round_video)
            # Default extension from mime
            mime_suffix = media_obj.mime_type.split('/')[-1]
            mime_suffix = mime_suffix.split(';')[0] # Handle params like 'audio/ogg; codecs=opus'
            if mime_suffix:
                 ext = f".{mime_suffix}"

            # Try to get original filename and extension
            original_filename_attr = None
            for attr in getattr(media_obj, 'attributes', []):
                if isinstance(attr, DocumentAttributeFilename):
                    original_filename_attr = attr.file_name
                    break

            if original_filename_attr:
                original_path = Path(original_filename_attr)
                # Use original extension if available and seems valid
                if original_path.suffix and len(original_path.suffix) > 1: # Avoid single dot suffixes
                    ext = original_path.suffix
                # Decide if original stem should be part of the name (currently not)
                # base_name_part = f"msg{message_id}_{original_path.stem}" # Example if using stem

            # Refine extension based on known types/attributes
            doc_attrs = getattr(media_obj, 'attributes', [])
            is_video_attr = any(isinstance(attr, DocumentAttributeVideo) for attr in doc_attrs)
            is_audio_attr = any(isinstance(attr, DocumentAttributeAudio) for attr in doc_attrs)

            if is_video_attr and media_type in ['video', 'round_video'] and ext in ['.dat', '.mkv', '.avi', '.mov', '.quicktime']: # Common video types sometimes have generic mime/ext
                 ext = '.mp4' # Default optimization target
            elif is_audio_attr and media_type == 'audio':
                # Check for specific audio attributes if needed, e.g., voice note uses .ogg
                is_voice = any(getattr(attr, 'voice', False) for attr in doc_attrs if isinstance(attr, DocumentAttributeAudio))
                if is_voice and ext in ['.dat', '.oga']: # Often voice notes are ogg/opus
                    ext = '.ogg'
                # Add more audio refinements if necessary (e.g., mp3, m4a based on mime/attrs)

            # Construct final_base for documents (non-images)
            # Include media type and ID for uniqueness
            unique_part = f"{media_type}_{media_id_part}" if media_id_part else media_type
            safe_unique_part = str(unique_part).replace('/', '_').replace('\\', '_')
            final_base = f"{base_name_part}_{safe_unique_part}" # e.g., msg123_video_98765

        else: # Fallback for unknown types if any slipped through
             logger.warning(f"Generating filename for unknown media type: {media_type} for message {message_id}")
             unique_part = f"{media_type}_{media_id_part}" if media_id_part else media_type
             safe_unique_part = str(unique_part).replace('/', '_').replace('\\', '_')
             final_base = f"{base_name_part}_{safe_unique_part}" # e.g., msg123_unknown_98765


        # Sanitize and finalize extension
        # Remove potential query parameters or fragments from extension if derived from filename/mime
        safe_ext = ext.split('?')[0].split('#')[0].split(';')[0]
        # Basic sanitization for extension
        safe_ext = safe_ext.replace('/', '_').replace('\\', '_')
        if not safe_ext.startswith('.'): safe_ext = '.' + safe_ext
        if len(safe_ext) <= 1: safe_ext = ".dat" # Fallback extension


        # Sanitize final_base (although parts were already sanitized)
        # Replace spaces and other potentially problematic characters for filenames
        # Using a more robust sanitization approach might be better
        safe_final_base = final_base.replace(' ', '_').replace(':', '_').replace(';', '_')
        # Consider limiting length if necessary

        return f"{safe_final_base}{safe_ext}"


    async def _optimize_image(self, input_path: Path, output_path: Path):
        """Optimizes an image using Pillow."""
        try:
            await asyncio.to_thread(self._sync_optimize_image, input_path, output_path)
            # If optimization succeeds and created a new file, the raw file will be deleted later
        except Exception as e:
            logger.error(f"Pillow optimization failed for {input_path}: {e}. Copying original.")
            # Fallback: copy the original file if optimization fails
            if input_path != output_path:
                 try:
                     async with aiofiles.open(input_path, 'rb') as src, aiofiles.open(output_path, 'wb') as dst:
                         # Read/write in chunks for potentially large files
                         while True:
                             chunk = await src.read(1024 * 1024) # 1MB chunks
                             if not chunk:
                                 break
                             await dst.write(chunk)
                     # After successful copy, the raw file *might* still need cleanup, handled outside
                 except Exception as copy_err:
                     logger.error(f"Failed to copy {input_path} to {output_path} after optimization failure: {copy_err}")
                     # Ensure output path is cleaned up if copy fails midway
                     if output_path.exists():
                         try: output_path.unlink()
                         except OSError: pass


    def _sync_optimize_image(self, input_path: Path, output_path: Path):
        """Synchronous image optimization logic."""
        with Image.open(input_path) as img:
            # Convert RGBA/P to RGB for JPEG compatibility
            if img.mode in ('RGBA', 'P'):
                 # Check if transparency actually exists for P mode
                 has_transparency = False
                 if img.mode == 'P' and 'transparency' in img.info:
                      # Check Pillow version for transparency handling changes if needed
                      transparency_info = img.info['transparency']
                      if isinstance(transparency_info, bytes):
                           # Palette transparency: Check if any alpha byte is < 255
                           if any(alpha < 255 for alpha in transparency_info):
                                has_transparency = True
                      elif isinstance(transparency_info, int):
                           # Single transparent color index
                           try:
                               palette = img.getpalette()
                               if palette and len(palette) > transparency_info * 3 + 3: # Check alpha exists - Simplified logic
                                   # Check if the alpha byte for the transparent index is < 255
                                   # alpha_index_in_palette = transparency_info * 3 + 3 # Example for RGBA palettes - Unused var
                                   # Adjust logic based on actual palette format (e.g., RGB, LA)
                                   # This part can be complex; safer to assume transparency if index exists
                                   # For simplicity here, we assume transparency if index is present
                                   has_transparency = True # Simplified check
                               elif not palette: # No palette, maybe grayscale with alpha?
                                   has_transparency = True # Assume conversion needed
                               else: # Palette exists but doesn't seem to have alpha or index is out of bounds
                                   pass # Assume no relevant transparency
                           except IndexError: # Palette access might fail
                                has_transparency = True # Safer to assume conversion needed


                 if img.mode == 'RGBA' or has_transparency:
                      logger.debug(f"Converting image {input_path} from {img.mode} to RGB for JPEG saving.")
                      # Create a white background image
                      bg = Image.new("RGB", img.size, (255, 255, 255))
                      try:
                          # Paste the image onto the background using alpha channel as mask
                          img_rgba = img if img.mode == 'RGBA' else img.convert('RGBA')
                          bg.paste(img_rgba, (0, 0), img_rgba)
                          img = bg
                      except ValueError as paste_err: # Handle cases like mask size mismatch
                           logger.warning(f"Alpha pasting failed for {input_path}: {paste_err}. Converting directly.")
                           img = img.convert('RGB') # Fallback conversion

                 else: # P mode without detected transparency or other modes like RGB, L
                      if img.mode != 'RGB':
                          logger.debug(f"Converting image {input_path} from {img.mode} to RGB.")
                          img = img.convert('RGB') # Ensure RGB format

            logger.debug(f"Saving optimized image {output_path} with quality {self.config.image_quality}")
            img.save(output_path, "JPEG", quality=self.config.image_quality, optimize=True, progressive=True)


    async def _optimize_video(self, input_path: Path, output_path: Path):
        """Optimizes a video using ffmpeg-python, applying CRF and preset but no scaling."""
        try:
            # Build ffmpeg command
            stream = ffmpeg.input(str(input_path))
            ffmpeg_options = {
                'crf': self.config.video_crf,
                'preset': self.config.video_preset,
                'c:a': 'copy', # Copy audio stream without re-encoding
                'c:v': 'libx264' # Specify H.264 codec for optimization
            }
            logger.debug(f"Optimizing video with options: crf={ffmpeg_options['crf']}, preset={ffmpeg_options['preset']}")

            stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)

            # Compile ffmpeg command arguments
            args = ffmpeg.compile(stream, overwrite_output=True)

            ffmpeg_executable = 'ffmpeg' # Assuming 'ffmpeg' is in PATH
            if args and args[0] == ffmpeg_executable:
                # ffmpeg-python sometimes includes the executable name in the args list. Remove it if present.
                actual_args = args[1:]
            else:
                actual_args = args

            command_to_run = [ffmpeg_executable] + actual_args

            # Run ffmpeg process
            proc = await asyncio.create_subprocess_exec(
                *command_to_run, # Use the potentially corrected command list
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                # Log stderr for debugging
                error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "(no stderr)"
                logger.error(f"ffmpeg failed for {input_path} (code {proc.returncode}):\n{error_msg}")
                # Fallback: copy original if ffmpeg fails
                if input_path != output_path:
                    logger.info(f"Copying original video {input_path} due to ffmpeg failure.")
                    try:
                        async with aiofiles.open(input_path, 'rb') as src, aiofiles.open(output_path, 'wb') as dst:
                            while True:
                                chunk = await src.read(1024 * 1024) # 1MB chunks
                                if not chunk: break
                                await dst.write(chunk)
                    except Exception as copy_err:
                         logger.error(f"Failed to copy {input_path} to {output_path} after ffmpeg failure: {copy_err}")
                         # Ensure output path is cleaned up if copy fails midway
                         if output_path.exists():
                             try: output_path.unlink()
                             except OSError: pass

            else:
                 logger.info(f"ffmpeg optimization successful for {output_path}")


        except ffmpeg.Error as e:
            stderr_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            logger.error(f"ffmpeg configuration or execution error for {input_path}: {stderr_output}")
            if input_path != output_path:
                 logger.info(f"Copying original video {input_path} due to ffmpeg error.")
                 try:
                     async with aiofiles.open(input_path, 'rb') as src, aiofiles.open(output_path, 'wb') as dst:
                         while True:
                             chunk = await src.read(1024 * 1024) # 1MB chunks
                             if not chunk: break
                             await dst.write(chunk)
                 except Exception as copy_err:
                     logger.error(f"Failed to copy {input_path} to {output_path} after ffmpeg error: {copy_err}")
                     if output_path.exists():
                         try: output_path.unlink()
                         except OSError: pass
        except Exception as e:
            logger.error(f"Video optimization failed unexpectedly for {input_path}: {e}", exc_info=self.config.verbose)
            if input_path != output_path:
                 logger.info(f"Copying original video {input_path} due to unexpected error.")
                 try:
                     async with aiofiles.open(input_path, 'rb') as src, aiofiles.open(output_path, 'wb') as dst:
                         while True:
                             chunk = await src.read(1024 * 1024) # 1MB chunks
                             if not chunk: break
                             await dst.write(chunk)
                 except Exception as copy_err:
                     logger.error(f"Failed to copy {input_path} to {output_path} after unexpected error: {copy_err}")
                     if output_path.exists():
                         try: output_path.unlink()
                         except OSError: pass
