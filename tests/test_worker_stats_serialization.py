"""Test worker stats JSON serialization fix."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import orjson
from dataclasses import asdict
from src.export_reporter import ExportMetrics


def test_worker_stats_with_string_keys():
    """Verify worker_stats with string keys serializes correctly to JSON."""
    metrics = ExportMetrics()
    
    # Simulate worker stats as they would be converted in exporter.py
    worker_stats = {
        0: {"messages": 1000, "flood_waits": 2},
        1: {"messages": 1500, "flood_waits": 1},
        2: {"messages": 900, "flood_waits": 0},
    }
    
    # Convert int keys to string keys (as done in exporter.py)
    metrics.worker_stats = {str(k): v for k, v in worker_stats.items()}
    
    # Try to serialize (this should not raise an error)
    metrics_dict = asdict(metrics)
    json_bytes = orjson.dumps(metrics_dict, option=orjson.OPT_INDENT_2)
    
    # Verify it's valid JSON
    parsed = orjson.loads(json_bytes)
    
    # Verify worker_stats structure
    assert "worker_stats" in parsed
    assert parsed["worker_stats"] is not None
    assert "0" in parsed["worker_stats"]
    assert "1" in parsed["worker_stats"]
    assert "2" in parsed["worker_stats"]
    
    # Verify data integrity
    assert parsed["worker_stats"]["0"]["messages"] == 1000
    assert parsed["worker_stats"]["1"]["messages"] == 1500
    assert parsed["worker_stats"]["2"]["messages"] == 900
    
    print("✅ Worker stats serialization test passed!")
    print(f"📊 Serialized {len(parsed['worker_stats'])} worker stats successfully")


def test_worker_stats_with_int_keys_fails():
    """Verify that integer keys would fail (for documentation purposes)."""
    metrics = ExportMetrics()
    
    # This is the OLD way (with integer keys) - should fail
    metrics.worker_stats = {
        0: {"messages": 1000, "flood_waits": 2},
        1: {"messages": 1500, "flood_waits": 1},
    }
    
    try:
        metrics_dict = asdict(metrics)
        # This should raise an error about dict keys
        orjson.dumps(metrics_dict)
        print("❌ Test failed: Should have raised an error for integer keys")
        assert False, "Expected serialization to fail with integer keys"
    except TypeError as e:
        print(f"✅ Expected error caught: {e}")
        assert "Dict key must be str" in str(e) or "keys must be str" in str(e).lower()


if __name__ == "__main__":
    print("Testing worker stats JSON serialization...")
    print("\n1. Testing correct implementation (string keys):")
    test_worker_stats_with_string_keys()
    
    print("\n2. Testing broken implementation (int keys):")
    test_worker_stats_with_int_keys_fails()
    
    print("\n✅ All tests passed!")
