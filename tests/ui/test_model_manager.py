"""
Tests for Whisper model manager functionality.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from src.media.processors.model_manager import (
    WhisperModelManager,
    WHISPER_MODELS,
)


class TestWhisperModelManager:
    """Test suite for WhisperModelManager."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager(self, temp_cache_dir):
        """Create manager with temporary cache directory."""
        return WhisperModelManager(custom_cache_dir=temp_cache_dir)

    def test_manager_initialization(self, manager, temp_cache_dir):
        """Test manager initialization."""
        assert manager.custom_cache_dir == temp_cache_dir
        assert manager.hf_cache_dir is not None

    def test_get_model_cache_path_custom(self, manager, temp_cache_dir):
        """Test custom cache path for model."""
        path = manager.get_model_cache_path("large-v3")
        assert path == temp_cache_dir / "whisper-large-v3"

    def test_get_model_cache_path_hf(self):
        """Test HuggingFace cache path for model."""
        manager = WhisperModelManager()
        path = manager.get_model_cache_path("large-v3")
        assert "whisper-large-v3" in str(path)

    def test_is_model_available_not_downloaded(self, manager):
        """Test checking for unavailable model."""
        assert not manager.is_model_available("large-v3")

    def test_is_model_available_invalid_model(self, manager):
        """Test checking invalid model."""
        assert not manager.is_model_available("invalid-model")

    def test_get_available_models_empty(self, manager):
        """Test getting available models when none are downloaded."""
        available = manager.get_available_models()
        assert available == []

    def test_get_available_models_with_models(self, manager, temp_cache_dir):
        """Test getting available models."""
        # Create mock model directories
        base_dir = temp_cache_dir / "whisper-large-v3"
        base_dir.mkdir()
        (base_dir / "model.bin").touch()

        available = manager.get_available_models()
        assert "large-v3" in available

    def test_get_model_info_available(self, manager, temp_cache_dir):
        """Test getting info for available model."""
        # Create mock model
        base_dir = temp_cache_dir / "whisper-large-v3"
        base_dir.mkdir()
        (base_dir / "model.bin").write_text("test" * 1000)  # ~4KB

        info = manager.get_model_info("large-v3")
        assert info is not None
        assert info["is_available"] is True
        assert "size_mb" in info
        assert "description" in info
        assert "cache_path" in info

    def test_get_model_info_unavailable(self, manager):
        """Test getting info for unavailable model."""
        info = manager.get_model_info("large-v3")
        assert info is not None
        assert info["is_available"] is False
        assert "size_mb" in info

    def test_get_model_info_invalid(self, manager):
        """Test getting info for invalid model."""
        info = manager.get_model_info("invalid-model")
        assert info is None

    def test_get_models_info(self, manager, temp_cache_dir):
        """Test getting info for all models."""
        # Create one model
        base_dir = temp_cache_dir / "whisper-large-v3"
        base_dir.mkdir()
        (base_dir / "model.bin").touch()

        all_info = manager.get_models_info()
        assert len(all_info) == len(WHISPER_MODELS)
        assert "large-v3" in all_info
        assert all_info["large-v3"]["is_available"] is True
        assert list(all_info.keys()) == list(WHISPER_MODELS.keys())

    def test_get_directory_size_mb(self):
        """Test directory size calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            # Create file with ~1MB
            (path / "test.bin").write_bytes(b"x" * (1024 * 1024))
            
            size = WhisperModelManager._get_directory_size_mb(path)
            assert size >= 1

    def test_get_total_cache_size_mb(self, manager, temp_cache_dir):
        """Test total cache size calculation."""
        # Create multiple model directories
        for model in ["large-v3"]:
            model_dir = temp_cache_dir / f"whisper-{model}"
            model_dir.mkdir()
            (model_dir / "model.bin").write_bytes(b"x" * (100 * 1024))  # 100KB each

        total_size = manager.get_total_cache_size_mb()
        assert total_size > 0

    def test_delete_model_not_available(self, manager):
        """Test deleting model that doesn't exist."""
        result = manager.delete_model("large-v3")
        assert result is True

    def test_delete_model_available(self, manager, temp_cache_dir):
        """Test deleting available model."""
        # Create mock model
        base_dir = temp_cache_dir / "whisper-large-v3"
        base_dir.mkdir()
        (base_dir / "model.bin").touch()

        assert manager.is_model_available("large-v3")
        result = manager.delete_model("large-v3")
        assert result is True
        assert not manager.is_model_available("large-v3")

    def test_delete_model_invalid(self, manager):
        """Test deleting invalid model."""
        result = manager.delete_model("invalid-model")
        assert result is False

    @pytest.mark.asyncio
    async def test_download_model_already_available(self, manager, temp_cache_dir):
        """Test downloading model that already exists."""
        # Create mock model
        base_dir = temp_cache_dir / "whisper-large-v3"
        base_dir.mkdir()
        (base_dir / "model.bin").touch()

        result = await manager.download_model("large-v3")
        assert result is True

    @pytest.mark.asyncio
    async def test_download_model_invalid(self, manager):
        """Test downloading invalid model."""
        result = await manager.download_model("invalid-model")
        assert result is False

    def test_cleanup_old_versions(self, manager):
        """Test cleanup of old versions."""
        # This test depends on HuggingFace cache structure
        # For custom cache, should return 0
        result = manager.cleanup_old_versions("large-v3")
        assert result == 0

    def test_whisper_models_dict(self):
        """Test WHISPER_MODELS dictionary."""
        assert len(WHISPER_MODELS) == 1
        assert "large-v3" in WHISPER_MODELS

        
        for model_name, info in WHISPER_MODELS.items():
            assert "size_mb" in info
            assert "description" in info
            assert isinstance(info["size_mb"], int)
            assert isinstance(info["description"], str)

    def test_get_hf_cache_dir(self):
        """Test HuggingFace cache directory detection."""
        with patch.dict('os.environ', {}, clear=True):
            cache_dir = WhisperModelManager._get_hf_cache_dir()
            assert cache_dir is not None
            assert isinstance(cache_dir, Path)
