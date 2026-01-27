#!/usr/bin/env python3
"""
Quick validation that slots are working correctly.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import ExportTarget, PerformanceSettings
from src.media.models import MediaMetadata
from src.export_reporter import ExportMetrics
from src.core.performance import SystemMetrics, ComponentStats


def test_slots_basic():
    """Test basic slots functionality."""
    print("🧪 Testing basic slots functionality...")
    
    # Test MediaMetadata
    metadata = MediaMetadata(file_size=1024, mime_type="image/jpeg")
    assert metadata.file_size == 1024
    assert not hasattr(metadata, "__dict__"), "Should not have __dict__"
    print("   ✅ MediaMetadata: slots working")
    
    # Test that we cannot add new attributes
    try:
        metadata.new_attr = "test"  # type: ignore
        print("   ❌ ERROR: Should not be able to add new attributes!")
        return False
    except AttributeError:
        print("   ✅ MediaMetadata: correctly prevents new attributes")
    
    # Test PerformanceSettings
    settings = PerformanceSettings()
    assert not hasattr(settings, "__dict__"), "Should not have __dict__"
    print("   ✅ PerformanceSettings: slots working")
    
    # Test ExportMetrics with default_factory
    metrics = ExportMetrics()
    assert isinstance(metrics.errors, list)
    assert not hasattr(metrics, "__dict__"), "Should not have __dict__"
    print("   ✅ ExportMetrics: slots working with default_factory")
    
    # Test dataclass with methods (ComponentStats)
    stats = ComponentStats(name="test")
    assert not hasattr(stats, "__dict__"), "Should not have __dict__"
    stats.record_call(duration=1.0, success=True)
    assert stats.calls_total == 1
    print("   ✅ ComponentStats: slots working with methods")
    
    # Test dataclass with @property
    assert stats.success_rate == 1.0
    print("   ✅ ComponentStats: @property works with slots")
    
    return True


def test_memory_comparison():
    """Quick memory comparison test."""
    print("\n💾 Testing memory savings...")
    
    # Create many instances
    instances = [MediaMetadata(file_size=i, mime_type="test") for i in range(1000)]
    
    # Verify no __dict__
    assert not hasattr(instances[0], "__dict__")
    print(f"   ✅ Created {len(instances)} instances, all without __dict__")
    
    return True


if __name__ == "__main__":
    print("=" * 70)
    print("SLOTS VALIDATION TEST (TIER C-2)")
    print("=" * 70)
    
    success = True
    
    try:
        if not test_slots_basic():
            success = False
        
        if not test_memory_comparison():
            success = False
        
        if success:
            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED")
            print("=" * 70)
            print("\nSlotted dataclasses are working correctly:")
            print("  • __dict__ is removed (memory optimization)")
            print("  • Cannot add new attributes dynamically")
            print("  • Methods and @property work correctly")
            print("  • field(default_factory) works correctly")
            sys.exit(0)
        else:
            print("\n❌ SOME TESTS FAILED")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
