"""
Hot Zones Manager - Adaptive Chunking Based on Empirical Performance Data

This module manages known problematic ID ranges (hot zones) in Telegram datacenters
and provides density-based adaptive chunking to optimize export performance.

Key Features:
- Pre-defined DC2 hot zones from empirical testing
- Density estimation via 3-point sampling
- Persistent slow-range database (JSON)
- Automatic hot zone learning from export history
- Optimal chunk size calculation based on ID range and density

Root Cause: DC2 has specific message ID ranges (especially 1.3M-1.4M) that exhibit
extremely high latency (60-90s per chunk). Combined with high message density
(>100 msgs/1K IDs), this creates severe bottlenecks during export.

Solution: Pre-split known hot ranges into small chunks (5K-10K IDs) and dynamically
adapt chunk sizes based on estimated message density.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.config import Config

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HotZone:
    """
    Represents a known problematic ID range in a specific datacenter.

    Attributes:
        id_start: Starting message ID of hot zone
        id_end: Ending message ID of hot zone
        datacenter: DC identifier (e.g., "DC2")
        optimal_chunk_size: Recommended chunk size (IDs per chunk)
        avg_latency_sec: Average observed latency in seconds
        message_density: Average messages per 1000 IDs
        severity: Risk level ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        last_observed: ISO timestamp of last observation
        observation_count: Number of times this zone was observed slow
    """

    id_start: int
    id_end: int
    datacenter: str
    optimal_chunk_size: int
    avg_latency_sec: float
    message_density: float
    severity: str
    last_observed: str
    observation_count: int = 1

    def overlaps(self, id_start: int, id_end: int) -> bool:
        """Check if given range overlaps with this hot zone"""
        return not (id_end < self.id_start or id_start > self.id_end)

    def contains(self, message_id: int) -> bool:
        """Check if message ID falls within this hot zone"""
        return self.id_start <= message_id <= self.id_end


@dataclass(slots=True)
class SlowChunkRecord:
    """
    Record of a slow chunk for persistent database.

    Attributes:
        id_range: Tuple of (start_id, end_id)
        duration_sec: Time taken to fetch chunk
        message_count: Number of messages in chunk
        density: Messages per 1000 IDs
        datacenter: DC identifier
        timestamp: ISO timestamp of observation
        worker_id: Worker thread that fetched this chunk
        chat_name: Optional chat identifier (for debugging)
    """

    id_range: Tuple[int, int]
    duration_sec: float
    message_count: int
    density: float
    datacenter: str
    timestamp: str
    worker_id: int
    chat_name: Optional[str] = None


# Default hot zones from empirical testing (4 DC2 exports)
DC2_DEFAULT_HOT_ZONES = [
    HotZone(
        id_start=1_300_000,
        id_end=1_400_000,
        datacenter="DC2",
        optimal_chunk_size=5_000,
        avg_latency_sec=91.4,
        message_density=181.0,
        severity="CRITICAL",
        last_observed="2025-12-05",
        observation_count=1,
    ),
    HotZone(
        id_start=1_400_000,
        id_end=1_500_000,
        datacenter="DC2",
        optimal_chunk_size=10_000,
        avg_latency_sec=3.6,
        message_density=175.0,
        severity="HIGH",
        last_observed="2025-12-05",
        observation_count=2,
    ),
    HotZone(
        id_start=1_600_000,
        id_end=1_700_000,
        datacenter="DC2",
        optimal_chunk_size=10_000,
        avg_latency_sec=3.3,
        message_density=149.0,
        severity="HIGH",
        last_observed="2025-12-05",
        observation_count=3,
    ),
    HotZone(
        id_start=700_000,
        id_end=1_000_000,
        datacenter="DC2",
        optimal_chunk_size=15_000,
        avg_latency_sec=32.0,
        message_density=90.0,
        severity="MEDIUM",
        last_observed="2025-12-05",
        observation_count=1,
    ),
]


class HotZonesManager:
    """
    Manages hot zones and provides adaptive chunking recommendations.

    This class:
    1. Loads known hot zones from defaults + persistent database
    2. Estimates message density via sampling
    3. Calculates optimal chunk sizes based on ID range and density
    4. Records slow chunks to persistent database
    5. Auto-learns new hot zones from patterns
    """

    def __init__(self, config: Config):
        """
        Initialize HotZonesManager.

        Args:
            config: TOBS configuration object
        """
        self.config = config
        self.hot_zones: List[HotZone] = []
        self.slow_chunks: List[SlowChunkRecord] = []

        # Database path: .monitoring/slow_ranges_db.json
        monitoring_dir = Path(config.export_path) / ".monitoring"
        monitoring_dir.mkdir(parents=True, exist_ok=True)
        self.slow_chunk_db_path = monitoring_dir / "slow_ranges_db.json"

        self._load_hot_zones()
        self._load_slow_chunk_database()

    def _load_hot_zones(self):
        """Load default hot zones (can be extended with user-defined zones)"""
        if self.config.enable_hot_zones:
            self.hot_zones = DC2_DEFAULT_HOT_ZONES.copy()
            logger.info(f"🔥 Loaded {len(self.hot_zones)} default hot zones")
        else:
            logger.info("⚠️ Hot zones disabled in configuration")

    def _load_slow_chunk_database(self):
        """Load persistent slow-chunk database from JSON"""
        if not self.slow_chunk_db_path.exists():
            logger.debug(
                f"💾 No existing slow-range database at {self.slow_chunk_db_path}"
            )
            return

        try:
            with open(self.slow_chunk_db_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Load hot zones from database (merge with defaults)
            if "hot_zones" in data:
                db_zones = [HotZone(**zone) for zone in data["hot_zones"]]
                # Merge with defaults, preferring database values for overlaps
                for db_zone in db_zones:
                    # Check if zone overlaps with existing
                    overlapping = [
                        z
                        for z in self.hot_zones
                        if z.overlaps(db_zone.id_start, db_zone.id_end)
                    ]
                    if overlapping:
                        # Update existing zone with database values
                        for existing_zone in overlapping:
                            if existing_zone.id_start == db_zone.id_start:
                                # Exact match - update in place
                                idx = self.hot_zones.index(existing_zone)
                                self.hot_zones[idx] = db_zone
                                break
                    else:
                        # New zone from database
                        self.hot_zones.append(db_zone)

            # Load slow chunk history
            if "slow_chunks" in data:
                self.slow_chunks = [
                    SlowChunkRecord(
                        id_range=tuple(chunk["id_range"]),
                        duration_sec=chunk["duration_sec"],
                        message_count=chunk["message_count"],
                        density=chunk["density"],
                        datacenter=chunk["datacenter"],
                        timestamp=chunk["timestamp"],
                        worker_id=chunk["worker_id"],
                        chat_name=chunk.get("chat_name"),
                    )
                    for chunk in data["slow_chunks"]
                ]

            logger.info(
                f"💾 Loaded slow-range database: {len(self.hot_zones)} hot zones, {len(self.slow_chunks)} slow chunks"
            )

        except Exception as e:
            logger.warning(f"⚠️ Failed to load slow-range database: {e}")

    def save_database(self):
        """Save hot zones and slow chunks to persistent JSON database"""
        try:
            data = {
                "version": "1.0",
                "last_updated": datetime.now().isoformat(),
                "hot_zones": [asdict(zone) for zone in self.hot_zones],
                "slow_chunks": [
                    {
                        "id_range": list(chunk.id_range),
                        "duration_sec": chunk.duration_sec,
                        "message_count": chunk.message_count,
                        "density": chunk.density,
                        "datacenter": chunk.datacenter,
                        "timestamp": chunk.timestamp,
                        "worker_id": chunk.worker_id,
                        "chat_name": chunk.chat_name,
                    }
                    for chunk in self.slow_chunks[-1000:]  # Keep last 1000 chunks
                ],
            }

            with open(self.slow_chunk_db_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug(f"💾 Saved slow-range database to {self.slow_chunk_db_path}")

        except Exception as e:
            logger.error(f"❌ Failed to save slow-range database: {e}")

    def get_optimal_chunk_size(
        self, id_start: int, id_end: int, datacenter: str
    ) -> int:
        """
        Get optimal chunk size for given ID range and datacenter.

        Args:
            id_start: Starting message ID
            id_end: Ending message ID
            datacenter: DC identifier (e.g., "DC2")

        Returns:
            Optimal chunk size in IDs (5K-50K)
        """
        if not self.config.enable_hot_zones:
            return self.config.shard_chunk_size  # Default

        # Check if range overlaps with any hot zones
        overlapping_zones = self.get_hot_zones_for_range(id_start, id_end, datacenter)

        if overlapping_zones:
            # Use most severe (smallest chunk size) hot zone
            most_severe = min(overlapping_zones, key=lambda z: z.optimal_chunk_size)
            logger.debug(
                f"🔥 Hot zone detected: {most_severe.id_start}-{most_severe.id_end} "
                f"(severity: {most_severe.severity}, chunk size: {most_severe.optimal_chunk_size})"
            )
            return most_severe.optimal_chunk_size

        # No hot zone matched - use default
        return self.config.shard_chunk_size

    def get_hot_zones_for_range(
        self, id_start: int, id_end: int, datacenter: str
    ) -> List[HotZone]:
        """
        Get all hot zones overlapping with given range.

        Args:
            id_start: Starting message ID
            id_end: Ending message ID
            datacenter: DC identifier

        Returns:
            List of overlapping hot zones
        """
        return [
            zone
            for zone in self.hot_zones
            if zone.datacenter == datacenter and zone.overlaps(id_start, id_end)
        ]

    async def estimate_density(
        self, client, entity, id_start: int, id_end: int
    ) -> float:
        """
        Estimate message density by sampling 3 points across the ID range.

        Samples at: start, middle, end of range (±1000 IDs around each point).

        Args:
            client: Telethon client
            entity: Chat entity
            id_start: Starting message ID
            id_end: Ending message ID

        Returns:
            Estimated density (messages per 1000 IDs)
        """
        if not self.config.enable_density_estimation:
            logger.debug("⚠️ Density estimation disabled in config")
            return 50.0  # Default assumption

        try:
            sample_points = self.config.density_sample_points
            sample_range = self.config.density_sample_range

            # Calculate sample positions
            span = id_end - id_start
            if span < sample_range * 3:
                # Range too small for meaningful sampling
                logger.debug(f"⚠️ Range too small for density sampling ({span} IDs)")
                return 50.0

            positions = []
            if sample_points == 3:
                positions = [id_start, id_start + span // 2, id_end]
            else:
                # Evenly distribute samples
                step = span // (sample_points + 1)
                positions = [id_start + i * step for i in range(1, sample_points + 1)]

            samples = []
            for pos in positions:
                try:
                    # Fetch small range around sample point
                    msgs = await client.get_messages(
                        entity,
                        min_id=max(1, pos - sample_range // 2),
                        max_id=pos + sample_range // 2,
                        limit=None,
                    )
                    samples.append(len(msgs))
                    logger.debug(
                        f"🔍 Sample at ID {pos}: {len(msgs)} messages in {sample_range} ID range"
                    )
                except Exception as e:
                    logger.debug(f"⚠️ Failed to sample at ID {pos}: {e}")
                    continue

            if not samples:
                logger.warning("⚠️ All density samples failed, using default")
                return 50.0

            # Calculate average density (msgs per 1000 IDs)
            avg_density = sum(samples) / len(samples) / sample_range * 1000
            logger.info(
                f"📊 Estimated density: {avg_density:.1f} msgs/1K IDs (from {len(samples)} samples)"
            )

            return avg_density

        except Exception as e:
            logger.warning(f"⚠️ Density estimation failed: {e}")
            return 50.0  # Safe default

    def get_chunk_size_for_density(self, density: float) -> int:
        """
        Get optimal chunk size based on message density.

        Args:
            density: Messages per 1000 IDs

        Returns:
            Optimal chunk size in IDs
        """
        if density > self.config.density_very_high_threshold:
            return self.config.chunk_size_very_high_density
        elif density > self.config.density_high_threshold:
            return self.config.chunk_size_high_density
        elif density > self.config.density_medium_threshold:
            return self.config.chunk_size_medium_density
        else:
            return self.config.chunk_size_low_density

    def record_slow_chunk(self, chunk_record: SlowChunkRecord):
        """
        Record a slow chunk to persistent database.

        Args:
            chunk_record: Slow chunk record to save
        """
        self.slow_chunks.append(chunk_record)
        logger.debug(
            f"💾 Recorded slow chunk: {chunk_record.id_range[0]}-{chunk_record.id_range[1]} "
            f"({chunk_record.duration_sec:.1f}s, density: {chunk_record.density:.1f})"
        )

    def analyze_and_update_hot_zones(self, slow_chunk_dict: Dict):
        """
        Analyze slow chunk and potentially create/update hot zones.

        This method learns from export history: if a specific ID range is
        consistently slow, it gets promoted to a hot zone.

        Args:
            slow_chunk_dict: Dictionary with slow chunk data from worker stats
        """
        try:
            start_id = slow_chunk_dict["start_id"]
            end_id = slow_chunk_dict["end_id"]
            duration = slow_chunk_dict["duration_sec"]
            messages = slow_chunk_dict["messages"]
            dc_id = slow_chunk_dict.get("dc_id", 0)

            # Calculate density
            span = end_id - start_id
            density = (messages / span * 1000) if span > 0 else 0

            datacenter = f"DC{dc_id}" if dc_id > 0 else "Unknown"

            # Check if this range is already a hot zone
            overlapping = self.get_hot_zones_for_range(start_id, end_id, datacenter)

            if overlapping:
                # Update existing hot zone statistics
                for zone in overlapping:
                    # Update moving average
                    old_count = zone.observation_count
                    new_count = old_count + 1
                    zone.avg_latency_sec = (
                        zone.avg_latency_sec * old_count + duration
                    ) / new_count
                    zone.message_density = (
                        zone.message_density * old_count + density
                    ) / new_count
                    zone.observation_count = new_count
                    zone.last_observed = datetime.now().strftime("%Y-%m-%d")

                    logger.debug(
                        f"📈 Updated hot zone {zone.id_start}-{zone.id_end}: "
                        f"{zone.observation_count} observations, avg {zone.avg_latency_sec:.1f}s"
                    )
            else:
                # Potential new hot zone if severe enough
                if duration > 10.0 or density > 150:
                    # Determine severity
                    if duration > 60 or density > 180:
                        severity = "CRITICAL"
                        chunk_size = 5_000
                    elif duration > 20 or density > 150:
                        severity = "HIGH"
                        chunk_size = 10_000
                    elif duration > 10 or density > 100:
                        severity = "MEDIUM"
                        chunk_size = 15_000
                    else:
                        severity = "LOW"
                        chunk_size = 25_000

                    new_zone = HotZone(
                        id_start=start_id,
                        id_end=end_id,
                        datacenter=datacenter,
                        optimal_chunk_size=chunk_size,
                        avg_latency_sec=duration,
                        message_density=density,
                        severity=severity,
                        last_observed=datetime.now().strftime("%Y-%m-%d"),
                        observation_count=1,
                    )

                    self.hot_zones.append(new_zone)
                    logger.info(
                        f"🔥 Created new hot zone: {start_id}-{end_id} "
                        f"(severity: {severity}, chunk size: {chunk_size})"
                    )

        except Exception as e:
            logger.warning(f"⚠️ Failed to analyze slow chunk for hot zone update: {e}")

    def get_recommendations(self) -> List[str]:
        """
        Generate actionable recommendations based on collected data.

        Returns:
            List of recommendation strings
        """
        recommendations = []

        # Analyze recent slow chunks
        recent_chunks = self.slow_chunks[-50:]  # Last 50
        if len(recent_chunks) > 10:
            # Group by datacenter
            dc_counts = {}
            for chunk in recent_chunks:
                dc = chunk.datacenter
                dc_counts[dc] = dc_counts.get(dc, 0) + 1

            # Recommend if one DC dominates
            for dc, count in dc_counts.items():
                if count > len(recent_chunks) * 0.5:
                    recommendations.append(
                        f"{dc} accounts for {count}/{len(recent_chunks)} recent slow chunks. "
                        "Consider reducing chunk sizes for this DC."
                    )

        # Check for high-density patterns
        high_density_chunks = [c for c in recent_chunks if c.density > 150]
        if len(high_density_chunks) > 5:
            recommendations.append(
                f"{len(high_density_chunks)} recent chunks had very high density (>150 msgs/1K IDs). "
                "Density-based chunking is active and should help."
            )

        # Check hot zones effectiveness
        if self.hot_zones:
            critical_zones = [z for z in self.hot_zones if z.severity == "CRITICAL"]
            if critical_zones:
                recommendations.append(
                    f"{len(critical_zones)} CRITICAL hot zones active. "
                    "Future exports in these ranges will use 5K chunk sizes."
                )

        return recommendations
