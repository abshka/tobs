"""
Hardware acceleration detection and configuration.

Detects available hardware encoders (VAAPI, NVENC, QSV, etc.)
and provides optimal encoder selection for media processing.
"""

import asyncio
import os
from typing import Any, Dict, Optional

from loguru import logger


class HardwareAccelerationDetector:
    """Детектор аппаратного ускорения."""

    def __init__(self, config: Optional[Any] = None):
        self.config = config
        self._cache: Dict[str, Any] = {}
        self._detection_complete = False
        self.available_encoders = {
            "nvidia": False,
            "amd": False,
            "intel": False,
            "vaapi": False,
            "videotoolbox": False,  # macOS
        }

    async def detect_hardware_acceleration(self) -> Dict[str, bool]:
        """Проверка доступности VA-API кодека в FFmpeg с реальным тестированием."""
        if self._detection_complete:
            return self.available_encoders

        try:
            # Проверяем доступность VA-API устройства
            vaapi_device = (
                getattr(self.config, "vaapi_device", "/dev/dri/renderD128")
                if self.config
                else "/dev/dri/renderD128"
            )
            if not self._check_vaapi_device(vaapi_device):
                logger.warning(f"VA-API device {vaapi_device} not accessible")
                self.available_encoders["vaapi"] = False
                self._detection_complete = True
                return self.available_encoders

            # Получаем список доступных кодеров
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-encoders",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            encoders_output = stdout.decode("utf-8", errors="ignore")

            # Проверяем наличие VA-API кодека
            if "h264_vaapi" in encoders_output:
                # Тестируем VA-API кодер
                if await self._test_hardware_encoder("h264_vaapi"):
                    self.available_encoders["vaapi"] = True
                    logger.info("VA-API hardware encoder h264_vaapi is working")
                else:
                    self.available_encoders["vaapi"] = False
                    logger.warning("VA-API hardware encoder h264_vaapi failed test")
            else:
                self.available_encoders["vaapi"] = False
                logger.warning("VA-API encoder not found in FFmpeg")

        except Exception as e:
            logger.warning(f"VA-API detection failed: {e}")
            self.available_encoders["vaapi"] = False

        self._detection_complete = True

        if self.available_encoders["vaapi"]:
            logger.info("VA-API hardware acceleration is available")
        else:
            logger.info("VA-API not available, using software encoding")

        return self.available_encoders

    def _get_encoder_name(self, encoder_type: str) -> str:
        """Получение имени VA-API кодера."""
        return "h264_vaapi"

    async def _test_hardware_encoder(self, encoder: str) -> bool:
        """Тестирование конкретного аппаратного кодера."""
        try:
            # Базовые аргументы
            args = [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=0.1:size=64x64:rate=1",
            ]

            # Add encoder-specific options
            if "nvenc" in encoder:
                args.extend(["-c:v", encoder, "-preset", "ultrafast"])
            elif "qsv" in encoder:
                args.extend(["-c:v", encoder, "-global_quality", "23"])
            elif "vaapi" in encoder:
                # VA-API требует специальную настройку
                vaapi_device = (
                    getattr(self.config, "vaapi_device", "/dev/dri/renderD128")
                    if self.config
                    else "/dev/dri/renderD128"
                )
                args.extend(["-vaapi_device", vaapi_device])
                args.extend(["-vf", "format=nv12,hwupload"])
                args.extend(["-c:v", encoder, "-qp", "23"])
            else:
                args.extend(["-c:v", encoder])

            args.extend(["-frames:v", "1", "-f", "null", "-"])

            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            success = proc.returncode == 0
            if not success:
                stderr_text = stderr.decode("utf-8", errors="ignore")[:300]
                logger.debug(f"Hardware encoder {encoder} test failed: {stderr_text}")
                # Для VA-API показываем более детальную ошибку
                if "vaapi" in encoder:
                    logger.warning(
                        f"VA-API test failed - check device access and drivers: {stderr_text[:100]}"
                    )

            return success

        except Exception as e:
            logger.debug(f"Error testing hardware encoder {encoder}: {e}")
            return False

    def get_best_video_encoder(self, fallback: str = "libx264") -> str:
        """Получение лучшего доступного и протестированного видео кодека."""
        # Порядок предпочтений: VAAPI (лучше для Intel) > Intel QSV > NVIDIA > AMD
        if self.available_encoders["vaapi"]:
            return "h264_vaapi"
        elif self.available_encoders["intel"]:
            return "h264_qsv"
        elif self.available_encoders["nvidia"]:
            return "h264_nvenc"
        elif self.available_encoders["amd"]:
            return "h264_amf"
        else:
            logger.info(
                f"No working hardware encoders found, falling back to {fallback}"
            )
            return fallback

    def _check_vaapi_device(self, device_path: str = "/dev/dri/renderD128") -> bool:
        """Проверка доступности устройства VA-API."""
        try:
            if not os.path.exists(device_path):
                logger.warning(f"VA-API device not found: {device_path}")
                return False

            if not os.access(device_path, os.R_OK):
                logger.warning(f"VA-API device not readable: {device_path}")
                return False

            if not os.access(device_path, os.W_OK):
                logger.warning(f"VA-API device not writable: {device_path}")
                return False

            logger.debug(f"VA-API device accessible: {device_path}")
            return True
        except Exception as e:
            logger.warning(f"VA-API device check failed: {e}")
            return False
