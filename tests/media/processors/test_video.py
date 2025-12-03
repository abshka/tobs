"""
Unit tests for VideoProcessor - Comprehensive coverage.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.media.models import MediaMetadata, ProcessingSettings
from src.media.processors.video import VideoProcessor

pytestmark = pytest.mark.unit


class TestVideoProcessor:
    """Tests for VideoProcessor class."""

    @pytest.fixture
    def video_processor(
        self,
        io_executor,
        cpu_executor,
        mock_hw_detector,
        mock_metadata_extractor,
        mock_config,
    ):
        """Create VideoProcessor instance for tests."""
        return VideoProcessor(
            io_executor=io_executor,
            cpu_executor=cpu_executor,
            hw_detector=mock_hw_detector,
            metadata_extractor=mock_metadata_extractor,
            config=mock_config,
        )

    @pytest.fixture
    def processing_settings_default(self):
        """Default processing settings for tests."""
        return ProcessingSettings(
            max_video_resolution=(1920, 1080),
            max_video_bitrate=2000,
            max_audio_bitrate=128,
            enable_hardware_acceleration=True,
        )

    @pytest.fixture
    def metadata_large_video(self):
        """Metadata for large video needing processing."""
        return MediaMetadata(
            file_size=60 * 1024 * 1024,  # 60MB
            mime_type="video/mp4",
            duration=300.0,
            width=3840,  # 4K
            height=2160,
            bitrate=10000000,  # 10 Mbps
        )

    @pytest.fixture
    def metadata_small_video(self):
        """Metadata for small video not needing processing."""
        return MediaMetadata(
            file_size=5 * 1024 * 1024,  # 5MB
            mime_type="video/mp4",
            duration=60.0,
            width=1280,  # 720p
            height=720,
            bitrate=800000,  # 800 kbps
        )

    # =========================================================================
    # Session 1: needs_processing_with_metadata Tests
    # =========================================================================

    def test_initialization(self, video_processor, mock_hw_detector):
        """Test that VideoProcessor initializes correctly."""
        assert video_processor.hw_detector is mock_hw_detector
        # VideoProcessor doesn't expose supports_processing, just verify initialization
        assert video_processor.config is not None

    def test_needs_processing_with_metadata_process_video_disabled(
        self, video_processor, metadata_large_video, processing_settings_default
    ):
        """Test that when process_video is False, no processing is needed."""
        video_processor.config.process_video = False

        result = video_processor.needs_processing_with_metadata(
            metadata_large_video, processing_settings_default
        )

        assert result is False

    def test_needs_processing_video_within_limits(
        self, video_processor, metadata_small_video, processing_settings_default
    ):
        """Test that video within all limits does not need processing."""
        video_processor.config.process_video = True

        result = video_processor.needs_processing_with_metadata(
            metadata_small_video, processing_settings_default
        )

        assert result is False

    def test_needs_processing_resolution_exceeds(
        self, video_processor, processing_settings_default
    ):
        """Test that videos exceeding resolution need processing."""
        video_processor.config.process_video = True
        processing_settings = processing_settings_default

        # 4K video exceeds 1920x1080
        metadata = MediaMetadata(
            file_size=10 * 1024 * 1024,  # 10MB
            mime_type="video/mp4",
            duration=120.0,
            width=3840,  # 4K width
            height=2160,
            bitrate=2000000,
        )

        result = video_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )

        assert result is True

    def test_needs_processing_bitrate_exceeds_threshold(
        self, video_processor, processing_settings_default
    ):
        """Test that videos with high bitrate need processing."""
        video_processor.config.process_video = True
        processing_settings = processing_settings_default

        # Bitrate exceeds 2000 * 1500 = 3,000,000
        metadata = MediaMetadata(
            file_size=10 * 1024 * 1024,  # 10MB
            mime_type="video/mp4",
            duration=120.0,
            width=1920,
            height=1080,
            bitrate=5000000,  # 5 Mbps
        )

        result = video_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )

        assert result is True

    def test_needs_processing_large_file(
        self, video_processor, processing_settings_default
    ):
        """Test that large files (> 50MB) need processing."""
        video_processor.config.process_video = True
        processing_settings = processing_settings_default

        metadata = MediaMetadata(
            file_size=60 * 1024 * 1024,  # 60MB
            mime_type="video/mp4",
            duration=300.0,
            width=1920,
            height=1080,
            bitrate=2000000,
        )

        result = video_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )

        assert result is True

    def test_needs_processing_edge_case_exactly_at_limit(
        self, video_processor, processing_settings_default
    ):
        """Test video exactly at resolution and bitrate limits."""
        video_processor.config.process_video = True
        processing_settings = processing_settings_default

        # Exactly at limits
        metadata = MediaMetadata(
            file_size=40 * 1024 * 1024,  # 40MB, under 50MB limit
            mime_type="video/mp4",
            duration=120.0,
            width=1920,  # Exactly at limit
            height=1080,  # Exactly at limit
            bitrate=2000000,  # Exactly at 2000 kbps
        )

        result = video_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )

        # Should not need processing (exactly at limits)
        assert result is False

    # =========================================================================
    # Session 2: FFmpeg Command Building & Audio Detection Tests
    # =========================================================================

    @patch("src.media.processors.video.ffmpeg")
    def test_has_audio_stream_with_audio(self, mock_ffmpeg, video_processor, tmp_path):
        """Test detection of audio stream in video file."""
        input_file = tmp_path / "video_with_audio.mp4"
        input_file.write_text("fake video")

        # Mock ffmpeg.probe to return audio stream
        mock_ffmpeg.probe.return_value = {
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio"},  # Has audio
            ]
        }

        result = video_processor._has_audio_stream(input_file)

        assert result is True
        mock_ffmpeg.probe.assert_called_once_with(str(input_file))

    @patch("src.media.processors.video.ffmpeg")
    def test_has_audio_stream_without_audio(
        self, mock_ffmpeg, video_processor, tmp_path
    ):
        """Test that video without audio is correctly detected."""
        input_file = tmp_path / "video_no_audio.mp4"
        input_file.write_text("fake video")

        # Mock ffmpeg.probe to return only video stream
        mock_ffmpeg.probe.return_value = {
            "streams": [
                {"codec_type": "video"},  # Only video, no audio
            ]
        }

        result = video_processor._has_audio_stream(input_file)

        assert result is False

    @patch("src.media.processors.video.ffmpeg")
    def test_has_audio_stream_probe_error(self, mock_ffmpeg, video_processor, tmp_path):
        """Test that probe errors default to False (no audio assumed)."""
        input_file = tmp_path / "corrupted.mp4"
        input_file.write_text("corrupted")

        # Mock ffmpeg.probe to raise an error
        mock_ffmpeg.probe.side_effect = Exception("Probe failed")

        result = video_processor._has_audio_stream(input_file)

        # Should return False as fallback
        assert result is False

    @patch("src.media.processors.video.ffmpeg")
    def test_build_vaapi_command_basic(self, mock_ffmpeg, video_processor, tmp_path):
        """Test building basic VAAPI FFmpeg command without scaling."""
        input_file = tmp_path / "input.mp4"
        output_file = tmp_path / "output.mp4"
        input_file.write_text("fake input")

        # Setup mock chain
        mock_input = MagicMock()
        mock_video = MagicMock()
        mock_format = MagicMock()
        mock_hwupload = MagicMock()
        mock_output = MagicMock()

        mock_ffmpeg.input.return_value = mock_input
        mock_input.video.filter.return_value = mock_format
        mock_format.filter.return_value = mock_hwupload
        mock_ffmpeg.output.return_value = mock_output

        video_args = {"vcodec": "h264_vaapi", "qp": 25}
        audio_args = {"acodec": "aac", "b:a": "128k"}

        # Mock audio stream detection
        with patch.object(video_processor, "_has_audio_stream", return_value=False):
            result = video_processor._build_vaapi_ffmpeg_command(
                input_file,
                output_file,
                video_args,
                audio_args,
                needs_scaling=False,
            )

        # Verify the mock chain was called correctly
        mock_ffmpeg.input.assert_called_once()
        mock_input.video.filter.assert_called_once_with("format", "nv12")
        mock_format.filter.assert_called_once_with("hwupload")

        # No scale_vaapi filter should be applied
        assert mock_hwupload.filter.call_count == 0

    @patch("src.media.processors.video.ffmpeg")
    def test_build_vaapi_command_with_scaling(
        self, mock_ffmpeg, video_processor, tmp_path
    ):
        """Test building VAAPI FFmpeg command with scaling."""
        input_file = tmp_path / "input_4k.mp4"
        output_file = tmp_path / "output_1080p.mp4"
        input_file.write_text("fake 4k input")

        # Setup more complex mock chain for scaling
        mock_input = MagicMock()
        mock_video = MagicMock()
        mock_format = MagicMock()
        mock_hwupload = MagicMock()
        mock_scale_vaapi = MagicMock()
        mock_output = MagicMock()

        mock_ffmpeg.input.return_value = mock_input
        mock_input.video.filter.return_value = mock_format
        # Chain: format().filter("hwupload").filter("scale_vaapi")
        mock_format.filter.return_value = mock_hwupload
        mock_hwupload.filter.return_value = mock_scale_vaapi
        mock_ffmpeg.output.return_value = mock_output

        video_args = {"vcodec": "h264_vaapi", "qp": 25}
        audio_args = {"acodec": "aac", "b:a": "128k"}

        # Mock audio stream detection
        with patch.object(video_processor, "_has_audio_stream", return_value=False):
            result = video_processor._build_vaapi_ffmpeg_command(
                input_file,
                output_file,
                video_args,
                audio_args,
                needs_scaling=True,
                scale_width=1920,
                scale_height=1080,
            )

        # Verify format filter
        mock_input.video.filter.assert_called_once_with("format", "nv12")

        # Verify hwupload filter
        mock_format.filter.assert_called_once_with("hwupload")

        # Verify scale_vaapi filter was applied
        mock_hwupload.filter.assert_called_once()
        scale_call = mock_hwupload.filter.call_args
        assert scale_call[0][0] == "scale_vaapi"
        assert scale_call[1]["w"] == 1920
        assert scale_call[1]["h"] == 1080

    def test_build_vaapi_command_device_unavailable(self, video_processor, tmp_path):
        """Test that RuntimeError is raised when VAAPI device unavailable."""
        input_file = tmp_path / "input.mp4"
        output_file = tmp_path / "output.mp4"
        input_file.write_text("fake input")

        # Mock VAAPI device check to fail
        video_processor.hw_detector._check_vaapi_device = MagicMock(return_value=False)
        video_processor.config.vaapi_device = "/dev/dri/renderD128"

        video_args = {"vcodec": "h264_vaapi", "qp": 25}
        audio_args = {"acodec": "aac", "b:a": "128k"}

        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="VA-API device not accessible"):
            video_processor._build_vaapi_ffmpeg_command(
                input_file,
                output_file,
                video_args,
                audio_args,
                needs_scaling=False,
            )

    # =========================================================================
    # Session 3: Hardware Encoder Selection Tests
    # =========================================================================

    def test_get_vaapi_codec_h264(self, video_processor):
        """Test that H.264 VAAPI codec is selected by default."""
        # Default config should not have use_h265
        video_processor.config.use_h265 = False

        codec = video_processor._get_vaapi_codec()

        assert codec == "h264_vaapi"

    def test_get_vaapi_codec_hevc(self, video_processor):
        """Test that HEVC VAAPI codec is selected when use_h265 is True."""
        video_processor.config.use_h265 = True

        codec = video_processor._get_vaapi_codec()

        assert codec == "hevc_vaapi"

    def test_get_vaapi_video_args_h264(self, video_processor):
        """Test VAAPI video arguments for H.264 codec."""
        video_processor.config.vaapi_quality = 23

        args = video_processor._get_vaapi_video_args("h264_vaapi")

        assert args["vcodec"] == "h264_vaapi"
        assert args["qp"] == 23
        assert args["profile:v"] == "high"

    def test_get_vaapi_video_args_hevc(self, video_processor):
        """Test VAAPI video arguments for HEVC codec."""
        video_processor.config.vaapi_quality = 28

        args = video_processor._get_vaapi_video_args("hevc_vaapi")

        assert args["vcodec"] == "hevc_vaapi"
        assert args["qp"] == 28
        assert args["profile:v"] == "main"

    # =========================================================================
    # Session 4: FFmpeg Execution & Error Handling Tests
    # =========================================================================

    def test_run_ffmpeg_input_validation_missing_file(
        self, video_processor, processing_settings_default, tmp_path
    ):
        """Test that _run_ffmpeg returns False when input file doesn't exist."""
        from src.media.models import ProcessingTask

        # Create task with non-existent input file
        input_file = tmp_path / "missing_video.mp4"
        output_file = tmp_path / "output.mp4"

        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="video",
            processing_settings=processing_settings_default,
        )

        video_args = {"vcodec": "libx264", "b:v": "2000k"}
        audio_args = {"acodec": "aac", "b:a": "128k"}

        result = video_processor._run_ffmpeg(
            task=task,
            video_args=video_args,
            audio_args=audio_args,
            use_hardware=False,
            needs_scaling=False,
            scale_width=None,
            scale_height=None,
        )

        assert result is False

    def test_run_ffmpeg_input_validation_empty_file(
        self, video_processor, processing_settings_default, tmp_path
    ):
        """Test that _run_ffmpeg returns False when input file is empty."""
        from src.media.models import ProcessingTask

        # Create empty input file
        input_file = tmp_path / "empty_video.mp4"
        output_file = tmp_path / "output.mp4"
        input_file.touch()  # Create empty file

        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="video",
            processing_settings=processing_settings_default,
        )

        video_args = {"vcodec": "libx264", "b:v": "2000k"}
        audio_args = {"acodec": "aac", "b:a": "128k"}

        result = video_processor._run_ffmpeg(
            task=task,
            video_args=video_args,
            audio_args=audio_args,
            use_hardware=False,
            needs_scaling=False,
            scale_width=None,
            scale_height=None,
        )

        assert result is False

    @patch("src.media.processors.video.ffmpeg")
    def test_run_ffmpeg_software_encoding_success(
        self, mock_ffmpeg, video_processor, processing_settings_default, tmp_path
    ):
        """Test successful software encoding with FFmpeg."""
        from src.media.models import ProcessingTask

        # Create input file with some content
        input_file = tmp_path / "input_video.mp4"
        output_file = tmp_path / "output.mp4"
        input_file.write_bytes(b"fake video data" * 1000)  # ~15KB

        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="video",
            processing_settings=processing_settings_default,
        )

        # Mock ffmpeg chain
        mock_input = MagicMock()
        mock_output = MagicMock()
        mock_ffmpeg.input.return_value = mock_input
        mock_input.output.return_value = mock_output
        mock_ffmpeg.compile.return_value = ["ffmpeg", "-i", "input.mp4"]

        # Mock ffmpeg.run to create output file
        def create_output_file(*args, **kwargs):
            output_file.write_bytes(b"processed video" * 500)  # ~7.5KB

        mock_ffmpeg.run.side_effect = create_output_file

        # Mock audio stream detection
        with patch.object(video_processor, "_has_audio_stream", return_value=True):
            video_args = {"vcodec": "libx264", "b:v": "2000k"}
            audio_args = {"acodec": "aac", "b:a": "128k"}

            result = video_processor._run_ffmpeg(
                task=task,
                video_args=video_args,
                audio_args=audio_args,
                use_hardware=False,
                needs_scaling=False,
                scale_width=None,
                scale_height=None,
            )

        assert result is True
        assert output_file.exists()
        assert output_file.stat().st_size > 0
        mock_ffmpeg.run.assert_called_once()

    @patch("src.media.processors.video.ffmpeg")
    def test_run_ffmpeg_hardware_fallback_to_software(
        self, mock_ffmpeg, video_processor, processing_settings_default, tmp_path
    ):
        """Test hardware encoding failure with fallback to software encoding."""
        import ffmpeg

        from src.media.models import ProcessingTask

        # Create input file
        input_file = tmp_path / "input_video.mp4"
        output_file = tmp_path / "output.mp4"
        input_file.write_bytes(b"fake video data" * 1000)

        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="video",
            processing_settings=processing_settings_default,
        )

        # Mock ffmpeg chain
        mock_input = MagicMock()
        mock_video = MagicMock()
        mock_format = MagicMock()
        mock_hwupload = MagicMock()
        mock_output = MagicMock()

        mock_ffmpeg.input.return_value = mock_input
        mock_input.video.filter.return_value = mock_format
        mock_format.filter.return_value = mock_hwupload
        mock_ffmpeg.output.return_value = mock_output
        mock_input.output.return_value = mock_output
        mock_ffmpeg.compile.return_value = ["ffmpeg", "-hwaccel", "vaapi"]

        # First call: hardware fails with "Cannot load" error
        # Second call: software succeeds
        call_count = 0

        def ffmpeg_run_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: hardware encoding fails
                error = ffmpeg.Error("ffmpeg", b"", b"Cannot load libcuda.so")
                raise error
            else:
                # Second call: software encoding succeeds
                output_file.write_bytes(b"processed video" * 500)

        mock_ffmpeg.run.side_effect = ffmpeg_run_side_effect
        mock_ffmpeg.Error = ffmpeg.Error  # Make sure Error class is available

        # Mock audio stream detection
        with patch.object(video_processor, "_has_audio_stream", return_value=True):
            video_args = {"vcodec": "h264_vaapi", "qp": 25}
            audio_args = {"acodec": "aac", "b:a": "128k"}

            result = video_processor._run_ffmpeg(
                task=task,
                video_args=video_args,
                audio_args=audio_args,
                use_hardware=True,
                needs_scaling=False,
                scale_width=None,
                scale_height=None,
            )

        assert result is True
        assert output_file.exists()
        assert output_file.stat().st_size > 0
        # Should be called twice: once for hardware, once for fallback
        assert mock_ffmpeg.run.call_count == 2
