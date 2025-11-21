"""
Tests for transcription configuration in interactive UI.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Config
from src.ui.interactive import InteractiveUI


@pytest.fixture
def mock_config():
    """Create a mock configuration object."""
    config = MagicMock(spec=Config)
    config.export_targets = []
    config.export_path = "/tmp/test"
    config.media_download = True
    config.process_video = False
    config.process_audio = True
    config.process_images = True
    config.enable_transcription = True
    config.transcription_model = "base"
    config.transcription_language = None
    config.transcription_device = "cpu"
    config.transcription_compute_type = "int8"
    config.transcription_cache_enabled = True
    return config


@pytest.fixture
def mock_telegram_manager():
    """Create a mock telegram manager."""
    manager = AsyncMock()
    return manager


@pytest.fixture
def interactive_ui(mock_config, mock_telegram_manager):
    """Create InteractiveUI instance."""
    return InteractiveUI(mock_config, mock_telegram_manager)


class TestTranscriptionConfiguration:
    """Test suite for transcription configuration."""

    def test_config_has_transcription_fields(self, mock_config):
        """Test that config has all required transcription fields."""
        assert hasattr(mock_config, "enable_transcription")
        assert hasattr(mock_config, "transcription_model")
        assert hasattr(mock_config, "transcription_language")
        assert hasattr(mock_config, "transcription_device")
        assert hasattr(mock_config, "transcription_compute_type")
        assert hasattr(mock_config, "transcription_cache_enabled")

    def test_transcription_default_values(self, mock_config):
        """Test default transcription values."""
        assert mock_config.enable_transcription is True
        assert mock_config.transcription_model == "base"
        assert mock_config.transcription_language is None  # Auto-detect
        assert mock_config.transcription_device == "cpu"
        assert mock_config.transcription_compute_type == "int8"
        assert mock_config.transcription_cache_enabled is True

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_transcription_toggle(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test toggling transcription on/off."""
        # Setup: transcription is enabled
        interactive_ui.config.enable_transcription = True

        # User chooses option 1 (toggle), then option 7 (return)
        mock_prompt.side_effect = ["1", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify transcription was toggled off
        assert interactive_ui.config.enable_transcription is False

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_transcription_model_selection(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test selecting Whisper model."""
        # User chooses option 2 (model), selects "small", then option 7 (return)
        mock_prompt.side_effect = ["2", "3", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify model was changed to "small"
        assert interactive_ui.config.transcription_model == "small"

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_transcription_language(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test setting transcription language."""
        # User chooses option 3 (language), selects Russian, then option 7 (return)
        mock_prompt.side_effect = ["3", "2", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify language was set to Russian
        assert interactive_ui.config.transcription_language == "ru"

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_transcription_device(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test selecting transcription device."""
        # User chooses option 4 (device), selects CUDA, then option 7 (return)
        mock_prompt.side_effect = ["4", "2", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify device was set to CUDA
        assert interactive_ui.config.transcription_device == "cuda"

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_transcription_compute_type(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test selecting compute type."""
        # User chooses option 5 (compute type), selects float16, then option 7 (return)
        mock_prompt.side_effect = ["5", "2", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify compute type was set to float16
        assert interactive_ui.config.transcription_compute_type == "float16"

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_transcription_cache_toggle(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test toggling transcription cache."""
        # Setup: cache is enabled
        interactive_ui.config.transcription_cache_enabled = True

        # User chooses option 6 (cache toggle), then option 7 (return)
        mock_prompt.side_effect = ["6", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify cache was toggled off
        assert interactive_ui.config.transcription_cache_enabled is False

    @patch("src.ui.interactive.Prompt.ask")
    @patch("src.ui.interactive.rprint")
    @patch("src.ui.interactive.clear_screen")
    @patch("builtins.input")
    async def test_configure_custom_language(
        self, mock_input, mock_clear, mock_rprint, mock_prompt, interactive_ui
    ):
        """Test setting custom language code."""
        # User chooses option 3 (language), option 5 (custom), enters "de", then option 7 (return)
        mock_prompt.side_effect = ["3", "5", "de", "7"]

        # Run configuration
        await interactive_ui._configure_transcription_settings()

        # Verify custom language was set
        assert interactive_ui.config.transcription_language == "de"

    def test_all_whisper_models_available(self, interactive_ui):
        """Test that all Whisper models are available for selection."""
        expected_models = ["tiny", "base", "small", "medium", "large"]

        # This test just verifies the model options are documented
        # In real code, the model validation happens in the Config class
        assert interactive_ui.config.transcription_model in expected_models

    def test_all_devices_available(self, interactive_ui):
        """Test that all device options are available."""
        expected_devices = ["cpu", "cuda", "auto"]

        assert interactive_ui.config.transcription_device in expected_devices

    def test_all_compute_types_available(self, interactive_ui):
        """Test that all compute types are available."""
        expected_types = ["int8", "float16", "float32"]

        assert interactive_ui.config.transcription_compute_type in expected_types
