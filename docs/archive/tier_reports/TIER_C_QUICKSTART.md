# TIER C: Quick Start Guide

**Status:** 🟡 Ready to Execute  
**Prerequisites:** ✅ TIER A and TIER B Complete  
**Timeline:** 8 days (2 weeks with 1 developer)  
**Current Performance:** ~400 msg/s  
**Target Performance:** 420+ msg/s (+5% polish)

---

## TL;DR

TIER C adds **final polish** with 4 independent low-priority optimizations:

1. **VA-API Auto-Detection** - Hardware video acceleration (2 days)
2. **Slotted Dataclasses** - Memory optimization (2 days)
3. **InputPeer Caching** - API call reduction (1 day)
4. **Enhanced Metrics** - Observability system (3 days)

**All tasks are optional** and can be skipped if production urgency is high.

---

## What to Do?

### Option 1: Full Implementation (Recommended)
Execute all 4 tasks for complete polish:

```bash
# Start any task (all independent)
git checkout -b feature/tier-c-vaapi
# or
git checkout -b feature/tier-c-slotted
# or
git checkout -b feature/tier-c-inputpeer
# or
git checkout -b feature/tier-c-metrics

# Follow detailed steps in TIER_C_PLAN.md
```

**Timeline:** 8 days total  
**Result:** 420+ msg/s, -20% memory, full observability

### Option 2: Cherry-Pick (Fast Track)
Pick only high-ROI tasks:

**Priority Order:**
1. C-3: InputPeer Caching (1 day, ROI 13.0) - **Quick win**
2. C-2: Slotted Dataclasses (2 days, ROI 12.0) - **Memory relief**
3. C-1: VA-API Auto-Detection (2 days, ROI 10.5) - **Video boost**
4. C-4: Enhanced Metrics (3 days, ROI 9.2) - **Observability**

**Timeline:** 3-5 days (for top 2-3 tasks)  
**Result:** Most impact with minimal time investment

### Option 3: Skip TIER C (Production First)
Go straight to production deployment:

```bash
# Current state is production-ready
git tag v2.0.0-production
# Deploy and monitor
```

**Rationale:** TIER C is polish, not critical  
**Defer to:** Post-launch optimization cycle

---

## Task Breakdown

### C-1: VA-API Auto-Detection (2 days)

**What it does:**  
Automatically detects hardware video acceleration (Intel/AMD GPUs) and uses it for faster video processing.

**Steps:**
1. Create `src/media/vaapi_detector.py` (4h)
2. Integrate into `VideoProcessor` (2h)
3. Add ENV config `FORCE_CPU_TRANSCODE` (1h)
4. Write unit tests (3h)

**Expected Impact:**
- Video processing: +3-5% faster
- CPU usage: -50-80% during video encode
- Automatic fallback if GPU unavailable

**Test:**
```bash
pytest tests/test_vaapi_detector.py
python -c "from src.media.vaapi_detector import get_vaapi_capabilities; print(get_vaapi_capabilities())"
```

**Rollback:**
```bash
FORCE_CPU_TRANSCODE=true  # Disable VA-API
```

---

### C-2: Slotted Dataclasses (2 days)

**What it does:**  
Converts dataclasses to use `__slots__` for memory efficiency (eliminates `__dict__` overhead).

**Steps:**
1. Identify slottable classes (2h)
2. Convert to `@dataclass(slots=True)` (4h)
3. Memory benchmark tests (2h)
4. Unit tests (2h)

**Expected Impact:**
- Memory: -15-25% for message-heavy exports
- Speed: +5-10% (slightly faster creation/access)

**Test:**
```bash
pytest tests/test_slotted_dataclasses.py
python tests/benchmarks/bench_slotted_memory.py
```

**Trade-off:**
- ⚠️ Requires Python 3.10+ for `slots=True` syntax
- ⚠️ Cannot add attributes dynamically (fine for our use case)

**Rollback:**
```python
# Remove slots=True from dataclass decorators
@dataclass  # instead of @dataclass(slots=True)
```

---

### C-3: InputPeer Caching (1 day)

**What it does:**  
Caches Telethon `InputPeer*` objects to avoid redundant entity resolution API calls.

**Steps:**
1. Create cache in `TelegramManager` (3h)
2. Integrate into `Exporter` (1h)
3. Add optional persistence (2h)
4. Unit tests (2h)

**Expected Impact:**
- API calls: -5-10% reduction
- Latency: -10-20ms per cached lookup
- Memory: +1-2MB (negligible)

**Test:**
```bash
pytest tests/test_input_peer_cache.py
# Check cache stats after export:
# Look for "InputPeer cache: X% hit rate" in logs
```

**Rollback:**
```python
# Remove caching, always call client.get_input_entity() directly
```

---

### C-4: Enhanced Metrics System (3 days)

**What it does:**  
Comprehensive metrics collection with JSON export for performance analysis and auto-tuning.

**Steps:**
1. Create `MetricsCollector` class (4h)
2. Integrate into `AsyncPipeline` (3h)
3. Add `ResourceMonitor` for CPU/memory (2h)
4. Export logic at end of export (1h)
5. Unit tests (2h)

**Expected Impact:**
- Observability: +100% (complete insights)
- Auto-tuning: +5-10% (data-driven decisions)
- Debugging: -50% time (clear bottlenecks)

**Test:**
```bash
pytest tests/test_metrics_collector.py
# After export, check:
ls monitoring/metrics_*.json
cat monitoring/metrics_<entity_id>.json | jq '.stages'
```

**Output Example:**
```json
{
  "stages": {
    "fetch": {"count": 1000, "avg_duration_s": 0.05, "throughput_per_s": 200},
    "process": {"count": 1000, "avg_duration_s": 0.03, "throughput_per_s": 333},
    "write": {"count": 1000, "avg_duration_s": 0.02, "throughput_per_s": 500}
  },
  "resources": {
    "avg_cpu_percent": 55.2,
    "avg_memory_mb": 512.3
  },
  "caches": {
    "sender": {"hits": 950, "misses": 50, "hit_rate_pct": 95.0}
  }
}
```

**Rollback:**
```python
# Remove metrics.record_*() calls
# Zero overhead
```

---

## Testing Strategy

### Quick Smoke Test (30 min)
```bash
# After each task implementation
pytest tests/test_<task_name>.py
python main.py  # Small chat export (~100 messages)
# Verify no regressions
```

### Full Test Suite (2h)
```bash
# Before merging to main
pytest tests/ -v --cov=src --cov-report=html

# Integration tests
pytest tests/integration/ --slow

# Benchmarks
python tests/benchmarks/bench_baseline.py > tier_c_baseline.json
python tests/benchmarks/bench_current.py > tier_c_result.json
python tests/benchmarks/compare.py tier_c_baseline.json tier_c_result.json
```

### Manual Verification (1h)
```bash
# Test each feature manually
export FORCE_CPU_TRANSCODE=false  # C-1: VA-API enabled
python main.py  # Export video-heavy chat

# Check slotted memory (C-2)
python -c "from src.export.exporter import ExportedMessage; print(hasattr(ExportedMessage(1, 'test', 'user', datetime.now(), [], None), '__dict__'))"  # Should be False

# Check InputPeer cache (C-3)
python main.py  # Export, check logs for "InputPeer cache: X% hit rate"

# Check metrics (C-4)
python main.py  # Export, check monitoring/metrics_*.json exists
```

---

## Success Criteria

### Overall TIER C ✅
- [ ] All 4 tasks implemented (or selected tasks if cherry-picking)
- [ ] All unit tests passing
- [ ] Performance: 400 → 420+ msg/s (+5%)
- [ ] Memory: -15-25% (if C-2 implemented)
- [ ] No functional regressions
- [ ] Documentation updated

### Per-Task (see TIER_C_PLAN.md for details)
- [ ] C-1: VA-API auto-detected, video +3-5% faster
- [ ] C-2: Memory -15-25% on 100k+ message export
- [ ] C-3: API calls -5-10%, cache hit rate >60%
- [ ] C-4: Metrics exported, overhead <2% CPU

---

## Common Issues & Solutions

### Issue: VA-API not detected (C-1)
**Symptom:** Logs show "VA-API unavailable"

**Solutions:**
```bash
# Check if VA-API drivers installed
vainfo

# Install drivers (Ubuntu/Debian)
sudo apt-get install vainfo intel-media-va-driver

# Override to force CPU
export FORCE_CPU_TRANSCODE=true
```

### Issue: Python 3.10+ required (C-2)
**Symptom:** SyntaxError on `@dataclass(slots=True)`

**Solutions:**
```bash
# Check Python version
python --version  # Must be 3.10+

# Upgrade Python
sudo apt-get install python3.12
# or use pyenv

# Alternative: skip C-2 if Python <3.10
```

### Issue: Metrics overhead too high (C-4)
**Symptom:** CPU usage increased >5% after C-4

**Solutions:**
```bash
# Increase sampling interval (default: 5s)
# In src/monitoring/resource_monitor.py:
# ResourceMonitor(interval_s=10.0)  # Less frequent sampling

# Or disable metrics collection
# Comment out metrics.record_*() calls
```

---

## Timeline & Checklist

### Week 1 (Days 1-5)
- [ ] Day 1-2: C-1 (VA-API Auto-Detection)
- [ ] Day 3-4: C-2 (Slotted Dataclasses)
- [ ] Day 5: C-3 (InputPeer Caching)

### Week 2 (Days 6-8)
- [ ] Day 6-8: C-4 (Enhanced Metrics System)

### Final (Days 9-10)
- [ ] Integration testing
- [ ] Documentation updates
- [ ] Performance benchmarks
- [ ] Git tag `v2.0.0-tier-c-complete`

---

## After TIER C

### Production Deployment Checklist
- [ ] Full test suite passing
- [ ] Performance benchmarks show 420+ msg/s
- [ ] Security audit complete (TIER S)
- [ ] Documentation up-to-date
- [ ] Docker image built and tested
- [ ] Monitoring/alerting configured

### Next Steps
1. **Deploy to Production**
   ```bash
   git tag v2.0.0
   docker build -t tobs:v2.0.0 .
   docker push tobs:v2.0.0
   ```

2. **Monitor Real-World Performance**
   - Collect metrics from production exports
   - Identify new bottlenecks
   - Plan TIER D optimizations (if needed)

3. **Community Feedback**
   - Gather user feedback
   - Address bug reports
   - Feature requests for next release

---

## Questions?

**Where to find detailed steps?**  
→ See `TIER_C_PLAN.md` (1149 lines, comprehensive guide)

**Can I skip TIER C?**  
→ Yes! Current state (post TIER B) is production-ready. TIER C is optional polish.

**Which task should I start with?**  
→ C-3 (InputPeer Caching) - quickest (1 day), highest ROI (13.0)

**What if I don't have VA-API hardware?**  
→ Skip C-1 or implement for auto-fallback (helps users with GPUs)

**Is Python 3.10+ required?**  
→ Only for C-2 (Slotted Dataclasses). Skip C-2 if Python <3.10.

---

**Status:** 🟢 Ready to Execute  
**Priority:** Low (Optional Polish)  
**Recommendation:** Cherry-pick high-ROI tasks (C-3, C-2) or skip entirely if production is urgent.

**Good luck! 🚀**
