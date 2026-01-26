# Progress Bar Integration - Total Message Count

## Implemented Changes (2025-01-XX)

### Summary
Integrated `get_total_message_count()` into exporter to display accurate progress percentages instead of `0/0 messages`.

### Changes Made

#### 1. Added total count fetch before export (`exporter.py:1153-1156`)
```python
# ✨ Get total message count BEFORE starting export (for accurate progress %)
logger.info(f"📊 Fetching total message count for {entity_name}...")
total_messages = await self.telegram_manager.get_total_message_count(entity)
logger.info(f"📊 Total messages in chat: {total_messages:,}")
```

#### 2. Updated OutputManager initialization (`exporter.py:1158-1160`)
```python
output_mgr = get_output_manager()
output_mgr.start_export(entity_name, total_messages=total_messages)  # ← Pass total
```

#### 3. Enhanced Progress bar with visual elements (`exporter.py:1162-1177`)
**Before:**
```python
with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    TextColumn("•"),
    TextColumn("[cyan]{task.fields[messages]} msgs"),  # No total
    ...
) as progress:
    task_id = progress.add_task(..., total=None, ...)  # No total
```

**After:**
```python
with Progress(
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),  # ← Visual progress bar
    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),  # ← Percentage
    TextColumn("•"),
    TextColumn("[cyan]{task.fields[messages]}/{task.total} msgs"),  # ← Show X/Total
    ...
) as progress:
    task_id = progress.add_task(..., total=total_messages, ...)  # ← Pass total
```

#### 4. Updated all `progress.update()` calls (4 locations)
Added `completed=processed_count` parameter to enable percentage calculation:

**Locations:**
- Line ~1304: Pipeline writer callback
- Line ~1543: Periodic progress update
- Line ~1635: Final progress update
- Line ~2591: Batch processing stats

**Pattern:**
```python
progress.update(
    task_id_progress,
    completed=processed_count,  # ← For percentage calculation
    messages=processed_count,
    media=media_count,
)
```

### Expected Output

**Before:**
```
⠋ Exporting ChatName... • 12,450 msgs • 234 media
```

**After:**
```
⠋ Exporting ChatName... ███████████░░░░░░░░░  56% • 28,500/50,647 msgs • 1,234 media
```

### API Efficiency

- **Cost:** 1 lightweight API call (`GetHistoryRequest(limit=1)`)
- **Timing:** ~50-100ms (runs once before export starts)
- **Benefit:** Accurate progress tracking throughout entire export

### Verification Steps

1. ✅ Code compiles without errors
2. ⏳ Run export and verify:
   - Progress bar shows percentage (e.g., `23%`)
   - Message count shows `X/Total` format
   - Visual progress bar fills as export proceeds
   - No performance regression

### Related Files

- `src/export/exporter.py` - Main integration point
- `src/telegram_client.py` - `get_total_message_count()` helper (already implemented)

### Next Steps

1. Run full export to validate display
2. Check for ANSI escape sequences in non-TTY logs (separate issue)
3. Consider adding total count to OutputManager progress updates
