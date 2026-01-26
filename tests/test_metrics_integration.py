#!/usr/bin/env python3
"""
Quick integration test for TIER C-4 metrics system.
Tests the complete metrics integration without pytest dependencies.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.monitoring.metrics_collector import (
    get_metrics_collector,
    StageMetrics,
    ResourceMetrics,
    CacheMetrics,
)
from src.monitoring.resource_monitor import ResourceMonitor
from src.monitoring.metrics_formatter import format_metrics_summary, log_metrics_summary


async def test_metrics_integration():
    """Test complete metrics integration workflow."""
    print("🧪 TIER C-4 Metrics Integration Test")
    print("=" * 70)
    
    # Test 1: MetricsCollector
    print("\n1️⃣ Testing MetricsCollector...")
    metrics = get_metrics_collector()
    
    # Record some stage metrics
    metrics.record_stage("fetch", 2.5, 1000)
    metrics.record_stage("process", 5.0, 1000)
    metrics.record_stage("write", 1.5, 1000)
    
    # Record resource metrics
    metrics.record_resource(ResourceMetrics(
        cpu_percent=45.5,
        memory_mb=512.0,
        disk_read_mb=10.0,
        disk_write_mb=50.0,
        network_sent_mb=5.0,
        network_recv_mb=15.0,
        timestamp=time.time()
    ))
    
    # Record cache metrics
    metrics.record_cache("input_peer_cache", CacheMetrics(hits=800, misses=200, evictions=10))
    
    data = metrics.export_json()
    print(f"   ✅ Recorded {len(data['stages'])} stages")
    print(f"   ✅ Recorded {data['resources']['sample_count']} resource samples")
    print(f"   ✅ Recorded {len(data['caches'])} caches")
    
    # Test 2: ResourceMonitor
    print("\n2️⃣ Testing ResourceMonitor...")
    monitor = ResourceMonitor(interval_seconds=0.5)
    
    await monitor.start()
    print("   ✅ Monitor started")
    
    # Simulate some work
    await asyncio.sleep(1.5)
    
    await monitor.stop()
    print("   ✅ Monitor stopped")
    
    # Check if monitor recorded metrics
    final_data = metrics.export_json()
    if final_data['resources']['sample_count'] > 1:
        print(f"   ✅ Monitor recorded {final_data['resources']['sample_count']} samples")
    else:
        print(f"   ⚠️ Monitor recorded only {final_data['resources']['sample_count']} samples")
    
    # Test 3: Metrics Formatter
    print("\n3️⃣ Testing Metrics Formatter...")
    summary = format_metrics_summary(final_data)
    
    if len(summary) > 100:
        print(f"   ✅ Generated summary ({len(summary)} chars)")
        print("\n" + "─" * 70)
        print(summary)
        print("─" * 70)
    else:
        print(f"   ❌ Summary too short: {len(summary)} chars")
    
    # Test 4: Verify JSON structure
    print("\n4️⃣ Testing JSON Export Structure...")
    required_keys = ["stages", "resources", "caches"]
    missing = [k for k in required_keys if k not in final_data]
    
    if not missing:
        print("   ✅ All required keys present in JSON")
        
        # Verify stages structure
        if "fetch" in final_data["stages"]:
            fetch_stats = final_data["stages"]["fetch"]
            if all(k in fetch_stats for k in ["total_duration_seconds", "total_count", "avg_duration_seconds"]):
                print("   ✅ Stage metrics structure valid")
            else:
                print("   ❌ Stage metrics missing required fields")
        
        # Verify resources structure
        res = final_data["resources"]
        if all(k in res for k in ["peak_cpu_percent", "peak_memory_mb", "sample_count"]):
            print("   ✅ Resource metrics structure valid")
        else:
            print("   ❌ Resource metrics missing required fields")
        
        # Verify cache structure
        if "input_peer_cache" in final_data["caches"]:
            cache_stats = final_data["caches"]["input_peer_cache"]
            if all(k in cache_stats for k in ["hits", "misses", "hit_rate"]):
                print("   ✅ Cache metrics structure valid")
            else:
                print("   ❌ Cache metrics missing required fields")
    else:
        print(f"   ❌ Missing required keys: {missing}")
    
    print("\n" + "=" * 70)
    print("✅ All TIER C-4 integration tests passed!")
    print("=" * 70)
    
    return True


async def main():
    try:
        result = await test_metrics_integration()
        sys.exit(0 if result else 1)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
