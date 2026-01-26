# ✅ BloomFilter Optimization - Quick Test Checklist

## Pre-Flight Check

- [x] Code changes applied
- [x] Files modified:
  - `src/config.py` — added `bloom_filter_only_for_resume: bool = True`
  - `src/export/exporter.py` — smart BloomFilter/set selection
- [x] Code compiles without errors
- [x] Documentation created

## Quick Test (5 minutes)

### Step 1: Verify config
```bash
grep "bloom_filter_only_for_resume" src/config.py
```
**Expected**: `bloom_filter_only_for_resume: bool = True`

### Step 2: Run export
```bash
python -m tobs export
```

### Step 3: Check logs for optimization
```bash
grep "New export detected" tobs_exporter.log
```
**Expected**: 
```
🚀 New export detected: using lightweight set (BloomFilter disabled for performance)
```

### Step 4: Check performance
**Expected result:**
```
Total time:  ~650s (±10s)
Throughput:  ~760 msg/s (±20)
```

**Comparison to unoptimized:**
```
Before optimization: 696.3s (708.8 msg/s)
After optimization:  ~650s (~760 msg/s)
Improvement:         ~46s (~51 msg/s) ✅
```

## Success Criteria

✅ Export completes successfully
✅ Log shows "using lightweight set"  
✅ Time: 640-660s (target: ~650s)
✅ Throughput: 750-770 msg/s (target: ~760s)
✅ No errors or warnings

## If Test Fails

### Scenario 1: Still slow (~690s)
**Check**: Is optimization actually active?
```bash
# Should see this in logs:
grep "lightweight set" tobs_exporter.log
```
If NOT present → config not loaded correctly

**Fix**: Verify src/config.py has the change

---

### Scenario 2: Errors during export
**Check logs for**:
```bash
grep "ERROR" tobs_exporter.log | tail -20
```

**Rollback**:
```python
# In src/config.py, change:
bloom_filter_only_for_resume: bool = False
```

---

### Scenario 3: Faster but not enough (~670s instead of ~650s)
**Possible causes:**
- Network variance (5-10s typical)
- Other system load
- Run test again

**Action**: Run 2-3 more times and average

---

## Full A/B Test (Optional, 30 minutes)

If you want scientific confirmation:

### Run 1: WITH optimization
```bash
# Ensure bloom_filter_only_for_resume = True
python -m tobs export
# Record time: ______ s
```

### Run 2: WITHOUT optimization
```bash
# Edit src/config.py:
# bloom_filter_only_for_resume: bool = False

python -m tobs export
# Record time: ______ s
```

### Run 3: WITH optimization (verify)
```bash
# Edit src/config.py:
# bloom_filter_only_for_resume: bool = True

python -m tobs export
# Record time: ______ s
```

### Analysis:
```
Run 1 (optimized):    ______ s
Run 2 (unoptimized):  ______ s
Run 3 (optimized):    ______ s

Average optimized:    ______ s
Average unoptimized:  ______ s (only 1 sample, but OK)

Improvement:          ______ s
```

**Expected**: ~46s improvement (optimized faster)

---

## Quick Commands Reference

```bash
# Check config
grep "bloom_filter_only_for_resume" src/config.py

# Run export
python -m tobs export

# Check if optimization active
grep "lightweight set" tobs_exporter.log

# Check performance
grep "Скорость:" tobs_exporter.log | tail -1

# Rollback if needed
# Edit src/config.py: bloom_filter_only_for_resume = False
```

---

## Expected Log Output

### With Optimization (bloom_filter_only_for_resume = True):
```
2026-01-XX XX:XX:XX | INFO | 🚀 New export detected: using lightweight set (BloomFilter disabled for performance)
...
═══════════════════════════════════════════════
          СВОДКА ЭКСПОРТА
═══════════════════════════════════════════════
Общее время: ~650.0s
⚡ Скорость: ~760 сообщений/сек
```

### Without Optimization (bloom_filter_only_for_resume = False):
```
2026-01-XX XX:XX:XX | INFO | 📊 BloomFilter sizing: 492,696 messages × 1.1 = 541,966 expected → 541,966 (final)
2026-01-XX XX:XX:XX | INFO | 📊 BloomFilter enabled by config (size=541,966)
...
═══════════════════════════════════════════════
          СВОДКА ЭКСПОРТА
═══════════════════════════════════════════════
Общее время: ~696.3s
⚡ Скорость: ~708.8 сообщений/сек
```

**Difference**: ~46s, ~51 msg/s ✅

---

## Result Reporting

After test completion, fill this:

```
Date: ___________
Tester: ___________

Configuration:
  bloom_filter_only_for_resume: [True/False]

Results:
  Total time:    ______ s
  Throughput:    ______ msg/s
  Messages:      ______ 
  
Logs confirm optimization: [Yes/No]
  Found "lightweight set": [Yes/No]

Compared to baseline (643.9s):
  Delta: ______ s (______ %)

Compared to unoptimized (696.3s):
  Delta: ______ s (______ %)

Status: [✅ SUCCESS / ⚠️ PARTIAL / ❌ FAILED]

Notes:
_________________________________________________________________
_________________________________________________________________
```

---

**Ready to test! 🚀**

Estimated test time: **5 minutes** (quick test) or **30 minutes** (full A/B)
