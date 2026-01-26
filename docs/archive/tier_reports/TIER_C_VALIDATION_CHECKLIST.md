# TIER C — Финальный чеклист валидации

## Быстрая проверка (5 минут)

### 1. Компиляция модулей ✅
```bash
cd /home/ab/Projects/Python/tobs

python3 -m py_compile \
  src/monitoring/metrics_collector.py \
  src/monitoring/resource_monitor.py \
  src/monitoring/metrics_formatter.py \
  src/export/exporter.py \
  src/export/pipeline.py
```
**Статус**: ✅ Проверено — все компилируется

### 2. Проверка интеграции
```bash
# Проверить, что импорты работают
python3 -c "
try:
    from src.monitoring import get_metrics_collector, ResourceMonitor
    from src.monitoring.metrics_formatter import log_metrics_summary
    print('✅ Все импорты успешны')
except Exception as e:
    print(f'❌ Ошибка импорта: {e}')
"
```

### 3. Структура файлов
```bash
# Проверить наличие всех новых файлов
ls -lh src/monitoring/metrics_collector.py
ls -lh src/monitoring/resource_monitor.py
ls -lh src/monitoring/metrics_formatter.py
ls -lh src/monitoring/__init__.py
```

---

## Unit Tests (если доступен pytest)

```bash
# TIER C-1: VA-API Detection
pytest tests/test_vaapi_detector.py -v

# TIER C-2: Slotted Dataclasses
pytest tests/test_slotted_dataclasses.py -v

# TIER C-3: InputPeer Cache
pytest tests/test_input_peer_cache.py -v

# TIER C-4: Metrics (новые тесты)
pytest tests/test_metrics_collector.py -v
pytest tests/test_resource_monitor.py -v
```

---

## Integration Test (Standalone)

```bash
# Запустить standalone тест без pytest
python3 tests/test_metrics_direct.py
```

**Ожидаемый вывод**:
- ✅ metrics_collector loaded
- ✅ resource_monitor loaded
- ✅ metrics_formatter loaded
- ✅ Recorded stage/resource/cache metrics
- ✅ Formatted metrics summary
- ✅ ALL TESTS PASSED

---

## Smoke Test на реальных данных

### Минимальный тест (без полного export)
```bash
# Создать тестовое окружение
export EXPORT_PATH=/tmp/tobs_tier_c_test
mkdir -p $EXPORT_PATH

# Запустить с минимальной конфигурацией (если main.py поддерживает dry-run)
# или проверить, что metrics файл создается
```

### Полный экспорт (если есть тестовый аккаунт)
```bash
# Запустить реальный export
python3 main.py --export-path /tmp/tobs_export_test

# После завершения проверить метрики
cat /tmp/tobs_export_test/export_metrics.json

# Должен содержать:
# - "stages": { "pipeline_fetch", "pipeline_process", "pipeline_write" }
# - "resources": { "peak_cpu_percent", "peak_memory_mb", ... }
# - "caches": { ... }
```

---

## Performance Comparison

### VA-API Test (если доступно GPU)
```bash
# Проверить детектирование VA-API
python3 -c "
from src.media.vaapi_detector import VAAPIDetector
detector = VAAPIDetector()
result = detector.detect_vaapi()
print(f'VA-API доступен: {result.available}')
if result.available:
    print(f'Устройство: {result.device_path}')
    print(f'Кодеки: {result.codecs}')
"

# Сравнить производительность с/без VA-API
# (требует видео в export)
FORCE_CPU_TRANSCODE=false python3 main.py --export videos_only
FORCE_CPU_TRANSCODE=true python3 main.py --export videos_only

# Сравнить time и CPU usage
```

---

## Rollback Test

### C-4 Metrics Disable
```bash
# Метрики должны работать с zero overhead, если не вызываются
# Проверить, что export работает без metrics integration:

# Временно закомментировать в src/export/exporter.py:
# - resource_monitor.start()
# - metrics.record_stage()

# Запустить export — должен работать без изменений
```

---

## Checklist Summary

- [ ] ✅ Все модули компилируются (py_compile)
- [ ] ⚠️ Unit tests (требует pytest + исправление Telethon import issues)
- [ ] ⏳ Integration test standalone (можно запустить вручную)
- [ ] ⏳ Real export smoke test (требует тестовый аккаунт)
- [ ] ⏳ VA-API detection test (требует GPU hardware)
- [ ] ✅ Documentation complete (TIER_C_COMPLETE.md)

---

## Known Issues

### pytest ImportError
```
ImportError: cannot import name 'GetFileHashes' from 'telethon.tl.functions.upload'
```

**Причина**: Конфликт версий Telethon или отсутствие некоторых imports  
**Обходной путь**: Использовать standalone тесты (`test_metrics_direct.py`)  
**Решение**: Обновить/исправить Telethon imports в `src/media/hash_dedup.py`

---

## Next Actions

### Immediate (Рекомендуется)
1. Запустить standalone integration test
2. Проверить real export с metrics на dev machine
3. Сравнить performance с/без VA-API

### Short-term
1. Исправить pytest import issues
2. Запустить полный test suite
3. Performance benchmarking

### Long-term
1. Continuous monitoring integration
2. Dashboard для metrics visualization
3. Automated performance regression detection

---

*Статус: TIER C полностью реализован и готов к валидации*  
*Дата: 2025-01-05*
