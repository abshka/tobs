# 📋 TOBS: Детальный план действий (Action Plan)

**Дата создания:** 2025-01-27  
**Статус:** Ready for Execution  
**Основано на:** Интеграция 3 независимых анализов (ChatGPT, Gemini, Claude)

---

## 🎯 Общая стратегия

**Главный принцип:** Security First → Performance → Polish

**Timeline:** 10 недель (400 часов работы)  
**Команда:** 1-2 разработчика  
**Методология:** Incremental delivery с верификацией на каждом этапе

---

## 📊 Текущий статус проекта

### Производительность
- **Baseline:** ~200 msg/s, CPU 40%
- **Target:** 420+ msg/s (2-2.5x improvement)
- **Уже реализовано:** 7 оптимизаций (Batch Fetching, Media Dedup, Metadata Cache, Part Size Autotuning, Shard Compression, BloomFilter, Lightweight Schema)

### Безопасность
- **Текущая оценка:** 4/10 (НЕ production-ready)
- **Target после TIER S:** 8/10 (production-ready)
- **Критические проблемы:** 5 (chmod 666, eval RCE, pickle RCE, atomic writes, socket hanging)

---

## 🚨 TIER S: Критические исправления (Неделя 1)

**Цель:** Устранить RCE векторы и критические security/stability issues  
**Время:** 5-7 дней  
**Приоритет:** HIGHEST

### S-1: Fix chmod 666 sessions (DAY 1)

**Источник:** Gemini report (CRITICAL finding)

**Проблема:**
- `run-tobs.sh`: команда `chmod 666 sessions/*.session`
- Даёт read/write доступ ВСЕМ пользователям системы
- Риск: кража Telegram API ключей

**Решение:**
```bash
# Файл: run-tobs.sh
# БЫЛО:
chmod 666 sessions/*.session

# СТАЛО:
chmod 600 sessions/*.session  # Только владелец
chown $UID:$GID sessions/*.session  # Правильный владелец в Docker
```

**Верификация:**
```bash
ls -la sessions/
# Должно быть: -rw------- (600)
```

**Acceptance Criteria:**
- [ ] Права доступа 600 на все .session файлы
- [ ] Docker контейнер использует правильный UID/GID
- [ ] Документация обновлена (SECURITY.md)

---

### S-2: Replace eval() на r_frame_rate (DAY 1-2)

**Источник:** Все три отчёта (RCE risk)

**Проблема:**
- `src/media/processors/video.py`: `eval()` на вывод ffprobe
- Вредоносная строка → arbitrary code execution

**Решение:**
```python
# БЫЛО:
fps = eval(r_frame_rate)

# СТАЛО:
from fractions import Fraction

def parse_frame_rate(rate_str: str) -> float:
    """Безопасный парсинг r_frame_rate из ffprobe."""
    try:
        if '/' in rate_str:
            return float(Fraction(rate_str))
        return float(rate_str)
    except (ValueError, ZeroDivisionError):
        logger.warning(f"Invalid frame rate: {rate_str}, defaulting to 30")
        return 30.0

fps = parse_frame_rate(r_frame_rate)
```

**Тесты:**
```python
# tests/test_video_processor.py
def test_parse_frame_rate_fraction():
    assert parse_frame_rate("30/1") == 30.0
    assert parse_frame_rate("24000/1001") ≈ 23.976

def test_parse_frame_rate_malicious():
    # Не должен выполнять код
    result = parse_frame_rate("__import__('os').system('rm -rf /')")
    assert result == 30.0  # fallback
```

**Acceptance Criteria:**
- [ ] Нет eval() в video.py
- [ ] Тесты покрывают fraction, float, invalid, malicious cases
- [ ] py_compile успешен
- [ ] Интеграционный тест с реальным ffprobe выводом

---

### S-3: Replace pickle → msgpack (DAY 2-3)

**Источник:** Все три отчёта (RCE risk)

**Проблема:**
- `src/telegram_sharded_client.py`: `pickle.loads()` для кэша/шардов
- Подмена .bin файла → RCE при десериализации

**Решение:**
```python
# Установить зависимость
# pyproject.toml:
dependencies = [
    "msgpack>=1.0.0",
]

# src/telegram_sharded_client.py
import msgpack

# БЫЛО:
with open(shard_file, 'rb') as f:
    data = pickle.load(f)

# СТАЛО:
with open(shard_file, 'rb') as f:
    data = msgpack.unpackb(f.read(), raw=False)

# Для записи:
# БЫЛО:
with open(shard_file, 'wb') as f:
    pickle.dump(data, f)

# СТАЛО:
with open(shard_file, 'wb') as f:
    f.write(msgpack.packb(data, use_bin_type=True))
```

**Migration script:**
```python
# scripts/migrate_pickle_to_msgpack.py
"""Конвертирует существующие pickle кэши в msgpack."""
import pickle
import msgpack
from pathlib import Path

cache_dir = Path('/tmp/tobs_cache')
for pkl_file in cache_dir.glob('**/*.bin'):
    try:
        with open(pkl_file, 'rb') as f:
            data = pickle.load(f)
        msgpack_file = pkl_file.with_suffix('.msgpack')
        with open(msgpack_file, 'wb') as f:
            f.write(msgpack.packb(data, use_bin_type=True))
        pkl_file.unlink()  # Удалить старый
        print(f"✓ Migrated {pkl_file}")
    except Exception as e:
        print(f"✗ Failed {pkl_file}: {e}")
```

**Acceptance Criteria:**
- [ ] Нет pickle imports в src/
- [ ] msgpack используется для всех persisted данных
- [ ] Migration script работает без ошибок
- [ ] Backward compatibility: код читает как msgpack, так и pickle (с deprecation warning)
- [ ] Документация обновлена

---

### S-4: Implement Atomic Writes (DAY 3-4)

**Источник:** Gemini report (Data Corruption risk)

**Проблема:**
- `src/note_generator.py`, `src/telegram_sharded_client.py`: прямая запись в файл
- Crash посередине → битый файл на диске

**Решение:**
```python
# src/utils/atomic_write.py
import os
import tempfile
from pathlib import Path

def atomic_write(path: Path, content: bytes | str, encoding: str = 'utf-8'):
    """Атомарная запись: tmp + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Запись во временный файл
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f'.tmp_{path.name}_'
    )
    try:
        if isinstance(content, str):
            os.write(fd, content.encode(encoding))
        else:
            os.write(fd, content)
        os.fsync(fd)  # Force flush to disk
        os.close(fd)
        
        # Атомарное переименование
        os.rename(tmp_path, path)
    except Exception:
        os.close(fd)
        os.unlink(tmp_path)
        raise
```

**Применить в:**
- `src/note_generator.py`: `AsyncBufferedSaver.flush()`
- `src/telegram_sharded_client.py`: запись шардов
- `src/export/exporter.py`: запись markdown файлов

**Тесты:**
```python
# tests/test_atomic_write.py
def test_atomic_write_success():
    path = Path('/tmp/test.txt')
    atomic_write(path, 'test content')
    assert path.read_text() == 'test content'

def test_atomic_write_crash_recovery():
    # Симулируем crash: exception в середине записи
    # Проверяем что файл либо старый, либо новый, но НЕ битый
```

**Acceptance Criteria:**
- [ ] atomic_write() реализован и протестирован
- [ ] Применён во всех hot paths записи
- [ ] Интеграционный тест: kill -9 во время записи → файл валидный
- [ ] Документация обновлена

---

### S-5: Fix Socket Hanging (DAY 4-5)

**Источник:** Gemini report (DoS/freeze risk)

**Проблема:**
- `src/core/connection.py`: `request_timeout=1800s` без sock_read timeout
- Half-open connection → зависание на 30 минут

**Решение:**
```python
# src/core/connection.py
import aiohttp

# БЫЛО:
timeout = aiohttp.ClientTimeout(total=1800.0)

# СТАЛО:
timeout = aiohttp.ClientTimeout(
    total=1800.0,      # Максимальное время всего запроса
    sock_read=60.0,    # Максимальное время ожидания данных от сокета
    sock_connect=10.0  # Максимальное время установки соединения
)

session = aiohttp.ClientSession(timeout=timeout)
```

**Конфигурация:**
```python
# src/config.py
class Config:
    # HTTP timeouts
    http_timeout_total: float = 1800.0  # 30 минут для больших файлов
    http_timeout_sock_read: float = 60.0  # 1 минута на чтение
    http_timeout_sock_connect: float = 10.0  # 10 сек на connect
```

**Acceptance Criteria:**
- [ ] Раздельные таймауты применены
- [ ] ENV переменные для конфигурации
- [ ] Интеграционный тест: симуляция hung socket → timeout через 60s
- [ ] Документация обновлена

---

### TIER S: Верификация и документация (DAY 5)

**Checklist:**
- [ ] Все 5 fixes применены
- [ ] Unit tests написаны и проходят
- [ ] Integration tests проходят
- [ ] py_compile successful на всех изменённых файлах
- [ ] Security scan (bandit/semgrep) чистый
- [ ] CHANGELOG.md обновлён
- [ ] SECURITY.md создан с описанием fixes
- [ ] Git commit с тегом `v1.0.0-security-fixes`

**Результат:**
- Security rating: 4/10 → 8/10
- Статус: Production-ready (security perspective)

---

## 🔥 TIER A: Performance Quick Wins (Неделя 2-3)

**Цель:** Достичь 300+ msg/s (55-80% улучшение)  
**Время:** 10-14 дней

### A-1: Logging Rate-Limiting (DAY 6-7)

**Источник:** Claude analysis (ROI 23.3, highest)

**Проблема:**
- Excessive logging в hot paths → 5-10% CPU overhead
- Нет batching, rate limiting

**Решение:**
```python
# src/logging/log_batcher.py (уже реализован!)
# Применить в:
# - src/export/exporter.py (periodic save logs)
# - src/media/downloader.py (download progress)
# - src/telegram_client.py (API calls)

# Конфигурация:
LOG_BATCH_INTERVAL=5.0  # Flush каждые 5 секунд
LOG_BATCH_SIZE=100      # Или при 100 сообщениях
```

**Метрики:**
```python
# Добавить instrumentation
class LogBatcher:
    def get_stats(self):
        return {
            'messages_batched': self.total_batched,
            'flushes': self.flush_count,
            'avg_batch_size': self.total_batched / self.flush_count,
            'cpu_time_saved': ...  # Estimate
        }
```

**Acceptance Criteria:**
- [ ] LogBatcher применён в exporter, downloader, telegram_client
- [ ] CPU usage снижен на >=5% (benchmark)
- [ ] Logs всё ещё информативны (не потеряны важные сообщения)
- [ ] ENV переменные для конфигурации

---

### A-2: Enable & Optimize AsyncPipeline (DAY 8-12)

**Источник:** Claude analysis (ROI 19.0, P0 bottleneck)

**Проблема:**
- AsyncPipeline существует но отключен (async_pipeline_enabled=False)
- Текущий flow последовательный: fetch → process → write

**Решение:**
```python
# .env
ASYNC_PIPELINE_ENABLED=true
ASYNC_PIPELINE_FETCH_WORKERS=4
ASYNC_PIPELINE_PROCESS_WORKERS=8
ASYNC_PIPELINE_FETCH_QUEUE_SIZE=500
ASYNC_PIPELINE_WRITE_QUEUE_SIZE=200
```

**Оптимизация:**
1. **Tune queue sizes** (benchmark разные значения)
2. **Backpressure handling** (pause fetch if queues full)
3. **Instrumentation** (add per-stage metrics - уже добавлено!)

**Benchmark скрипт:**
```python
# tests/benchmarks/bench_pipeline_tuning.py
"""Тюнинг AsyncPipeline параметров."""

configs = [
    {'fetch_workers': 2, 'process_workers': 4, 'queue_size': 200},
    {'fetch_workers': 4, 'process_workers': 8, 'queue_size': 500},
    {'fetch_workers': 8, 'process_workers': 16, 'queue_size': 1000},
]

for config in configs:
    result = await run_export_with_config(config)
    print(f"{config} → {result.throughput} msg/s")
```

**Acceptance Criteria:**
- [ ] AsyncPipeline включён по умолчанию
- [ ] Benchmark показывает >=30% улучшение throughput
- [ ] Нет memory leaks (long-running test)
- [ ] Graceful degradation при ошибках
- [ ] Документация обновлена

---

### A-3: Graceful Shutdown (DAY 13)

**Источник:** ChatGPT report (reliability)

**Решение:**
```python
# main.py
import signal

shutdown_requested = False
force_shutdown = False

def handle_sigint_first(signum, frame):
    global shutdown_requested
    if not shutdown_requested:
        shutdown_requested = True
        logger.info("⏸️  Graceful shutdown initiated (Ctrl+C again to force)")
        # Set flag для остановки продюсеров
        # Дождаться flush очередей
    else:
        force_shutdown = True
        logger.warning("⚠️  Force shutdown!")
        sys.exit(1)

signal.signal(signal.SIGINT, handle_sigint_first)
```

**Acceptance Criteria:**
- [ ] Ctrl+C №1: graceful (flush buffers, save state)
- [ ] Ctrl+C №2: force exit
- [ ] Resume после graceful shutdown работает
- [ ] Тест: interrupt mid-export → state saved

---

### A-4: DC-Aware Worker Assignment (DAY 14-17)

**Источник:** Claude analysis (ROI 14.2, P0)

**Проблема:**
- Workers назначаются round-robin без учёта datacenter
- DC migration → 100-500ms overhead per request

**Решение:**
```python
# src/telegram_sharded_client.py
class DCPool:
    """Пул воркеров с группировкой по DC."""
    
    def __init__(self):
        self.dc_pools = {}  # dc_id -> [worker1, worker2, ...]
        
    async def get_worker_for_entity(self, entity):
        """Получить воркера из правильного DC."""
        dc_id = await self.detect_dc(entity)
        if dc_id not in self.dc_pools:
            # Pre-warm workers for this DC
            self.dc_pools[dc_id] = await self.create_dc_pool(dc_id)
        return self.dc_pools[dc_id].get_available()
```

**Acceptance Criteria:**
- [ ] DC detection реализован
- [ ] Workers группируются по DC
- [ ] Pre-warming для известных DC
- [ ] Benchmark показывает 10-20% latency reduction

---

### TIER A: Результаты

**Expected Performance:**
- Throughput: 200 → 300+ msg/s (55-80% improvement)
- CPU: 40% → 60%
- Latency: снижена на 10-20%

---

## 📈 TIER B: Strategic Improvements (Неделя 4-6)

**Время:** 15-21 день  
**Target:** 400+ msg/s (90-125% improvement)

### B-1: Thread Pool Унификация (DAY 18-19)
### B-2: Zero-Copy Media (DAY 20-22)
### B-3: Parallel Media Processing (DAY 23-27)
### B-4: Пагинация Fix (DAY 28-29)
### B-5: TTY-Aware Modes (DAY 30-31)
### B-6: Hash-Based Deduplication (DAY 32-35)

*(Детальные шаги для TIER B см. в TIER_B_DETAILED.md)*

---

## ✨ TIER C: Polish (Неделя 7-10)

**Время:** 22-28 дней  
**Target:** 420+ msg/s (105-155% improvement)

### C-1: VA-API Auto-Detection
### C-2: Slotted Dataclasses
### C-3: InputPeer Caching
### C-4: Enhanced Metrics

*(Детальные шаги для TIER C см. в TIER_C_DETAILED.md)*

---

## 📋 Testing & Verification Strategy

### Unit Tests
```bash
pytest tests/ -v --cov=src --cov-report=html
# Target: >80% coverage
```

### Integration Tests
```bash
# Smoke test
pytest tests/integration/test_smoke.py

# Full export test
pytest tests/integration/test_full_export.py --slow
```

### Benchmarks
```bash
# Baseline
python tests/benchmarks/bench_baseline.py > baseline.json

# After каждой оптимизации
python tests/benchmarks/bench_current.py > current.json
python tests/benchmarks/compare.py baseline.json current.json
```

### Security Scan
```bash
bandit -r src/
semgrep --config=auto src/
```

---

## 📊 Progress Tracking

### Checklist Template
```markdown
## Week N: TIER X

- [ ] Task 1: Description
  - [ ] Implementation
  - [ ] Unit tests
  - [ ] Integration tests
  - [ ] Benchmark
  - [ ] Documentation
  - [ ] Code review
  - [ ] Merged to main

Progress: X/Y tasks completed
ETA: YYYY-MM-DD
```

### Metrics Dashboard
```python
# Обновлять каждую неделю
{
    "week": 1,
    "tier": "S",
    "throughput_msg_s": 200,  # Updated after benchmarks
    "cpu_usage_pct": 40,
    "security_rating": 8.0,   # После TIER S
    "tests_coverage_pct": 75,
    "completed_tasks": ["S-1", "S-2", "S-3"],
    "in_progress": ["S-4"],
    "blockers": []
}
```

---

## 🚀 Next Steps

### Immediate Actions (Today)
1. Review этот план с командой
2. Setup development environment
3. Создать feature branch: `feature/tier-s-security-fixes`
4. Начать S-1: Fix chmod 666

### Week 1 Goals
- [ ] Complete все TIER S fixes
- [ ] Security rating 4/10 → 8/10
- [ ] Production-ready security status
- [ ] Tag release: v1.0.0-security-fixes

### Success Criteria (10 weeks)
- [ ] Security: 8/10+
- [ ] Performance: 420+ msg/s (2x baseline)
- [ ] Test coverage: >80%
- [ ] Documentation: complete
- [ ] Zero P0/P1 bugs

---

## 📚 Дополнительная документация

- `TIER_B_DETAILED.md` - Детальные шаги TIER B
- `TIER_C_DETAILED.md` - Детальные шаги TIER C
- `TESTING_GUIDE.md` - Стратегия тестирования
- `BENCHMARK_GUIDE.md` - Методология benchmarking
- `ROLLBACK_PLAN.md` - Что делать если что-то сломалось

---

**Статус:** ✅ Ready for Execution  
**Последнее обновление:** 2025-01-27  
**Автор:** Claude (integrated from 3 analysis reports)
