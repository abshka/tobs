# 🔧 Performance Regression Fix - TIER S/A/B/C Hotpath Optimization

## Проблема
После применения TIER оптимизаций скорость упала с 765 msg/s до 536 msg/s (-30%).

## Корневые причины

### 1. Импорт в hot path (TIER A-3)
```python
# ❌ ПЛОХО: 493,176 импортов
async for message in fetch_messages(...):
    from src.shutdown_manager import shutdown_manager
    if shutdown_manager.shutdown_requested:
        break
```

### 2. BloomFilter проверка на каждое сообщение (TIER B-4)
```python
# ❌ ПЛОХО: 493,176 проверок
async for message in fetch_messages(...):
    if message.id in entity_data.processed_message_ids:
        continue
```

### 3. Неверное измерение API времени
```python
# ❌ ПЛОХО: api_start внутри loop
async for message in fetch_messages(...):
    api_start = time.time()  # ← Неправильная точка!
    batch.append(message)
    if len(batch) < batch_size:
        continue
    api_time = time.time() - api_start  # ← Включает overhead loop
```

## Решение

### Fix 1: Вынести импорт ВНЕ цикла
```python
# ✅ ХОРОШО: 1 импорт вместо 493,176
from src.shutdown_manager import shutdown_manager

async for message in fetch_messages(...):
    if shutdown_manager.shutdown_requested:
        break
```

### Fix 2: Оптимизировать BloomFilter проверку
```python
# ✅ ХОРОШО: Проверять только если resume активен
processed_ids = entity_data.processed_message_ids if resume_from_id > 0 else None

async for message in fetch_messages(...):
    if processed_ids and message.id in processed_ids:
        continue
```

### Fix 3: Исправить измерение API времени
```python
# ✅ ХОРОШО: Измерять между батчами
batch_fetch_start = time.time()

async for message in fetch_messages(...):
    batch.append(message)
    
    if len(batch) >= batch_size:
        # API время = с начала текущего батча до его заполнения
        api_time = time.time() - batch_fetch_start
        self.statistics.time_api_requests += api_time
        
        # ... process batch ...
        
        # Сброс таймера для следующего батча
        batch_fetch_start = time.time()
```

## Ожидаемый результат
- **Скорость:** 536 msg/s → **750+ msg/s** (возврат к baseline)
- **API время:** 904s → **~630s** (правильное измерение)
- **Обработка:** 14.1s → **~16s** (правильное распределение)

## Применение патча
```bash
# Будет создан патч в следующем сообщении
git apply performance_hotpath_fix.patch
```
