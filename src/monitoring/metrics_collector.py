"""
Comprehensive metrics collection for TOBS performance monitoring.

Provides per-stage latency tracking, resource utilization monitoring,
and cache effectiveness analysis.

Part of TIER C-4 optimization.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict
import json

# Graceful logger import (fallback to standard logging if src.utils unavailable)
try:
    from src.utils import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


def _ensure_list(v):
    """Utility: coerce a scalar or iterable to a list of floats."""
    if v is None:
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    try:
        return [float(x) for x in v]
    except Exception:
        return [float(v)]


def _combine_lists(a, b):
    """Combine two numeric sequences element-wise with broadcasting and length handling."""
    a = _ensure_list(a)
    b = _ensure_list(b)
    if not a and not b:
        return []
    if not a:
        return [0.0 + bi for bi in b]
    if not b:
        return [ai + 0.0 for ai in a]
    if len(a) == len(b):
        return [ai + bi for ai, bi in zip(a, b)]
    if len(a) == 1:
        return [a[0] + bi for bi in b]
    if len(b) == 1:
        return [ai + b[0] for ai in a]
    min_len = min(len(a), len(b))
    return [a[i] + b[i] for i in range(min_len)]


@dataclass
class StageMetrics:
    """Metrics for a single processing stage (fetch, process, write, etc.)."""
    
    stage_name: str
    count: int = 0
    total_duration_s: float = 0.0
    min_duration_s: float = float('inf')
    max_duration_s: float = 0.0
    errors: int = 0
    
    @property
    def avg_duration_s(self) -> float:
        """Average duration per operation."""
        return self.total_duration_s / self.count if self.count > 0 else 0.0
    
    @property
    def throughput_per_s(self) -> float:
        """Operations per second throughput."""
        return self.count / self.total_duration_s if self.total_duration_s > 0 else 0.0


@dataclass
class ResourceMetrics:
    """System resource utilization metrics (CPU, memory, I/O).
    
    Supports both legacy single-sample usage (scalar fields like cpu_percent=45.5,
    disk_read_mb=10.0, disk_write_mb=50.0) and list-based sampling. Scalars are coerced
    to single-element lists and read/write fields are combined into total disk/network
    IO when needed.
    """

    cpu_percent: list[float] | float | None = field(default_factory=list)
    memory_mb: list[float] | float | None = field(default_factory=list)
    disk_io_mb: list[float] | float | None = field(default_factory=list)
    network_io_mb: list[float] | float | None = field(default_factory=list)

    # Backwards-compatible fields (sometimes tests provide separate read/write and sent/recv)
    disk_read_mb: list[float] | float | None = field(default_factory=list)
    disk_write_mb: list[float] | float | None = field(default_factory=list)
    network_sent_mb: list[float] | float | None = field(default_factory=list)
    network_recv_mb: list[float] | float | None = field(default_factory=list)

    timestamp: float | None = None

    def __post_init__(self):
        # Coerce scalars to lists and normalize inputs
        self.cpu_percent = _ensure_list(self.cpu_percent)
        self.memory_mb = _ensure_list(self.memory_mb)
        self.disk_io_mb = _ensure_list(self.disk_io_mb)
        self.network_io_mb = _ensure_list(self.network_io_mb)
        self.disk_read_mb = _ensure_list(self.disk_read_mb)
        self.disk_write_mb = _ensure_list(self.disk_write_mb)
        self.network_sent_mb = _ensure_list(self.network_sent_mb)
        self.network_recv_mb = _ensure_list(self.network_recv_mb)

        # Construct disk_io list from read/write if needed
        if not self.disk_io_mb and (self.disk_read_mb or self.disk_write_mb):
            self.disk_io_mb = _combine_lists(self.disk_read_mb, self.disk_write_mb)

        # Construct network_io list from sent/recv if needed
        if not self.network_io_mb and (self.network_sent_mb or self.network_recv_mb):
            self.network_io_mb = _combine_lists(self.network_sent_mb, self.network_recv_mb)

    @property
    def avg_cpu_percent(self) -> float:
        """Average CPU utilization percentage."""
        return sum(self.cpu_percent) / len(self.cpu_percent) if self.cpu_percent else 0.0

    @property
    def avg_memory_mb(self) -> float:
        """Average memory usage in MB."""
        return sum(self.memory_mb) / len(self.memory_mb) if self.memory_mb else 0.0

    @property
    def avg_disk_io_mb(self) -> float:
        """Average disk I/O in MB."""
        return sum(self.disk_io_mb) / len(self.disk_io_mb) if self.disk_io_mb else 0.0

    @property
    def avg_network_io_mb(self) -> float:
        """Average network I/O in MB."""
        return sum(self.network_io_mb) / len(self.network_io_mb) if self.network_io_mb else 0.0


@dataclass
class CacheMetrics:
    """Cache effectiveness metrics (hit rate, evictions, size)."""

    cache_name: str | None = None
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size_bytes: int = 0

    @property
    def hit_rate_pct(self) -> float:
        """Cache hit rate percentage."""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


class MetricsCollector:
    """
    Central metrics collection and export system.
    
    Tracks:
    - Per-stage execution metrics (fetch, process, write)
    - System resource utilization (CPU, memory, disk/network I/O)
    - Cache effectiveness (hits, misses, hit rate)
    
    Features:
    - Zero-overhead when not used (lazy initialization)
    - JSON export for analysis and auto-tuning
    - Thread-safe for asyncio usage
    
    Typical usage:
        metrics = get_metrics_collector()
        
        # Record stage execution
        metrics.record_stage("fetch", duration_s=0.5)
        
        # Record resources (periodic)
        metrics.record_resources(cpu_pct=50.0, memory_mb=1024.0, disk_io_mb=100.0, network_io_mb=50.0)
        
        # Record cache metrics
        metrics.record_cache("sender_cache", hits=100, misses=10, evictions=5, size_bytes=1024*1024)
        
        # Export to JSON at end
        metrics.export_json(Path("metrics.json"))
    """
    
    def __init__(self):
        """Initialize the metrics collector."""
        self.stages: Dict[str, StageMetrics] = {}
        self.resources = ResourceMetrics()
        self.caches: Dict[str, CacheMetrics] = {}
        self.start_time = datetime.now()
    
    def record_stage(self, stage_name: str, duration_s: float, error: bool = False) -> None:
        """
        Record stage execution metrics.
        
        Args:
            stage_name: Name of the stage (e.g., "fetch", "process", "write")
            duration_s: Duration in seconds
            error: Whether the stage encountered an error
        """
        if stage_name not in self.stages:
            self.stages[stage_name] = StageMetrics(stage_name=stage_name)
        
        stage = self.stages[stage_name]
        stage.count += 1
        stage.total_duration_s += duration_s
        stage.min_duration_s = min(stage.min_duration_s, duration_s)
        stage.max_duration_s = max(stage.max_duration_s, duration_s)
        if error:
            stage.errors += 1
    
    def record_resources(
        self,
        cpu_pct: float,
        memory_mb: float,
        disk_io_mb: float,
        network_io_mb: float
    ) -> None:
        """
        Record resource utilization snapshot.
        
        Args:
            cpu_pct: CPU utilization percentage
            memory_mb: Memory usage in megabytes
            disk_io_mb: Disk I/O in megabytes
            network_io_mb: Network I/O in megabytes
        """
        self.resources.cpu_percent.append(cpu_pct)
        self.resources.memory_mb.append(memory_mb)
        self.resources.disk_io_mb.append(disk_io_mb)
        self.resources.network_io_mb.append(network_io_mb)

    def record_resource(self, resource: ResourceMetrics) -> None:
        """
        Record a ResourceMetrics instance (backwards-compatible).

        Accepts either:
        - a single-sample ResourceMetrics constructed with scalar fields (e.g., cpu_percent=45.5)
        - a ResourceMetrics containing lists of samples

        This function will append/extend the internal lists accordingly.
        """
        # CPU and memory
        self.resources.cpu_percent.extend(resource.cpu_percent)
        self.resources.memory_mb.extend(resource.memory_mb)

        # Disk I/O: prefer disk_io_mb if present, otherwise combine read/write
        if resource.disk_io_mb:
            self.resources.disk_io_mb.extend(resource.disk_io_mb)
        else:
            disk_combined = _combine_lists(resource.disk_read_mb, resource.disk_write_mb)
            if disk_combined:
                self.resources.disk_io_mb.extend(disk_combined)

        # Network I/O: prefer network_io_mb if present, otherwise combine sent/recv
        if resource.network_io_mb:
            self.resources.network_io_mb.extend(resource.network_io_mb)
        else:
            net_combined = _combine_lists(resource.network_sent_mb, resource.network_recv_mb)
            if net_combined:
                self.resources.network_io_mb.extend(net_combined)

    
    def record_cache(
        self,
        cache_name: str,
        hits_or_cache,
        misses: int | None = None,
        evictions: int | None = None,
        size_bytes: int | None = None
    ) -> None:
        """
        Record cache metrics snapshot.

        Can accept either:
        - individual fields (hits, misses, evictions, size_bytes)
        - a CacheMetrics instance as the second argument
        """
        # Support passing a CacheMetrics instance for backwards compatibility
        if isinstance(hits_or_cache, CacheMetrics):
            self.caches[cache_name] = hits_or_cache
            return

        # Otherwise, treat as individual fields
        hits = hits_or_cache if hits_or_cache is not None else 0
        misses = misses or 0
        evictions = evictions or 0
        size_bytes = size_bytes or 0

        self.caches[cache_name] = CacheMetrics(
            cache_name=cache_name,
            hits=hits,
            misses=misses,
            evictions=evictions,
            size_bytes=size_bytes
        )
    
    def export_json(self, output_path: Path | None = None) -> dict:
        """
        Export all metrics to JSON file or return the data dictionary if no path is given.
        
        Args:
            output_path: Optional Path to output JSON file. If None, the data dict is returned.
        """
        total_duration_s = (datetime.now() - self.start_time).total_seconds()

        data = {
            "start_time": self.start_time.isoformat(),
            "duration_s": total_duration_s,
            "stages": {
                name: {
                    "count": s.count,
                    "total_duration_s": s.total_duration_s,
                    "avg_duration_s": s.avg_duration_s,
                    "min_duration_s": s.min_duration_s if s.min_duration_s != float('inf') else 0.0,
                    "max_duration_s": s.max_duration_s,
                    "throughput_per_s": s.throughput_per_s,
                    "errors": s.errors
                }
                for name, s in self.stages.items()
            },
            "resources": {
                "avg_cpu_percent": self.resources.avg_cpu_percent,
                "avg_memory_mb": self.resources.avg_memory_mb,
                "avg_disk_io_mb": self.resources.avg_disk_io_mb,
                "avg_network_io_mb": self.resources.avg_network_io_mb,
                "sample_count": len(self.resources.cpu_percent),
                "cpu_samples": len(self.resources.cpu_percent),
                "memory_samples": len(self.resources.memory_mb)
            },
            "caches": {
                name: {
                    "hits": c.hits,
                    "misses": c.misses,
                    "hit_rate_pct": c.hit_rate_pct,
                    "evictions": c.evictions,
                    "size_mb": c.size_bytes / 1024 / 1024
                }
                for name, c in self.caches.items()
            }
        }

        # If no output path is provided, return the data dictionary
        if output_path is None:
            return data

        # Ensure parent directory exists and write JSON with pretty print
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"📊 Metrics exported to {output_path}")
        return data


# Global singleton
_metrics_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector (lazy initialization)."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector
