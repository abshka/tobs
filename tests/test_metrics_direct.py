#!/usr/bin/env python3
"""
Direct test for TIER C-4 metrics modules (bypassing src.__init__.py).
"""

import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Direct imports to bypass src/__init__.py
import importlib.util

def load_module_from_path(module_name, file_path):
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


print("🧪 TIER C-4 Direct Module Test")
print("=" * 70)

# Load monitoring modules directly
print("\n1️⃣ Loading monitoring modules...")
monitoring_path = project_root / "src" / "monitoring"

metrics_collector = load_module_from_path(
    "metrics_collector",
    monitoring_path / "metrics_collector.py"
)
print("   ✅ metrics_collector loaded")

resource_monitor = load_module_from_path(
    "resource_monitor",
    monitoring_path / "resource_monitor.py"
)
print("   ✅ resource_monitor loaded")

metrics_formatter = load_module_from_path(
    "metrics_formatter",
    monitoring_path / "metrics_formatter.py"
)
print("   ✅ metrics_formatter loaded")


async def test_functionality():
    """Test the loaded modules."""
    print("\n2️⃣ Testing MetricsCollector...")
    
    # Get singleton instance
    metrics = metrics_collector.get_metrics_collector()
    print("   ✅ Got metrics collector singleton")
    
    # Record stage metrics
    metrics.record_stage("test_fetch", 2.5, 1000)
    metrics.record_stage("test_process", 5.0, 1000)
    metrics.record_stage("test_write", 1.5, 1000)
    print("   ✅ Recorded 3 stage metrics")
    
    # Record resource metrics
    res_metric = metrics_collector.ResourceMetrics(
        cpu_percent=45.5,
        memory_mb=512.0,
        disk_read_mb=10.0,
        disk_write_mb=50.0,
        network_sent_mb=5.0,
        network_recv_mb=15.0,
        timestamp=time.time()
    )
    metrics.record_resource(res_metric)
    print("   ✅ Recorded resource metrics")
    
    # Record cache metrics
    cache_metric = metrics_collector.CacheMetrics(
        hits=800,
        misses=200,
        evictions=10
    )
    metrics.record_cache("test_cache", cache_metric)
    print("   ✅ Recorded cache metrics")
    
    # Export to JSON
    data = metrics.export_json()
    print(f"   ✅ Exported to JSON: {len(data)} top-level keys")
    
    # Verify structure
    assert "stages" in data
    assert "resources" in data
    assert "caches" in data
    assert len(data["stages"]) == 3
    assert data["resources"]["sample_count"] >= 1
    assert len(data["caches"]) == 1
    print("   ✅ JSON structure validated")
    
    print("\n3️⃣ Testing ResourceMonitor...")
    monitor = resource_monitor.ResourceMonitor(interval_seconds=0.5)
    print("   ✅ ResourceMonitor instantiated")
    
    await monitor.start()
    print("   ✅ Monitor started")
    
    # Let it sample a few times
    await asyncio.sleep(2.0)
    
    await monitor.stop()
    print("   ✅ Monitor stopped")
    
    # Check if samples were recorded
    final_data = metrics.export_json()
    sample_count = final_data["resources"]["sample_count"]
    print(f"   ✅ Collected {sample_count} resource samples")
    
    print("\n4️⃣ Testing Formatter...")
    # Create mock logger to capture output
    class MockLogger:
        def __init__(self):
            self.messages = []
        def info(self, msg):
            self.messages.append(msg)
    
    mock_logger = MockLogger()
    
    # Temporarily replace logger in formatter module
    original_logger = metrics_formatter.logger
    metrics_formatter.logger = mock_logger
    
    try:
        metrics_formatter.log_metrics_summary(final_data)
        print(f"   ✅ Formatted metrics ({len(mock_logger.messages)} lines)")
        
        # Verify key sections are present
        full_text = "\n".join(mock_logger.messages)
        assert "Pipeline Stage Performance" in full_text
        assert "Resource Utilization" in full_text
        assert "Cache Performance" in full_text
        print("   ✅ All expected sections present")
        
        # Print sample output
        print("\n" + "─" * 70)
        print("Sample formatted output:")
        print("─" * 70)
        for line in mock_logger.messages[:15]:  # First 15 lines
            print(line)
        print("   ... (output continues)")
        print("─" * 70)
        
    finally:
        metrics_formatter.logger = original_logger
    
    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED!")
    print("=" * 70)
    print("\nTIER C-4 Enhanced Metrics System is fully functional.")
    
    return True


async def main():
    try:
        result = await test_functionality()
        sys.exit(0 if result else 1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
