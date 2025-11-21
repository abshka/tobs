"""
Tests for Config path management.

Tests cover:
- _update_target_paths with different folder structures
- Path getters (export, media, cache, monitoring)
- Entity folder name generation
- add_export_target functionality
"""

from unittest.mock import MagicMock, patch


from src.config import Config, ExportTarget


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigPathManagementStructured:
    """Test path management with structured export (use_entity_folders=True, use_structured_export=True)."""

    def test_structured_export_paths(self, mock_mkdir, mock_memory, mock_disk):
        """Test structured export creates entity_name/ with subfolders."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@channel1", name="Test Channel")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets,
            use_entity_folders=True,
            use_structured_export=True
        )

        # Verify paths are structured: entity_name/subdir
        export_path = config.get_export_path_for_entity("@channel1")
        media_path = config.get_media_path_for_entity("@channel1")
        cache_path = config.get_cache_path_for_entity("@channel1")
        monitoring_path = config.get_monitoring_path_for_entity("@channel1")

        # All should be under Test Channel folder (sanitize_filename keeps spaces)
        assert "Test Channel" in str(export_path)
        assert export_path.name == "Test Channel"  # Export is entity base
        assert media_path.parent.name == "Test Channel"
        assert media_path.name == "media"
        assert cache_path.parent.name == "Test Channel"
        assert cache_path.name == "cache"
        assert monitoring_path.parent.name == "Test Channel"
        assert monitoring_path.name == "monitoring"

    def test_multiple_targets_structured(self, mock_mkdir, mock_memory, mock_disk):
        """Test multiple targets each get their own structured folder."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@channel1", name="Channel One"),
            ExportTarget(id="-1001234567890", name="Channel Two")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets,
            use_entity_folders=True,
            use_structured_export=True
        )

        # Each target has its own folder
        path1 = config.get_export_path_for_entity("@channel1")
        path2 = config.get_export_path_for_entity("-1001234567890")

        assert "Channel One" in str(path1)
        assert "Channel Two" in str(path2)
        assert path1 != path2


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigPathManagementLegacy:
    """Test path management with legacy export (use_entity_folders=True, use_structured_export=False)."""

    def test_legacy_export_paths(self, mock_mkdir, mock_memory, mock_disk):
        """Test legacy export creates entity_name/ with _media subfolder."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@channel1", name="Old Channel")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets,
            use_entity_folders=True,
            use_structured_export=False
        )

        # Verify legacy structure: entity_name/_media
        export_path = config.get_export_path_for_entity("@channel1")
        media_path = config.get_media_path_for_entity("@channel1")

        assert "Old Channel" in str(export_path)
        assert export_path.name == "Old Channel"
        assert media_path.parent.name == "Old Channel"
        assert media_path.name == "_media"  # Legacy uses _media


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigPathManagementFlat:
    """Test path management with flat structure (use_entity_folders=False)."""

    def test_flat_export_paths(self, mock_mkdir, mock_memory, mock_disk):
        """Test flat export uses export_path root directly."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@channel1", name="Flat Channel")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets,
            use_entity_folders=False
        )

        # All paths should be at root level
        export_path = config.get_export_path_for_entity("@channel1")
        media_path = config.get_media_path_for_entity("@channel1")

        assert export_path == config.export_path
        assert media_path.parent == config.export_path
        assert media_path.name == "media"


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigPathGetters:
    """Test path getter methods."""

    def test_get_export_path_for_unknown_entity_returns_default(self, mock_mkdir, mock_memory, mock_disk):
        """Test getting export path for unknown entity returns default export_path."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        config = Config(api_id=12345, api_hash="a" * 32)

        # Unknown entity should return default export_path
        path = config.get_export_path_for_entity("@unknown")
        assert path == config.export_path

    def test_get_media_path_for_unknown_entity_returns_default(self, mock_mkdir, mock_memory, mock_disk):
        """Test getting media path for unknown entity returns default."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        config = Config(api_id=12345, api_hash="a" * 32)

        path = config.get_media_path_for_entity("@unknown")
        assert path == config.export_path / config.media_subdir

    def test_get_cache_path_for_unknown_entity_returns_default(self, mock_mkdir, mock_memory, mock_disk):
        """Test getting cache path for unknown entity returns default."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        config = Config(api_id=12345, api_hash="a" * 32)

        path = config.get_cache_path_for_entity("@unknown")
        assert path == config.export_path / config.cache_subdir

    def test_get_monitoring_path_for_unknown_entity_returns_default(self, mock_mkdir, mock_memory, mock_disk):
        """Test getting monitoring path for unknown entity returns default."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        config = Config(api_id=12345, api_hash="a" * 32)

        path = config.get_monitoring_path_for_entity("@unknown")
        assert path == config.export_path / config.monitoring_subdir


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigEntityFolderName:
    """Test _get_entity_folder_name method."""

    def test_entity_folder_name_sanitized(self, mock_mkdir, mock_memory, mock_disk):
        """Test entity folder names are sanitized."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@channel1", name="My: Test/Channel*")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets,
            use_entity_folders=True
        )

        # Name should be sanitized (no special chars)
        path = config.get_export_path_for_entity("@channel1")
        # sanitize_filename should replace special chars
        assert ":" not in str(path)
        assert "/" not in path.name
        assert "*" not in str(path)

    def test_entity_folder_name_uses_id_when_no_name(self, mock_mkdir, mock_memory, mock_disk):
        """Test entity folder uses id_ prefix when name is empty."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@channel1", name="")  # Empty name
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets,
            use_entity_folders=True
        )

        path = config.get_export_path_for_entity("@channel1")
        # Should use "id_@channel1"
        assert "id_" in str(path) or "@channel1" in str(path)


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigAddExportTarget:
    """Test add_export_target method."""

    def test_add_export_target_new(self, mock_mkdir, mock_memory, mock_disk):
        """Test adding a new export target."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        config = Config(api_id=12345, api_hash="a" * 32)

        assert len(config.export_targets) == 0

        new_target = ExportTarget(id="@newchannel", name="New Channel")
        config.add_export_target(new_target)

        assert len(config.export_targets) == 1
        assert config.export_targets[0].id == "@newchannel"
        # Paths should be updated
        assert "@newchannel" in config.export_paths

    def test_add_export_target_duplicate_skipped(self, mock_mkdir, mock_memory, mock_disk):
        """Test adding duplicate target is skipped."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        targets = [
            ExportTarget(id="@existing", name="Existing")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets
        )

        assert len(config.export_targets) == 1

        # Try to add same target again
        duplicate = ExportTarget(id="@existing", name="Different Name")
        config.add_export_target(duplicate)

        # Should still be 1
        assert len(config.export_targets) == 1
