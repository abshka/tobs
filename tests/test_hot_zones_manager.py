"""
Unit tests for HotZonesManager - Hot Zones & Density-Based Adaptive Chunking

Tests cover:
- Hot zone detection and overlap checking
- Optimal chunk size calculation
- Density estimation (mocked)
- Slow chunk recording and persistence
- Database save/load functionality
- Automatic hot zone learning from patterns
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Config
from src.hot_zones_manager import (
    DC2_DEFAULT_HOT_ZONES,
    HotZone,
    HotZonesManager,
    SlowChunkRecord,
)


@pytest.fixture
def temp_config():
    """Create a temporary config with test paths"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config(
            api_id=12345,
            api_hash="test_hash",
            export_path=Path(tmpdir),
            enable_hot_zones=True,
            enable_density_estimation=True,
            density_very_high_threshold=150.0,
            density_high_threshold=100.0,
            density_medium_threshold=50.0,
            chunk_size_very_high_density=5_000,
            chunk_size_high_density=10_000,
            chunk_size_medium_density=15_000,
            chunk_size_low_density=50_000,
            density_sample_points=3,
            density_sample_range=1_000,
            shard_chunk_size=50_000,  # Default
        )
        yield config


@pytest.fixture
def hot_zones_manager(temp_config):
    """Create HotZonesManager instance with temp config"""
    return HotZonesManager(temp_config)


class TestHotZoneBasics:
    """Test HotZone dataclass functionality"""

    def test_hot_zone_overlaps(self):
        """Test overlap detection between hot zones and ID ranges"""
        zone = HotZone(
            id_start=1_300_000,
            id_end=1_400_000,
            datacenter="DC2",
            optimal_chunk_size=5_000,
            avg_latency_sec=91.4,
            message_density=181.0,
            severity="CRITICAL",
            last_observed="2025-12-05",
            observation_count=1,
        )

        # Fully within zone
        assert zone.overlaps(1_320_000, 1_380_000) is True

        # Partially overlapping (start before zone)
        assert zone.overlaps(1_250_000, 1_350_000) is True

        # Partially overlapping (end after zone)
        assert zone.overlaps(1_350_000, 1_450_000) is True

        # Completely outside (before)
        assert zone.overlaps(1_200_000, 1_250_000) is False

        # Completely outside (after)
        assert zone.overlaps(1_500_000, 1_600_000) is False

        # Exact match
        assert zone.overlaps(1_300_000, 1_400_000) is True

    def test_hot_zone_contains(self):
        """Test if message ID falls within hot zone"""
        zone = HotZone(
            id_start=1_300_000,
            id_end=1_400_000,
            datacenter="DC2",
            optimal_chunk_size=5_000,
            avg_latency_sec=91.4,
            message_density=181.0,
            severity="CRITICAL",
            last_observed="2025-12-05",
        )

        assert zone.contains(1_350_000) is True
        assert zone.contains(1_300_000) is True  # Boundary
        assert zone.contains(1_400_000) is True  # Boundary
        assert zone.contains(1_250_000) is False
        assert zone.contains(1_450_000) is False


class TestHotZonesManagerInit:
    """Test HotZonesManager initialization"""

    def test_manager_loads_default_hot_zones(self, hot_zones_manager):
        """Test that default DC2 hot zones are loaded"""
        assert len(hot_zones_manager.hot_zones) == len(DC2_DEFAULT_HOT_ZONES)
        assert hot_zones_manager.hot_zones[0].id_start == 1_300_000
        assert hot_zones_manager.hot_zones[0].datacenter == "DC2"

    def test_manager_disabled_hot_zones(self, temp_config):
        """Test manager with hot zones disabled"""
        temp_config.enable_hot_zones = False
        manager = HotZonesManager(temp_config)
        assert len(manager.hot_zones) == 0

    def test_database_path_created(self, hot_zones_manager):
        """Test that monitoring directory and database path are created"""
        assert (
            hot_zones_manager.slow_chunk_db_path.exists() is False
        )  # Not created until first save
        monitoring_dir = hot_zones_manager.slow_chunk_db_path.parent
        assert monitoring_dir.exists() is True
        assert monitoring_dir.name == ".monitoring"


class TestOptimalChunkSize:
    """Test optimal chunk size calculation"""

    def test_chunk_size_critical_hot_zone(self, hot_zones_manager):
        """Test chunk size for CRITICAL hot zone (1.3M-1.4M)"""
        chunk_size = hot_zones_manager.get_optimal_chunk_size(
            1_320_000, 1_380_000, "DC2"
        )
        assert chunk_size == 5_000  # CRITICAL zone

    def test_chunk_size_high_hot_zone(self, hot_zones_manager):
        """Test chunk size for HIGH hot zone (1.4M-1.5M)"""
        chunk_size = hot_zones_manager.get_optimal_chunk_size(
            1_420_000, 1_480_000, "DC2"
        )
        assert chunk_size == 10_000  # HIGH zone

    def test_chunk_size_no_hot_zone(self, hot_zones_manager):
        """Test chunk size for range outside hot zones"""
        chunk_size = hot_zones_manager.get_optimal_chunk_size(
            2_000_000, 2_100_000, "DC2"
        )
        assert chunk_size == 50_000  # Default shard_chunk_size

    def test_chunk_size_different_dc(self, hot_zones_manager):
        """Test chunk size for different datacenter (no hot zones)"""
        chunk_size = hot_zones_manager.get_optimal_chunk_size(
            1_320_000, 1_380_000, "DC5"
        )
        assert chunk_size == 50_000  # No DC5 hot zones

    def test_chunk_size_overlapping_zones(self, hot_zones_manager):
        """Test that most severe zone is used for overlapping ranges"""
        # Add a test zone that overlaps with CRITICAL zone
        hot_zones_manager.hot_zones.append(
            HotZone(
                id_start=1_350_000,
                id_end=1_450_000,
                datacenter="DC2",
                optimal_chunk_size=8_000,
                avg_latency_sec=30.0,
                message_density=120.0,
                severity="MEDIUM",
                last_observed="2025-12-05",
            )
        )

        # Should use the more severe (smaller chunk size) zone
        chunk_size = hot_zones_manager.get_optimal_chunk_size(
            1_360_000, 1_380_000, "DC2"
        )
        assert chunk_size == 5_000  # Uses CRITICAL zone, not MEDIUM


class TestDensityBasedChunkSize:
    """Test density-based chunk size calculation"""

    def test_chunk_size_very_high_density(self, hot_zones_manager):
        """Test chunk size for very high density (>150 msgs/1K IDs)"""
        chunk_size = hot_zones_manager.get_chunk_size_for_density(180.0)
        assert chunk_size == 5_000

    def test_chunk_size_high_density(self, hot_zones_manager):
        """Test chunk size for high density (>100 msgs/1K IDs)"""
        chunk_size = hot_zones_manager.get_chunk_size_for_density(120.0)
        assert chunk_size == 10_000

    def test_chunk_size_medium_density(self, hot_zones_manager):
        """Test chunk size for medium density (>50 msgs/1K IDs)"""
        chunk_size = hot_zones_manager.get_chunk_size_for_density(70.0)
        assert chunk_size == 15_000

    def test_chunk_size_low_density(self, hot_zones_manager):
        """Test chunk size for low density (≤50 msgs/1K IDs)"""
        chunk_size = hot_zones_manager.get_chunk_size_for_density(30.0)
        assert chunk_size == 50_000


class TestDensityEstimation:
    """Test density estimation via sampling"""

    @pytest.mark.asyncio
    async def test_density_estimation_success(self, hot_zones_manager):
        """Test successful density estimation with mocked client"""
        # Mock Telethon client
        mock_client = AsyncMock()
        mock_entity = MagicMock()

        # Mock get_messages to return different counts for each sample
        async def mock_get_messages(entity, min_id=None, max_id=None, limit=None):
            # Simulate different message densities at different points
            return [MagicMock() for _ in range(150)]  # 150 messages per 1K ID range

        mock_client.get_messages = mock_get_messages

        density = await hot_zones_manager.estimate_density(
            mock_client, mock_entity, 1_000_000, 2_000_000
        )

        # With 150 msgs per 1000 IDs, density should be 150
        assert density == pytest.approx(150.0, rel=0.1)

    @pytest.mark.asyncio
    async def test_density_estimation_disabled(self, temp_config):
        """Test density estimation when disabled in config"""
        temp_config.enable_density_estimation = False
        manager = HotZonesManager(temp_config)

        mock_client = AsyncMock()
        mock_entity = MagicMock()

        density = await manager.estimate_density(
            mock_client, mock_entity, 1_000_000, 2_000_000
        )

        # Should return default value
        assert density == 50.0

    @pytest.mark.asyncio
    async def test_density_estimation_small_range(self, hot_zones_manager):
        """Test density estimation with range too small for sampling"""
        mock_client = AsyncMock()
        mock_entity = MagicMock()

        # Range smaller than 3x sample_range
        density = await hot_zones_manager.estimate_density(
            mock_client, mock_entity, 1_000_000, 1_002_000
        )

        # Should return default
        assert density == 50.0

    @pytest.mark.asyncio
    async def test_density_estimation_error_handling(self, hot_zones_manager):
        """Test density estimation with API errors"""
        mock_client = AsyncMock()
        mock_entity = MagicMock()

        # Mock get_messages to raise exception
        mock_client.get_messages = AsyncMock(side_effect=Exception("API Error"))

        density = await hot_zones_manager.estimate_density(
            mock_client, mock_entity, 1_000_000, 2_000_000
        )

        # Should return safe default
        assert density == 50.0


class TestSlowChunkRecording:
    """Test slow chunk recording and database persistence"""

    def test_record_slow_chunk(self, hot_zones_manager):
        """Test recording a slow chunk"""
        record = SlowChunkRecord(
            id_range=(1_320_000, 1_370_000),
            duration_sec=91.4,
            message_count=9056,
            density=181.0,
            datacenter="DC2",
            timestamp="2025-12-05T04:38:13Z",
            worker_id=3,
            chat_name="TestChat",
        )

        hot_zones_manager.record_slow_chunk(record)

        assert len(hot_zones_manager.slow_chunks) == 1
        assert hot_zones_manager.slow_chunks[0].duration_sec == 91.4

    def test_database_save_and_load(self, hot_zones_manager):
        """Test saving and loading database"""
        # Record some slow chunks
        record1 = SlowChunkRecord(
            id_range=(1_320_000, 1_370_000),
            duration_sec=91.4,
            message_count=9056,
            density=181.0,
            datacenter="DC2",
            timestamp="2025-12-05T04:38:13Z",
            worker_id=3,
        )
        hot_zones_manager.record_slow_chunk(record1)

        # Save database
        hot_zones_manager.save_database()
        assert hot_zones_manager.slow_chunk_db_path.exists()

        # Create new manager and load database
        new_manager = HotZonesManager(hot_zones_manager.config)

        # Should have loaded the slow chunk
        assert len(new_manager.slow_chunks) == 1
        assert new_manager.slow_chunks[0].duration_sec == 91.4

    def test_database_prunes_old_records(self, hot_zones_manager):
        """Test that database keeps only last 1000 chunks"""
        # Add 1200 chunks
        for i in range(1200):
            record = SlowChunkRecord(
                id_range=(1_000_000 + i * 1000, 1_001_000 + i * 1000),
                duration_sec=10.0 + i,
                message_count=100,
                density=50.0,
                datacenter="DC2",
                timestamp="2025-12-05T04:38:13Z",
                worker_id=0,
            )
            hot_zones_manager.record_slow_chunk(record)

        # Save
        hot_zones_manager.save_database()

        # Load and verify only last 1000 are kept
        with open(hot_zones_manager.slow_chunk_db_path, "r") as f:
            data = json.load(f)

        assert len(data["slow_chunks"]) == 1000


class TestHotZoneLearning:
    """Test automatic hot zone learning from patterns"""

    def test_create_new_critical_hot_zone(self, hot_zones_manager):
        """Test creating a new CRITICAL hot zone from very slow chunk"""
        initial_count = len(hot_zones_manager.hot_zones)

        slow_chunk = {
            "start_id": 800_000,
            "end_id": 850_000,
            "duration_sec": 80.0,  # Very slow
            "messages": 9000,
            "dc_id": 2,
        }

        hot_zones_manager.analyze_and_update_hot_zones(slow_chunk)

        # Should create new hot zone
        assert len(hot_zones_manager.hot_zones) == initial_count + 1

        # Find the new zone
        new_zone = [z for z in hot_zones_manager.hot_zones if z.id_start == 800_000][0]
        assert new_zone.severity == "CRITICAL"
        assert new_zone.optimal_chunk_size == 5_000

    def test_update_existing_hot_zone(self, hot_zones_manager):
        """Test updating statistics of existing hot zone"""
        # Get existing CRITICAL zone (1.3M-1.4M)
        zone = hot_zones_manager.hot_zones[0]
        initial_obs_count = zone.observation_count
        initial_latency = zone.avg_latency_sec

        # Simulate another slow chunk in same range
        slow_chunk = {
            "start_id": 1_320_000,
            "end_id": 1_370_000,
            "duration_sec": 60.0,  # Different latency
            "messages": 8000,
            "dc_id": 2,
        }

        hot_zones_manager.analyze_and_update_hot_zones(slow_chunk)

        # Should update existing zone, not create new one
        assert len(hot_zones_manager.hot_zones) == len(DC2_DEFAULT_HOT_ZONES)

        # Observation count should increase
        assert zone.observation_count == initial_obs_count + 1

        # Average latency should be updated (moving average)
        expected_avg = (initial_latency * initial_obs_count + 60.0) / (
            initial_obs_count + 1
        )
        assert zone.avg_latency_sec == pytest.approx(expected_avg, rel=0.01)

    def test_ignore_moderately_slow_chunk(self, hot_zones_manager):
        """Test that moderately slow chunks don't create hot zones"""
        initial_count = len(hot_zones_manager.hot_zones)

        # Not slow enough to create hot zone
        slow_chunk = {
            "start_id": 3_000_000,
            "end_id": 3_050_000,
            "duration_sec": 5.0,  # Moderately slow but not critical
            "messages": 1000,
            "dc_id": 2,
        }

        hot_zones_manager.analyze_and_update_hot_zones(slow_chunk)

        # Should NOT create new hot zone
        assert len(hot_zones_manager.hot_zones) == initial_count


class TestRecommendations:
    """Test recommendation generation"""

    def test_recommendations_for_dc2_dominance(self, hot_zones_manager):
        """Test recommendations when DC2 dominates slow chunks"""
        # Add many DC2 slow chunks
        for i in range(30):
            record = SlowChunkRecord(
                id_range=(1_000_000 + i * 10000, 1_010_000 + i * 10000),
                duration_sec=15.0,
                message_count=500,
                density=50.0,
                datacenter="DC2",
                timestamp="2025-12-05T04:38:13Z",
                worker_id=0,
            )
            hot_zones_manager.record_slow_chunk(record)

        recommendations = hot_zones_manager.get_recommendations()

        # Should recommend DC2-specific action
        assert len(recommendations) > 0
        assert any("DC2" in rec for rec in recommendations)

    def test_recommendations_for_high_density(self, hot_zones_manager):
        """Test recommendations for high-density patterns"""
        # Add high-density slow chunks
        for i in range(10):
            record = SlowChunkRecord(
                id_range=(1_000_000 + i * 10000, 1_010_000 + i * 10000),
                duration_sec=20.0,
                message_count=1800,  # Very high density
                density=180.0,
                datacenter="DC2",
                timestamp="2025-12-05T04:38:13Z",
                worker_id=0,
            )
            hot_zones_manager.record_slow_chunk(record)

        recommendations = hot_zones_manager.get_recommendations()

        # Should mention high density
        assert len(recommendations) > 0
        assert any("density" in rec.lower() for rec in recommendations)

    def test_recommendations_for_critical_zones(self, hot_zones_manager):
        """Test recommendations mention critical zones"""
        recommendations = hot_zones_manager.get_recommendations()

        # Should mention CRITICAL zones (from defaults)
        assert any("CRITICAL" in rec for rec in recommendations)


class TestGetHotZonesForRange:
    """Test querying hot zones for specific ranges"""

    def test_get_overlapping_zones(self, hot_zones_manager):
        """Test getting all overlapping zones"""
        zones = hot_zones_manager.get_hot_zones_for_range(1_320_000, 1_450_000, "DC2")

        # Should match CRITICAL zone (1.3M-1.4M) and HIGH zone (1.4M-1.5M)
        assert len(zones) >= 2
        assert any(z.severity == "CRITICAL" for z in zones)
        assert any(z.severity == "HIGH" for z in zones)

    def test_get_zones_no_overlap(self, hot_zones_manager):
        """Test querying range with no overlapping zones"""
        zones = hot_zones_manager.get_hot_zones_for_range(5_000_000, 6_000_000, "DC2")

        # Should be empty
        assert len(zones) == 0

    def test_get_zones_different_dc(self, hot_zones_manager):
        """Test querying different datacenter"""
        zones = hot_zones_manager.get_hot_zones_for_range(1_320_000, 1_380_000, "DC5")

        # No DC5 zones
        assert len(zones) == 0
