#!/bin/bash
# Скрипт для очистки устаревших MD файлов из корня проекта
# Использование: ./docs/CLEANUP_SCRIPT.sh [--dry-run]

set -e

DRY_RUN=false
if [ "$1" == "--dry-run" ]; then
    DRY_RUN=true
    echo "🔍 DRY RUN MODE - файлы не будут удалены/перемещены"
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_DIR="$PROJECT_ROOT/docs/archive/optimization_reports"

echo "📋 Анализ файлов для очистки..."
echo ""

# Функция для безопасного удаления
safe_delete() {
    local file="$1"
    if [ -f "$file" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "  [DRY RUN] Удалить: $file"
        else
            echo "  ❌ Удалить: $file"
            rm "$file"
        fi
    else
        echo "  ⚠️  Файл не найден: $file"
    fi
}

# Функция для безопасного перемещения
safe_move() {
    local file="$1"
    local dest="$2"
    if [ -f "$file" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "  [DRY RUN] Переместить: $file → $dest"
        else
            echo "  📦 Переместить: $file → $dest"
            mkdir -p "$(dirname "$dest")"
            mv "$file" "$dest"
        fi
    else
        echo "  ⚠️  Файл не найден: $file"
    fi
}

# Создать архивную директорию
if [ "$DRY_RUN" = false ]; then
    mkdir -p "$ARCHIVE_DIR"
fi

echo "🗑️  Удаление устаревших файлов:"
echo ""

# Удалить устаревшие файлы
safe_delete "$PROJECT_ROOT/FEATURE_IMPLEMENTATION_PLAN.md"
safe_delete "$PROJECT_ROOT/IMPLEMENTATION_INDEX.md"
safe_delete "$PROJECT_ROOT/IMPLEMENTATION_SUMMARY.txt"
safe_delete "$PROJECT_ROOT/IMPROVEMENTS_SUMMARY.md"

echo ""
echo "📦 Перемещение исторических отчетов в архив:"
echo ""

# Переместить отчеты по оптимизациям
safe_move "$PROJECT_ROOT/OPTIMIZATION_REPORT_BATCH_FETCH.md" "$ARCHIVE_DIR/OPTIMIZATION_REPORT_BATCH_FETCH.md"
safe_move "$PROJECT_ROOT/OPTIMIZATION_REPORT_MEDIA_DEDUPE.md" "$ARCHIVE_DIR/OPTIMIZATION_REPORT_MEDIA_DEDUPE.md"
safe_move "$PROJECT_ROOT/OPTIMIZATION_REPORT_METADATA_CACHING.md" "$ARCHIVE_DIR/OPTIMIZATION_REPORT_METADATA_CACHING.md"
safe_move "$PROJECT_ROOT/OPTIMIZATION_REPORT_PART_SIZE.md" "$ARCHIVE_DIR/OPTIMIZATION_REPORT_PART_SIZE.md"
safe_move "$PROJECT_ROOT/OPTIMIZATION_REPORT_SHARD_COMPRESSION.md" "$ARCHIVE_DIR/OPTIMIZATION_REPORT_SHARD_COMPRESSION.md"

echo ""
if [ "$DRY_RUN" = true ]; then
    echo "✅ DRY RUN завершен. Для реального выполнения запустите без --dry-run"
else
    echo "✅ Очистка завершена!"
    echo ""
    echo "📊 Результат:"
    echo "  - Удалено: 4 устаревших файла"
    echo "  - Перемещено в архив: 5 исторических отчетов"
    echo "  - Архив: $ARCHIVE_DIR"
fi
