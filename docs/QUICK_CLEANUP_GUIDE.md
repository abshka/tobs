# Быстрое руководство по очистке документации

## 🎯 Что будет сделано

**Удалить (4 файла):**
- `FEATURE_IMPLEMENTATION_PLAN.md` → заменен на `docs/IMPLEMENTATION_PLAN.md`
- `IMPLEMENTATION_INDEX.md` → заменен на `docs/README.md`
- `IMPLEMENTATION_SUMMARY.txt` → заменен на `docs/ANALYSIS_SUMMARY.md`
- `IMPROVEMENTS_SUMMARY.md` → заменен на `docs/IMPLEMENTED_OPTIMIZATIONS_REVIEW.md`

**Переместить в архив (5 файлов):**
- `OPTIMIZATION_REPORT_*.md` → `docs/archive/optimization_reports/`

**Оставить (4 файла):**
- `README.md`
- `DOCKER_QUICKSTART.md`
- `PERFORMANCE_GUIDE.md`
- `GEMINI.md`

---

## 🚀 Выполнение очистки

### Шаг 1: Проверка (dry-run)
```bash
./docs/CLEANUP_SCRIPT.sh --dry-run
```

### Шаг 2: Выполнение
```bash
./docs/CLEANUP_SCRIPT.sh
```

Готово! ✅

---

## 📊 Результат

**До:** 9 MD файлов в корне  
**После:** 4 актуальных MD файла в корне

Вся техническая документация теперь в `docs/` с четкой структурой.

---

**Детали:** См. [CLEANUP_RECOMMENDATIONS.md](CLEANUP_RECOMMENDATIONS.md)
