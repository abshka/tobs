# TIER C-4: Metrics Integration Plan

## Overview
Integration of MetricsCollector and ResourceMonitor into production code paths.

## Integration Points

### 1. Exporter Integration (run_export)
**File:** `src/export/exporter.py`  
**Location:** `run_export()` function  
**Changes:**
- Start ResourceMonitor at beginning of export
- Stop ResourceMonitor at end of export
- Add metrics summary log after export completion
- Export metrics JSON file alongside export data

**Estimated time:** 1h

### 2. AsyncPipeline Integration
**File:** `src/export/pipeline.py`  
**Location:** `AsyncPipeline.run()` method  
**Changes:**
- Record "fetch" stage metrics
- Record "process" stage metrics
- Record "write" stage metrics
- Track resource usage during pipeline execution

**Estimated time:** 1.5h

### 3. Metrics Summary Logger
**New utility:** Helper function to format metrics for human-readable logging  
**Changes:**
- Parse MetricsCollector JSON output
- Format stage timings in table format
- Show resource utilization summary
- Show cache hit rates

**Estimated time:** 30m

## Implementation Order

1. ✅ MetricsCollector implementation (DONE)
2. ✅ ResourceMonitor implementation (DONE)
3. ✅ Unit tests (DONE)
4. → **NOW:** Integration into exporter and pipeline
5. → **NEXT:** Run integration tests and validate metrics output

## Code Changes Preview

### A) Exporter Integration
```python
# In run_export() function:

from src.monitoring import get_metrics_collector, ResourceMonitor

async def run_export(...):
    metrics = get_metrics_collector()
    resource_monitor = ResourceMonitor(interval_seconds=5.0)
    
    try:
        # Start resource monitoring
        await resource_monitor.start()
        
        # ... existing export logic ...
        
        results = await exporter.export_all(targets, progress_queue)
        
        return results
        
    finally:
        # Stop monitoring
        await resource_monitor.stop()
        
        # Export metrics to JSON
        metrics_path = os.path.join(config.export_path, "export_metrics.json")
        with open(metrics_path, 'w') as f:
            json.dump(metrics.export_json(), f, indent=2)
        
        # Log human-readable summary
        _log_metrics_summary(metrics)
```

### B) Pipeline Integration
```python
# In AsyncPipeline.run():

from src.monitoring import get_metrics_collector

async def run(self, ...):
    metrics = get_metrics_collector()
    
    # Fetch stage
    fetch_start = time.time()
    # ... fetch logic ...
    metrics.record_stage("fetch", time.time() - fetch_start, processed_count)
    
    # Process stage
    process_start = time.time()
    # ... process logic ...
    metrics.record_stage("process", time.time() - process_start, processed_count)
    
    # Write stage
    write_start = time.time()
    # ... write logic ...
    metrics.record_stage("write", time.time() - write_start, processed_count)
```

### C) Metrics Summary Logger
```python
def _log_metrics_summary(metrics):
    """Log human-readable metrics summary."""
    data = metrics.export_json()
    
    logger.info("=" * 60)
    logger.info("📊 Export Metrics Summary")
    logger.info("=" * 60)
    
    # Stage timings
    if data.get("stages"):
        logger.info("\n🔄 Pipeline Stages:")
        for stage_name, stats in data["stages"].items():
            duration = stats["total_duration_seconds"]
            count = stats["total_count"]
            avg = stats["avg_duration_seconds"]
            logger.info(f"  • {stage_name:15s}: {duration:7.2f}s (avg: {avg:.3f}s/msg)")
    
    # Resource usage
    if data.get("resources"):
        res = data["resources"]
        logger.info("\n💻 Resource Usage:")
        logger.info(f"  • Peak CPU: {res.get('peak_cpu_percent', 0):.1f}%")
        logger.info(f"  • Peak Memory: {res.get('peak_memory_mb', 0):.1f} MB")
        logger.info(f"  • Samples: {res.get('sample_count', 0)}")
    
    # Cache performance
    if data.get("caches"):
        logger.info("\n🗄️ Cache Performance:")
        for cache_name, stats in data["caches"].items():
            hits = stats["hits"]
            total = hits + stats["misses"]
            hit_rate = (hits / total * 100) if total > 0 else 0
            logger.info(f"  • {cache_name}: {hit_rate:.1f}% hit rate ({hits}/{total})")
    
    logger.info("=" * 60)
```

## Testing Strategy

1. **Unit Tests:** Already completed
   - `tests/test_metrics_collector.py`
   - `tests/test_resource_monitor.py`

2. **Integration Tests:**
   - Run small export with metrics enabled
   - Verify metrics JSON is created
   - Verify all stages are recorded
   - Verify resource monitoring works

3. **Production Validation:**
   - Run full export on dev machine
   - Compare metrics before/after VA-API enabling
   - Validate no performance degradation from metrics overhead

## Rollback Plan

If metrics cause issues:
1. Remove `ResourceMonitor` start/stop calls (zero overhead when not used)
2. Remove `record_stage()` calls from pipeline
3. Keep collectors in codebase (dormant, zero overhead)

## Next Steps

After integration:
1. Run pytest on all TIER C tests
2. Run integration test with small dataset
3. Validate metrics output format
4. Document metrics JSON schema
5. Mark TIER C as COMPLETE

---
**Status:** Ready for implementation  
**Dependencies:** None (all prerequisites complete)  
**Risk:** Low (metrics have zero overhead when unused)
