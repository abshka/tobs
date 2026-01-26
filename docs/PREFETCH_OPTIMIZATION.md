# Prefetch Optimization - Producer-Consumer Pipeline

## 📋 Обзор

Prefetch optimization реализует producer-consumer паттерн для overlap'а сетевых запросов (fetch) с обработкой сообщений (process).

## ✅ Статус Интеграции

**Implemented:** 2026-01-07
**Coverage:** 
- ✅ Regular channels/chats (`_export_regular_target`)
- ✅ Forum topics (`_export_forum` → `_export_forum_topic_with_prefetch`)

## 🎯 Performance Gain

**Expected:** 1.5-2.5% improvement in I/O-bound scenarios
**Actual:** Depends on network latency and processing time ratio

## ⚙️ Configuration

```bash
# Enable prefetch (default: true)
ENABLE_PREFETCH_BATCHES=true

# Queue size (default: 2 for double-buffering)
PREFETCH_QUEUE_SIZE=2

# Batch size (default: 100)
PREFETCH_BATCH_SIZE=100
```

## 📊 Metrics in Logs

Look for:
```
⚡ Prefetch enabled: queue_size=2, batch_size=100
📊 Prefetch stats: utilization=85%, efficiency=82%
```

**Good metrics:**
- Utilization: 70-100% (prefetch keeping up)
- Efficiency: 70-100% (good overlap)

## 🧪 Testing

```bash
# A/B Test
ENABLE_PREFETCH_BATCHES=true python src/main.py export   # Test 1
ENABLE_PREFETCH_BATCHES=false python src/main.py export  # Test 2
```

Compare total duration and msgs/sec.

## 📁 Files

- `src/export/prefetch_processor.py` - Core implementation
- `src/export/exporter.py` - Integration (lines ~1347, ~2125)
- `src/config.py` - Configuration flags

---
For detailed documentation, see inline comments in code.
