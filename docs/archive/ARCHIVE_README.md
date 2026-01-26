# Archive Documentation

This directory contains historical development documents that have been archived for reference.

## 📁 Directory Structure

### `/tier_reports/`
Contains all TIER A, B, and C implementation reports and status updates from the initial development phase.

**Contents:**
- TIER_A_* - Quick wins and initial improvements
- TIER_B_* - Core optimizations (B1-B6)
- TIER_C_* - Advanced features and metrics integration

**Note:** All TIER tasks have been completed and integrated into the codebase.

---

### `/performance_analysis/`
Historical performance analysis reports and regression investigations.

**Contents:**
- Multiple performance correction documents
- Final analysis reports (with Run1, Run2 data)
- Optimization postmortems
- Code review findings
- Executive summaries

**Current Status:** Superseded by `/PERFORMANCE_IMPROVEMENTS.md` in project root.

---

### `/hotfixes/`
Applied hotfixes and optimization patches.

**Contents:**
- HOTFIX_APPLIED.md, HOTFIX_V2_APPLIED.md
- TAKEOUT_FIX.md, TAKEOUT_FIX_RU.md
- LAZY_SENDER_OPTIMIZATION.md
- BLOOMFILTER_OPTIMIZATION.md

**Note:** All hotfixes have been merged into main codebase.

---

### `/checklists/`
Test checklists used during feature validation.

**Contents:**
- BLOOMFILTER_TEST_CHECKLIST.md
- TAKEOUT_TEST_CHECKLIST.md

**Note:** Checklists completed; features are production-ready.

---

### `/debug/`
Debugging reports and diagnostic documents.

**Contents:**
- DEBUG_RESOURCEMONITOR.md
- RESOURCEMONITOR_RESTORED.md
- THROTTLING_DIAGNOSIS.md
- TAKEOUT_DELAY_ANALYSIS.md

**Note:** Issues resolved; debugging insights preserved for reference.

---

### `/plans/`
Design documents and implementation plans.

**Contents:**
- GRACEFUL_SHUTDOWN_DESIGN.md
- IMPLEMENTATION_ACTION_PLAN.md

**Note:** Plans executed; architecture documented in main docs.

---

### `/quickstarts/`
Legacy quick start guides superseded by current documentation.

**Contents:**
- QUICK_START_GUIDE.md (old version)
- TAKEOUT_QUICKSTART.md

**Current Documentation:**
- `/README.md` - Main project documentation
- `/DOCKER_QUICKSTART.md` - Docker/Podman quick start

---

## 📊 Archive Timeline

1. **Initial Development (TIER A-C)** - Feature implementation and optimization
2. **Performance Investigation** - Multiple analysis rounds identifying I/O bottleneck
3. **Hotfix Phase** - Targeted fixes for lazy sender, BloomFilter, takeout
4. **Stabilization** - Graceful shutdown, throttle detection, adaptive backoff
5. **Current State** - Production-ready with comprehensive monitoring

---

## 🔍 Finding Historical Context

To find context about specific features or decisions:

1. **Performance history** → `/performance_analysis/`
2. **Feature implementation details** → `/tier_reports/`
3. **Bug fixes and patches** → `/hotfixes/`
4. **Design rationale** → `/plans/`
5. **Testing methodology** → `/checklists/`

---

## ⚠️ Important Note

**These documents are for historical reference only.**

For current project documentation, see:
- Project root: `README.md`, `DOCKER_QUICKSTART.md`, `PERFORMANCE_IMPROVEMENTS.md`
- `/docs/` directory: Current architecture, optimization guides, and changelogs
