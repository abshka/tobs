"""
Video processor for media files.

Handles video file processing including transcoding, scaling,
and hardware acceleration support (VAAPI/NVENC/QSV).
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import aiofiles
import ffmpeg
from loguru import logger

from ..models import MediaMetadata, ProcessingSettings, ProcessingTask
from .base import BaseProcessor

# Environment variables for processing control (fallback values)
DEFAULT_PROCESS_VIDEO = os.getenv("PROCESS_VIDEO", "false").lower() == "true"


class VideoProcessor(BaseProcessor):
    """Процессор для видеофайлов с поддержкой аппаратного ускорения."""

    def __init__(
        self,
        io_executor: Any,
        cpu_executor: Any,
        hw_detector: Any,
        metadata_extractor: Any,
        config: Optional[Any] = None,
        settings: Optional[ProcessingSettings] = None,
    ):
        """
        Инициализация видеопроцессора.

        Args:
            io_executor: Executor для IO операций
            cpu_executor: Executor для CPU-интенсивных операций (FFmpeg)
            hw_detector: HardwareAccelerationDetector для проверки GPU
            metadata_extractor: MetadataExtractor для извлечения метаданных
            config: Объект конфигурации
            settings: Настройки обработки по умолчанию
        """
        super().__init__(io_executor, cpu_executor, settings)
        self.hw_detector = hw_detector
        self.metadata_extractor = metadata_extractor
        self.config = config

        # Статус аппаратного ускорения
        self._hw_acceleration_ready = False
        self._hw_acceleration_tested = False
        self._ffmpeg_tested = False

        # Статистика
        self._video_processed_count = 0
        self._video_copied_count = 0
        self._hardware_encoding_count = 0
        self._software_encoding_count = 0

    async def initialize(self) -> None:
        """Инициализация процессора (проверка FFmpeg и hardware acceleration)."""
        # Проверка FFmpeg
        self._ffmpeg_tested = await self._test_ffmpeg_health()

        # Проверка аппаратного ускорения
        if self.hw_detector:
            available_encoders = await self.hw_detector.detect_hardware_acceleration()
            self._hw_acceleration_ready = available_encoders.get("vaapi", False)
            self._hw_acceleration_tested = True

            if self._hw_acceleration_ready:
                logger.info(
                    "✅ Hardware acceleration (VAAPI) ready for video processing"
                )
            else:
                logger.info(
                    "ℹ️ Hardware acceleration not available, using software encoding"
                )

    async def process(self, task: ProcessingTask, worker_name: str) -> bool:
        """
        Обработка видео файла с fallback на копирование.

        Args:
            task: Задача обработки с input/output путями
            worker_name: Имя воркера для логирования

        Returns:
            True если обработка успешна, False иначе
        """
        try:
            # Проверяем работоспособность FFmpeg
            if not await self._test_ffmpeg_health():
                logger.debug(
                    f"FFmpeg health check failed, using copy for {task.input_path}"
                )
                return await self._copy_file(task)

            settings = task.processing_settings or self.settings
            metadata = task.metadata

            # Проверяем настройку сжатия видео из конфигурации
            if not getattr(self.config, "compress_video", False):
                logger.debug("Video compression disabled in config, copying file")
                return await self._copy_file(task)

            # Определение нужности обработки
            needs_proc = self.needs_processing_with_metadata(metadata, settings)

            if not needs_proc:
                logger.debug(
                    f"Video doesn't need processing, copying: {task.input_path}"
                )
                return await self._copy_file(task)

            # Подготовка параметров обработки
            needs_scaling = False
            scale_width = None
            scale_height = None

            if metadata and metadata.width and metadata.height:
                # Проверка необходимости изменения разрешения
                if (
                    metadata.width > settings.max_video_resolution[0]
                    or metadata.height > settings.max_video_resolution[1]
                ):
                    needs_scaling = True
                    scale_width = settings.max_video_resolution[0]
                    scale_height = settings.max_video_resolution[1]

            # Выбор стратегии кодирования
            use_hardware = (
                self._hw_acceleration_ready
                and self._hw_acceleration_tested
                and settings.enable_hardware_acceleration
            )

            # Настройки видео и аудио
            if use_hardware:
                video_codec = self._get_vaapi_codec()
                video_args = self._get_vaapi_video_args(video_codec)
                logger.debug(f"Using hardware encoder: {video_codec}")
                self._hardware_encoding_count += 1
            else:
                video_codec = settings.video_codec
                video_args = {
                    "vcodec": video_codec,
                    "b:v": f"{settings.max_video_bitrate}k",
                }
                logger.debug(f"Using software encoder: {video_codec}")
                self._software_encoding_count += 1

            audio_args = {
                "acodec": settings.audio_codec,
                "b:a": f"{settings.max_audio_bitrate}k",
            }

            # Выполнение FFmpeg в отдельном потоке
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.cpu_executor,
                self._run_ffmpeg,
                task,
                video_args,
                audio_args,
                use_hardware,
                needs_scaling,
                scale_width,
                scale_height,
            )

            if success and task.output_path.exists():
                logger.debug(
                    f"✅ Video processing completed: {task.input_path} -> {task.output_path}"
                )
                self._video_processed_count += 1
                return True
            else:
                # Fallback: простое копирование при ошибке FFmpeg
                logger.debug(f"Using fallback copy for {task.input_path}")
                return await self._copy_file(task)

        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            return await self._copy_file(task)

    def needs_processing(self, file_path: Path, settings: ProcessingSettings) -> bool:
        """
        Определяет, нужна ли обработка видео файла.

        Args:
            file_path: Путь к видеофайлу
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        # Извлекаем метаданные синхронно (это helper метод)
        try:
            # Для синхронного вызова используем базовую проверку
            if not file_path.exists():
                return False

            file_size = file_path.stat().st_size
            # Не обрабатываем слишком маленькие файлы
            if file_size < 1024:  # меньше 1KB
                return False

            # Оптимизируем только большие файлы
            if file_size > 50 * 1024 * 1024:  # больше 50MB
                return True

            return False
        except Exception:
            return False

    def needs_processing_with_metadata(
        self, metadata: Optional[MediaMetadata], settings: ProcessingSettings
    ) -> bool:
        """
        Определение необходимости обработки видео с учетом метаданных.

        Args:
            metadata: Метаданные видеофайла
            settings: Настройки обработки

        Returns:
            True если файл требует обработки
        """
        # Проверяем настройки из config или переменную окружения
        process_video = getattr(self.config, "process_video", DEFAULT_PROCESS_VIDEO)
        if not process_video:
            logger.debug(
                "Video processing disabled via config or PROCESS_VIDEO environment variable"
            )
            return False

        if not metadata:
            return False  # Если нет метаданных, не рискуем с FFmpeg

        # Не обрабатываем слишком маленькие файлы (возможно повреждены)
        if metadata.file_size < 1024:  # меньше 1KB
            return False

        # Не обрабатываем слишком короткие видео (могут быть GIF)
        if metadata.duration and metadata.duration < 1.0:
            return False

        # Проверка разрешения - только если превышает значительно
        if (
            metadata.width
            and metadata.height
            and (
                metadata.width > settings.max_video_resolution[0] * 1.2
                or metadata.height > settings.max_video_resolution[1] * 1.2
            )
        ):
            return True

        # Проверка битрейта - только если значительно превышает
        if (
            metadata.bitrate and metadata.bitrate > settings.max_video_bitrate * 1500
        ):  # 1.5x threshold
            return True

        # Проверка размера файла - оптимизируем только большие файлы
        if metadata.file_size > 50 * 1024 * 1024:  # больше 50MB
            return True

        # Агрессивная оптимизация только для действительно больших файлов
        if settings.aggressive_compression and metadata.file_size > 10 * 1024 * 1024:
            return True

        return False

    def _run_ffmpeg(
        self,
        task: ProcessingTask,
        video_args: dict,
        audio_args: dict,
        use_hardware: bool,
        needs_scaling: bool,
        scale_width: Optional[int],
        scale_height: Optional[int],
    ) -> bool:
        """
        Выполнение FFmpeg команды (синхронный метод для executor).

        Args:
            task: Задача обработки
            video_args: Аргументы для видео кодирования
            audio_args: Аргументы для аудио кодирования
            use_hardware: Использовать ли аппаратное ускорение
            needs_scaling: Нужно ли масштабирование
            scale_width: Ширина для масштабирования
            scale_height: Высота для масштабирования

        Returns:
            True если FFmpeg выполнен успешно
        """
        try:
            # Валидация входного файла
            if not task.input_path.exists():
                logger.error(f"Input file does not exist: {task.input_path}")
                return False

            if not task.input_path.is_file():
                logger.error(f"Input path is not a file: {task.input_path}")
                return False

            file_size = task.input_path.stat().st_size
            if file_size == 0:
                logger.error(f"Input file is empty: {task.input_path}")
                return False

            logger.debug(
                f"Processing file: {task.input_path} (size: {file_size} bytes)"
            )

            # Построение FFmpeg команды
            if use_hardware and "vaapi" in video_args.get("vcodec", ""):
                # VAAPI hardware encoding
                output = self._build_vaapi_ffmpeg_command(
                    task.input_path,
                    task.output_path,
                    video_args,
                    audio_args,
                    needs_scaling,
                    scale_width,
                    scale_height,
                )
            else:
                # Software encoding
                input_stream = ffmpeg.input(str(task.input_path))

                if needs_scaling and scale_width and scale_height:
                    input_stream = input_stream.video.filter(
                        "scale",
                        scale_width,
                        scale_height,
                        force_original_aspect_ratio="decrease",
                    )

                # Проверяем наличие аудио потока
                has_audio = self._has_audio_stream(task.input_path)
                if has_audio:
                    output = input_stream.output(
                        str(task.output_path), **video_args, **audio_args
                    )
                else:
                    output = input_stream.output(str(task.output_path), **video_args)

            # Логируем FFmpeg команду
            cmd_args = ffmpeg.compile(output)
            logger.debug(f"Executing FFmpeg command: {' '.join(cmd_args)}")

            # Выполнение FFmpeg
            ffmpeg.run(output, overwrite_output=True, quiet=True)

            # Проверяем результат
            if not task.output_path.exists():
                logger.error(
                    f"FFmpeg completed but output file not found: {task.output_path}"
                )
                return False

            output_size = task.output_path.stat().st_size
            if output_size == 0:
                logger.error(f"FFmpeg produced empty file: {task.output_path}")
                return False

            # Проверка на подозрительно маленький размер
            source_size = task.input_path.stat().st_size
            if output_size < source_size * 0.01:  # Меньше 1% от исходного
                logger.warning(
                    f"FFmpeg output suspiciously small: {output_size} bytes from {source_size} bytes"
                )
                return False

            return True

        except ffmpeg.Error as e:
            # Детальная обработка ошибок FFmpeg
            stderr_output = (
                e.stderr.decode("utf-8") if e.stderr else "No stderr available"
            )
            stdout_output = (
                e.stdout.decode("utf-8") if e.stdout else "No stdout available"
            )
            logger.error(f"FFmpeg processing failed for {task.input_path}")
            logger.error(f"FFmpeg stderr: {stderr_output}")
            logger.error(f"FFmpeg stdout: {stdout_output}")

            # Попытка fallback на software кодер если использовался hardware
            if video_args.get("vcodec") != "libx264" and (
                "libcuda.so" in stderr_output
                or "Cannot load" in stderr_output
                or "Error while opening encoder" in stderr_output
            ):
                logger.warning("Hardware encoder failed, retrying with libx264")
                try:
                    video_args_fallback = video_args.copy()
                    video_args_fallback["vcodec"] = "libx264"

                    input_stream = ffmpeg.input(str(task.input_path))
                    has_audio = self._has_audio_stream(task.input_path)

                    if has_audio:
                        output_fallback = input_stream.output(
                            str(task.output_path), **video_args_fallback, **audio_args
                        )
                    else:
                        output_fallback = input_stream.output(
                            str(task.output_path), **video_args_fallback
                        )

                    ffmpeg.run(output_fallback, overwrite_output=True, quiet=True)

                    if (
                        task.output_path.exists()
                        and task.output_path.stat().st_size > 0
                    ):
                        logger.info(
                            f"Fallback to software encoding succeeded for {task.input_path}"
                        )
                        return True
                except Exception as fallback_error:
                    logger.error(
                        f"Software encoding fallback also failed: {fallback_error}"
                    )

            return False

        except Exception as e:
            logger.error(f"FFmpeg unexpected error for {task.input_path}: {str(e)}")
            return False

    def _build_vaapi_ffmpeg_command(
        self,
        input_path: Path,
        output_path: Path,
        video_args: dict,
        audio_args: dict,
        needs_scaling: bool = False,
        scale_width: Optional[int] = None,
        scale_height: Optional[int] = None,
    ):
        """
        Построение команды FFmpeg для VA-API с правильными фильтрами.

        Args:
            input_path: Путь к входному файлу
            output_path: Путь к выходному файлу
            video_args: Аргументы видео кодирования
            audio_args: Аргументы аудио кодирования
            needs_scaling: Нужно ли масштабирование
            scale_width: Ширина для масштабирования
            scale_height: Высота для масштабирования

        Returns:
            FFmpeg output object
        """
        vaapi_device = getattr(self.config, "vaapi_device", "/dev/dri/renderD128")

        # Проверяем доступность устройства
        if not self.hw_detector._check_vaapi_device(vaapi_device):
            logger.warning(
                f"VA-API device {vaapi_device} not accessible, falling back to software encoding"
            )
            raise RuntimeError("VA-API device not accessible")

        # Для VA-API нужна специальная структура команды
        input_args = {"vaapi_device": vaapi_device}

        # Создаем входной поток с устройством VA-API
        input_with_device = ffmpeg.input(str(input_path), **input_args)

        # Добавляем фильтры для VA-API: format -> hwupload -> scaling (если нужно)
        video_stream = input_with_device.video.filter("format", "nv12").filter(
            "hwupload"
        )

        # Применяем масштабирование на GPU если нужно
        if needs_scaling and scale_width and scale_height:
            video_stream = video_stream.filter(
                "scale_vaapi",
                w=scale_width,
                h=scale_height,
                format="nv12",
                force_original_aspect_ratio="decrease",
            )

        # Проверяем наличие аудио потока
        has_audio = self._has_audio_stream(input_path)

        # Создаем выходной поток с аппаратным кодированием
        if has_audio:
            output = ffmpeg.output(
                video_stream,
                input_with_device.audio,
                str(output_path),
                **video_args,
                **audio_args,
            )
        else:
            output = ffmpeg.output(video_stream, str(output_path), **video_args)

        return output

    def _get_vaapi_codec(self) -> str:
        """Получить VAAPI кодек на основе конфигурации."""
        return "hevc_vaapi" if getattr(self.config, "use_h265", False) else "h264_vaapi"

    def _get_vaapi_video_args(self, codec: str) -> dict:
        """
        Получение аргументов для VA-API кодирования.

        Args:
            codec: Название кодека (h264_vaapi или hevc_vaapi)

        Returns:
            Словарь с аргументами FFmpeg
        """
        args = {
            "vcodec": codec,
            "qp": getattr(self.config, "vaapi_quality", 25),
        }

        # Добавляем профиль
        if "h264" in codec:
            args["profile:v"] = "high"
        elif "hevc" in codec:
            args["profile:v"] = "main"

        return args

    def _get_nvenc_video_args(self, codec: str) -> dict:
        """
        Получение аргументов для NVENC кодирования.

        Args:
            codec: Название кодека (h264_nvenc или hevc_nvenc)

        Returns:
            Словарь с аргументами FFmpeg
        """
        return {
            "vcodec": codec,
            "preset": "fast",
            "cq": getattr(self.config, "video_crf", 28),
        }

    def _get_qsv_video_args(self, codec: str) -> dict:
        """
        Получение аргументов для Intel Quick Sync кодирования.

        Args:
            codec: Название кодека (h264_qsv или hevc_qsv)

        Returns:
            Словарь с аргументами FFmpeg
        """
        return {
            "vcodec": codec,
            "global_quality": getattr(self.config, "video_crf", 28),
        }

    def _has_audio_stream(self, file_path: Path) -> bool:
        """
        Проверка наличия аудио потока в видеофайле.

        Args:
            file_path: Путь к видеофайлу

        Returns:
            True если есть аудио поток
        """
        try:
            probe = ffmpeg.probe(str(file_path))
            return any(s["codec_type"] == "audio" for s in probe["streams"])
        except Exception:
            return False

    async def _test_ffmpeg_health(self) -> bool:
        """
        Тест базовой функциональности FFmpeg.

        Returns:
            True если FFmpeg работает корректно
        """
        if self._ffmpeg_tested:
            return True

        try:
            # Простой тест: проверяем наличие ffmpeg через subprocess
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                self._ffmpeg_tested = True
                logger.debug("FFmpeg health check passed")
                return True
            else:
                logger.warning(f"FFmpeg health check failed with code {proc.returncode}")
                return False
                
        except FileNotFoundError:
            logger.error("FFmpeg not found in PATH. Please install ffmpeg.")
            return False
        except Exception as e:
            logger.warning(f"FFmpeg health check failed: {e}")
            return False

    async def _copy_file(self, task: ProcessingTask) -> bool:
        """
        Копирование файла как fallback при ошибках обработки.

        Args:
            task: Задача обработки

        Returns:
            True если копирование успешно
        """
        max_attempts = 3

        for attempt in range(max_attempts):
            try:
                # Проверка исходного файла
                if not task.input_path.exists():
                    logger.error(f"Source file not found: {task.input_path}")
                    return False

                source_size = task.input_path.stat().st_size
                if source_size == 0:
                    logger.error(f"Source file is empty: {task.input_path}")
                    return False

                # Создание директории
                task.output_path.parent.mkdir(parents=True, exist_ok=True)

                # Удаляем существующий файл если есть
                if task.output_path.exists():
                    task.output_path.unlink()

                # Копирование файла
                copied_bytes = 0
                async with aiofiles.open(task.input_path, "rb") as src:
                    async with aiofiles.open(task.output_path, "wb") as dst:
                        while chunk := await src.read(64 * 1024):  # 64KB chunks
                            await dst.write(chunk)
                            copied_bytes += len(chunk)
                        await dst.flush()

                # Проверка результата
                if not task.output_path.exists():
                    logger.error(f"Output file was not created: {task.output_path}")
                    continue

                output_size = task.output_path.stat().st_size

                if output_size != source_size:
                    logger.error(
                        f"File copy size mismatch! Source: {source_size} bytes, "
                        f"Output: {output_size} bytes (attempt {attempt + 1})"
                    )
                    continue

                self._video_copied_count += 1
                return True

            except Exception as e:
                logger.error(f"File copy failed on attempt {attempt + 1}: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                continue

        logger.error(f"File copy failed after {max_attempts} attempts")
        return False

    def get_statistics(self) -> dict[str, Any]:
        """
        Получить статистику обработки видео.

        Returns:
            Словарь со статистикой
        """
        total_processed = self._video_processed_count + self._video_copied_count
        hw_percentage = (
            (self._hardware_encoding_count / total_processed) * 100
            if total_processed > 0
            else 0
        )

        return {
            "total_processed": total_processed,
            "encoded": self._video_processed_count,
            "copied": self._video_copied_count,
            "hardware_encoding": self._hardware_encoding_count,
            "software_encoding": self._software_encoding_count,
            "hardware_encoding_percentage": hw_percentage,
        }

    def log_statistics(self) -> None:
        """Логировать статистику обработки видео."""
        stats = self.get_statistics()
        logger.info(
            f"Video processing stats: {stats['total_processed']} total, "
            f"{stats['encoded']} encoded, {stats['copied']} copied, "
            f"HW encoding: {stats['hardware_encoding_percentage']:.1f}%"
        )
