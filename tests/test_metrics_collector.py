"""
Unit tests for MetricsCollector (TIER C-4).

Tests stage metrics, resource metrics, cache metrics, and JSON export.
"""

import json
import time
from pathlib import Path

import pytest

from src.monitoring.metrics_collector import (
    MetricsCollector,
    StageMetrics,
    ResourceMetrics,
    CacheMetrics,
    get_metrics_collector,
)


@pytest.fixture
def collector():
    """Create a fresh metrics collector."""
    return MetricsCollector()


@pytest.fixture
def temp_json_path(tmp_path):
    """Create a temporary JSON file path."""
    return tmp_path / "metrics.json"


def test_stage_metrics_initialization():
    """Test StageMetrics initializes with correct defaults."""
    stage = StageMetrics(stage_name="fetch")
    
    assert stage.stage_name == "fetch"
    assert stage.count == 0
    assert stage.total_duration_s == 0.0
    assert stage.min_duration_s == float('inf')
    assert stage.max_duration_s == 0.0
    assert stage.errors == 0


def test_stage_metrics_properties():
    """Test StageMetrics computed properties."""
    stage = StageMetrics(
        stage_name="process",
        count=3,
        total_duration_s=1.5,
        min_duration_s=0.3,
        max_duration_s=0.7
    )
    
    assert stage.avg_duration_s == 0.5  # 1.5 / 3
    assert stage.throughput_per_s == 2.0  # 3 / 1.5


def test_resource_metrics_properties():
    """Test ResourceMetrics computed properties."""
    resources = ResourceMetrics(
        cpu_percent=[40.0, 50.0, 60.0],
        memory_mb=[1000.0, 1100.0, 1200.0],
        disk_io_mb=[10.0, 20.0],
        network_io_mb=[5.0, 15.0]
    )
    
    assert resources.avg_cpu_percent == 50.0
    assert resources.avg_memory_mb == 1100.0
    assert resources.avg_disk_io_mb == 15.0
    assert resources.avg_network_io_mb == 10.0


def test_cache_metrics_hit_rate():
    """Test CacheMetrics hit rate calculation."""
    cache = CacheMetrics(
        cache_name="test_cache",
        hits=80,
        misses=20
    )
    
    assert cache.hit_rate_pct == 80.0


def test_metrics_collector_initialization(collector):
    """Test MetricsCollector initializes empty."""
    assert len(collector.stages) == 0
    assert len(collector.resources.cpu_percent) == 0
    assert len(collector.caches) == 0


def test_record_stage_basic(collector):
    """Test basic stage recording."""
    collector.record_stage("fetch", 0.5)
    collector.record_stage("fetch", 0.3)
    collector.record_stage("fetch", 0.7, error=True)
    
    stage = collector.stages["fetch"]
    assert stage.count == 3
    assert stage.total_duration_s == 1.5
    assert stage.min_duration_s == 0.3
    assert stage.max_duration_s == 0.7
    assert stage.errors == 1
    assert stage.avg_duration_s == 0.5


def test_record_stage_multiple_stages(collector):
    """Test recording multiple different stages."""
    collector.record_stage("fetch", 1.0)
    collector.record_stage("process", 2.0)
    collector.record_stage("write", 0.5)
    
    assert len(collector.stages) == 3
    assert "fetch" in collector.stages
    assert "process" in collector.stages
    assert "write" in collector.stages


def test_record_resources(collector):
    """Test resource recording."""
    collector.record_resources(50.0, 1024.0, 100.0, 50.0)
    collector.record_resources(60.0, 1100.0, 120.0, 60.0)
    
    assert len(collector.resources.cpu_percent) == 2
    assert len(collector.resources.memory_mb) == 2
    assert collector.resources.avg_cpu_percent == 55.0
    assert collector.resources.avg_memory_mb == 1062.0


def test_record_cache(collector):
    """Test cache metrics recording."""
    collector.record_cache("sender_cache", 100, misses=10, evictions=5, size_bytes=1024*1024)

    assert "sender_cache" in collector.caches
    cache = collector.caches["sender_cache"]
    assert cache.hits == 100
    assert cache.misses == 10
    assert cache.hit_rate_pct == (100 / 110 * 100)
    assert cache.evictions == 5
    assert cache.size_bytes == 1024 * 1024


def test_export_json_basic(collector, temp_json_path):
    """Test JSON export with basic data."""
    collector.record_stage("fetch", 1.0)
    collector.record_resources(50.0, 1024.0, 100.0, 50.0)
    collector.record_cache("test_cache", 100, 10, 5, 1024*1024)
    
    collector.export_json(temp_json_path)
    
    assert temp_json_path.exists()
    
    with open(temp_json_path) as f:
        data = json.load(f)
    
    # Verify structure
    assert "start_time" in data
    assert "duration_s" in data
    assert "stages" in data
    assert "resources" in data
    assert "caches" in data
    
    # Verify stages
    assert "fetch" in data["stages"]
    assert data["stages"]["fetch"]["count"] == 1
    assert data["stages"]["fetch"]["total_duration_s"] == 1.0
    
    # Verify resources
    assert data["resources"]["avg_cpu_percent"] == 50.0
    assert data["resources"]["avg_memory_mb"] == 1024.0
    
    # Verify caches
    assert "test_cache" in data["caches"]
    assert data["caches"]["test_cache"]["hits"] == 100
    assert data["caches"]["test_cache"]["hit_rate_pct"] == (100 / 110 * 100)


def test_export_json_empty(collector, temp_json_path):
    """Test JSON export with no metrics."""
    collector.export_json(temp_json_path)
    
    with open(temp_json_path) as f:
        data = json.load(f)
    
    assert data["stages"] == {}
    assert data["resources"]["avg_cpu_percent"] == 0.0
    assert data["caches"] == {}


def test_get_metrics_collector_singleton():
    """Test global singleton returns same instance."""
    collector1 = get_metrics_collector()
    collector2 = get_metrics_collector()
    
    assert collector1 is collector2


def test_stage_metrics_zero_division_safety():
    """Test StageMetrics properties handle zero counts."""
    stage = StageMetrics(stage_name="test")
    
    # Should not raise ZeroDivisionError
    assert stage.avg_duration_s == 0.0
    assert stage.throughput_per_s == 0.0


def test_resource_metrics_empty_lists():
    """Test ResourceMetrics properties handle empty lists."""
    resources = ResourceMetrics()
    
    # Should not raise errors
    assert resources.avg_cpu_percent == 0.0
    assert resources.avg_memory_mb == 0.0
    assert resources.avg_disk_io_mb == 0.0
    assert resources.avg_network_io_mb == 0.0


def test_cache_metrics_zero_total():
    """Test CacheMetrics hit rate with zero total."""
    cache = CacheMetrics(cache_name="test", hits=0, misses=0)
    
    # Should not raise ZeroDivisionError
    assert cache.hit_rate_pct == 0.0
