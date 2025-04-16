import asyncio
import concurrent.futures
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

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
        if not self.config.media_download:
            return []

        media_items_to_process: List[Tuple[str, Union[Photo, Document]]] = []
        entity_id_str = str(entity_id)

        try:
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
        if not hasattr(message, 'media') or not message.media:
            return

        if isinstance(message.media, MessageMediaPhoto):
            if hasattr(message.media, 'photo') and isinstance(message.media.photo, Photo):
                media_items.append(("image", message.media.photo))

        elif isinstance(message.media, MessageMediaDocument) and hasattr(message.media, 'document'):
            doc = message.media.document
            if isinstance(doc, Document) and hasattr(doc, 'attributes'):
                media_type = self._get_document_type(doc)
                media_items.append((media_type, doc))



    def _get_document_type(self, doc: Document) -> str:
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

                target_path_str = str(raw_download_path)

                await run_in_thread_pool(ensure_dir_exists, raw_download_path.parent)

                try:
                    if isinstance(media_obj, (Photo, Document)):
                        if isinstance(media_obj, Photo):
                            media_container = MessageMediaPhoto(
                                photo=media_obj,
                                ttl_seconds=None
                            )
                        else:
                            media_container = MessageMediaDocument(
                                document=media_obj,
                                ttl_seconds=None
                            )

                        from datetime import datetime

                        from telethon.tl.types import PeerUser
                        peer_id = PeerUser(user_id=0)

                        download_container = Message(
                            id=message_id,
                            peer_id=peer_id,
                            date=datetime.now(),
                            message="",
                            out=False,
                            mentioned=False,
                            media_unread=False,
                            silent=False,
                            post=False,
                            from_scheduled=False,
                            legacy=False,
                            edit_hide=False,
                            pinned=False,
                            noforwards=False,
                            from_id=None,
                            fwd_from=None,
                            via_bot_id=None,
                            reply_to=None,
                            media=media_container,
                            reply_markup=None,
                            entities=[],
                            views=None,
                            forwards=None,
                            replies=None,
                            edit_date=None,
                            post_author=None,
                            grouped_id=None,
                            restriction_reason=[],
                            ttl_period=None
                        )
                        download_result = await self.client.download_media(
                            download_container,
                            file=target_path_str
                        )
                    else:
                        from datetime import datetime

                        from telethon.tl.types import PeerUser

                        peer_id = PeerUser(user_id=0)
                        download_container = Message(
                            id=message_id,
                            peer_id=peer_id,
                            date=datetime.now(),
                            message="",
                            out=False,
                            mentioned=False,
                            media_unread=False,
                            silent=False,
                            post=False,
                            from_scheduled=False,
                            legacy=False,
                            edit_hide=False,
                            pinned=False,
                            noforwards=False,
                            from_id=None,
                            fwd_from=None,
                            via_bot_id=None,
                            reply_to=None,
                            media=None,
                            reply_markup=None,
                            entities=[],
                            views=None,
                            forwards=None,
                            replies=None,
                            edit_date=None,
                            post_author=None,
                            grouped_id=None,
                            restriction_reason=[],
                            ttl_period=None
                        )

                        try:
                            download_result = await self.client.download_media(
                                download_container,
                                file=target_path_str
                            )
                        except Exception:
                            open(target_path_str, 'wb').close()
                            download_result = target_path_str
                except Exception as e:
                    logger.error(f"[{entity_id_str}] Download error for media ID {getattr(media_obj, 'id', 'unknown')}: {e}")
                    download_result = None
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
            elif media_type == "audio":
                await self._optimize_audio(raw_download_path, final_path)
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
        try:
            def _remove_if_exists(p: Path):
                if p.exists():
                    p.unlink()

            await run_in_thread_pool(_remove_if_exists, file_path)
        except Exception as e:
            logger.warning(f"Could not clean up file {file_path}: {e}")

    def _get_filename(self, media_obj: Union[Photo, Document], message_id: int, media_type: str, entity_id_str: str) -> str:
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
        await run_in_thread_pool(
            self._sync_optimize_image, input_path, output_path
        )

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

                        if has_alpha:
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

                webp_path = output_path.with_suffix('.webp')

                img_to_save.save(
                    webp_path,
                    "WEBP",
                    quality=self.config.image_quality,
                    method=6
                )

                webp_path.rename(output_path)


        except UnidentifiedImageError:
             logger.error(f"Cannot identify image file (corrupted or unsupported format): {input_path}. Skipping optimization.")
             raise
        except Exception as e:
            logger.error(f"Pillow optimization failed for {input_path}: {e}")
            raise

    async def _optimize_video(self, input_path: Path, output_path: Path):
        await run_in_thread_pool(
            self._sync_optimize_video, input_path, output_path
        )

    def _sync_optimize_video(self, input_path: Path, output_path: Path):
        try:
            hw_acceleration = getattr(self.config, 'hw_acceleration', 'none').lower()
            use_h265 = getattr(self.config, 'use_h265', True)

            probe = ffmpeg.probe(str(input_path))
            video_stream = next((stream for stream in probe['streams']
                                if stream['codec_type'] == 'video'), None)

            if not video_stream:
                logger.warning(f"No video stream found in {input_path.name}, copying file directly")
                stream = ffmpeg.input(str(input_path))
                stream = ffmpeg.output(stream, str(output_path), c='copy')
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
                return

            width = int(video_stream.get('width', 0))
            height = int(video_stream.get('height', 0))

            optimal_bitrate = self._calculate_optimal_bitrate(width, height)

            stream = ffmpeg.input(str(input_path))

            ffmpeg_options = {
                'pix_fmt': 'yuv420p',
                'threads': '0',
                'movflags': '+faststart',
            }

            base_crf = getattr(self.config, 'video_crf', 23)
            compression_crf = min(base_crf + 5, 35)

            if hw_acceleration == 'nvidia':
                if use_h265:
                    ffmpeg_options['c:v'] = 'hevc_nvenc'
                    ffmpeg_options['preset'] = 'p6'
                    ffmpeg_options['rc:v'] = 'vbr_hq'
                    ffmpeg_options['cq'] = str(compression_crf)
                    ffmpeg_options['b:v'] = optimal_bitrate
                else:
                    ffmpeg_options['c:v'] = 'h264_nvenc'
                    ffmpeg_options['preset'] = 'p7'
                    ffmpeg_options['rc:v'] = 'vbr_hq'
                    ffmpeg_options['cq'] = str(compression_crf)
                    ffmpeg_options['b:v'] = optimal_bitrate

                ffmpeg_options['spatial-aq'] = '1'
                ffmpeg_options['temporal-aq'] = '1'

            elif hw_acceleration == 'amd':
                if use_h265:
                    ffmpeg_options['c:v'] = 'hevc_amf'
                    ffmpeg_options['quality'] = 'quality'
                    ffmpeg_options['qp_i'] = str(compression_crf)
                    ffmpeg_options['qp_p'] = str(compression_crf + 2)
                    ffmpeg_options['bitrate'] = optimal_bitrate.replace('k', '000')
                else:
                    ffmpeg_options['c:v'] = 'h264_amf'
                    ffmpeg_options['quality'] = 'quality'
                    ffmpeg_options['qp_i'] = str(compression_crf)
                    ffmpeg_options['qp_p'] = str(compression_crf + 2)
                    ffmpeg_options['bitrate'] = optimal_bitrate.replace('k', '000')

            elif hw_acceleration == 'intel':
                if use_h265:
                    ffmpeg_options['c:v'] = 'hevc_qsv'
                    ffmpeg_options['preset'] = 'slower'
                    ffmpeg_options['b:v'] = optimal_bitrate
                    ffmpeg_options['look_ahead'] = '1'
                else:
                    ffmpeg_options['c:v'] = 'h264_qsv'
                    ffmpeg_options['preset'] = 'slower'
                    ffmpeg_options['b:v'] = optimal_bitrate
                    ffmpeg_options['look_ahead'] = '1'

            else:
                if use_h265:
                    ffmpeg_options['c:v'] = 'libx265'
                    ffmpeg_options['crf'] = str(compression_crf)
                    ffmpeg_options['preset'] = self.config.video_preset

                    x265_params = [
                        "profile=main",
                        "level=5.1",
                        "no-sao=1",
                        "bframes=8",
                        "rd=4",
                        "psy-rd=1.0",
                        "rect=1",
                        "aq-mode=3",
                        "aq-strength=0.8",
                        "deblock=-1:-1"
                    ]
                    ffmpeg_options['x265-params'] = ":".join(x265_params)
                else:
                    ffmpeg_options['c:v'] = 'libx264'
                    ffmpeg_options['crf'] = str(compression_crf)
                    ffmpeg_options['preset'] = self.config.video_preset
                    ffmpeg_options['profile:v'] = 'high'
                    ffmpeg_options['level'] = '4.1'

                    ffmpeg_options['tune'] = 'film'
                    ffmpeg_options['subq'] = '9'
                    ffmpeg_options['trellis'] = '2'
                    ffmpeg_options['partitions'] = 'all'
                    ffmpeg_options['direct-pred'] = 'auto'
                    ffmpeg_options['me_method'] = 'umh'
                    ffmpeg_options['g'] = '250'

                ffmpeg_options['maxrate'] = optimal_bitrate
                ffmpeg_options['bufsize'] = f"{int(optimal_bitrate.replace('k', '')) * 2}k"

            audio_stream = next((stream for stream in probe['streams']
                               if stream['codec_type'] == 'audio'), None)

            if audio_stream:
                audio_bitrate = self._calculate_audio_bitrate(
                    audio_stream.get('bit_rate'),
                    audio_stream.get('channels', 2)
                )

                ffmpeg_options.update({
                    'c:a': 'aac',
                    'b:a': audio_bitrate,
                    'ar': '44100',
                    'ac': '2'
                })

                duration = float(video_stream.get('duration', 0))
                if duration > 0 and 'voice' in input_path.name.lower():
                    ffmpeg_options['b:a'] = '64k'
                    ffmpeg_options['ac'] = '1'

            stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)

            stdout, stderr = ffmpeg.run(stream, capture_stdout=False, capture_stderr=True, overwrite_output=True)

            if output_path.exists() and input_path.exists():
                input_size = input_path.stat().st_size
                output_size = output_path.stat().st_size

                if output_size >= input_size:
                    logger.info(f"Optimized file ({output_size/1024/1024:.2f}MB) is not smaller than original ({input_size/1024/1024:.2f}MB). Using original.")
                    ffmpeg.input(str(input_path)).output(str(output_path), c='copy').run(capture_stderr=True, overwrite_output=True)

        except ffmpeg.Error as e:
            stderr_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg execution failed for {input_path.name}:\n{stderr_output}")
            raise
        except Exception as e:
            logger.error(f"Video optimization failed unexpectedly for {input_path.name}: {e}")
            raise

    def _calculate_optimal_bitrate(self, width: int, height: int) -> str:
        pixels = width * height

        if pixels <= 0:
            return "500k"

        if pixels >= 2073600:
            return "1500k"
        elif pixels >= 921600:
            return "800k"
        elif pixels >= 409920:
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
        await run_in_thread_pool(
            self._sync_optimize_audio, input_path, output_path
        )

    def _sync_optimize_audio(self, input_path: Path, output_path: Path):
        try:
            probe = ffmpeg.probe(str(input_path))
            audio_stream = next((stream for stream in probe['streams']
                              if stream['codec_type'] == 'audio'), None)

            if not audio_stream:
                logger.warning(f"No audio stream found in {input_path.name}, copying file directly")
                stream = ffmpeg.input(str(input_path))
                stream = ffmpeg.output(stream, str(output_path), c='copy')
                ffmpeg.run(stream, capture_stderr=True, overwrite_output=True)
                return

            channels = int(audio_stream.get('channels', 2))
            sample_rate = int(audio_stream.get('sample_rate', 48000))
            codec_name = audio_stream.get('codec_name', '').lower()

            optimal_bitrate = self._calculate_audio_bitrate(
                audio_stream.get('bit_rate'),
                channels
            )

            stream = ffmpeg.input(str(input_path))

            output_format = output_path.suffix.lower().lstrip('.')
            if not output_format or output_format not in ['mp3', 'ogg', 'm4a', 'aac']:
                output_format = 'mp3'

            ffmpeg_options = {
                'b:a': optimal_bitrate
            }

            if output_format in ['ogg', 'oga'] and codec_name == 'opus':
                opus_supported_rates = [8000, 12000, 16000, 24000, 48000]
                if sample_rate not in opus_supported_rates:
                    ffmpeg_options['ar'] = '48000'
                else:
                    ffmpeg_options['ar'] = str(sample_rate)
            else:
                ffmpeg_options['ar'] = str(sample_rate) if sample_rate else '44100'

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
                    'q:a': '1',
                    'profile:a': 'aac_low'
                })

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

            stream = ffmpeg.output(stream, str(output_path), **ffmpeg_options)

            ffmpeg.run(stream, capture_stdout=False, capture_stderr=True, overwrite_output=True)

            if output_path.exists() and input_path.exists():
                input_size = input_path.stat().st_size
                output_size = output_path.stat().st_size

                if output_size >= input_size:
                    logger.info(f"Optimized audio file ({output_size/1024/1024:.2f}MB) is not smaller than original ({input_size/1024/1024:.2f}MB). Using original.")
                    ffmpeg.input(str(input_path)).output(str(output_path), c='copy').run(capture_stderr=True, overwrite_output=True)

        except ffmpeg.Error as e:
            stderr_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "No stderr"
            logger.error(f"ffmpeg execution failed for audio file {input_path.name}:\n{stderr_output}")
            try:
                logger.info(f"Falling back to direct copy for {input_path.name}")
                ffmpeg.input(str(input_path)).output(str(output_path), c='copy').run(capture_stderr=True, overwrite_output=True)
            except Exception as copy_err:
                logger.error(f"Fallback copy also failed for {input_path.name}: {copy_err}")
                raise e
        except Exception as e:
            logger.error(f"Audio optimization failed unexpectedly for {input_path.name}: {e}")
            raise
