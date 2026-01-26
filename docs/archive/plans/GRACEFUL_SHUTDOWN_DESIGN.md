# Graceful Shutdown Implementation Design

**TIER A - Task 3**  
**Приоритет:** Medium  
**Сложность:** Medium-High  
**Timeline:** 3-5 дней  
**Статус:** 🔄 In Progress

---

## 🎯 Цель

Реализовать двухступенчатый механизм Ctrl+C:
1. **Первый Ctrl+C:** Graceful shutdown с полным cleanup
2. **Второй Ctrl+C (в течение 5 сек):** Force shutdown

---

## 📐 Дизайн

### Current State (Проблемы)

```python
# main.py - текущая реализация
def handle_sigint(signum, frame):
    """Handle SIGINT (Ctrl+C) signal."""
    rprint("\n[bold yellow]Received interrupt signal. Cleaning up...[/bold yellow]")
    sys.exit(0)  # ❌ Просто exit без cleanup
```

**Проблемы:**
- ❌ Нет graceful shutdown - данные могут быть потеряны
- ❌ Буферы не flushed (log_batcher, export file)
- ❌ Telegram connections не закрыты properly
- ❌ Progress state не сохранён
- ❌ Нет возможности force shutdown если graceful зависает

---

### Target Architecture

```
User presses Ctrl+C
        ↓
┌───────────────────────────────────────┐
│   First SIGINT Received               │
│   - Set shutdown_requested = True     │
│   - Print: "Stopping gracefully..."   │
│   - Start 5-second timer              │
└───────────────────────────────────────┘
        ↓
┌───────────────────────────────────────┐
│   Graceful Shutdown Sequence          │
│   1. Stop accepting new messages      │
│   2. Finish current message batch     │
│   3. Flush all buffers:               │
│      - LogBatcher                     │
│      - AsyncBufferedSaver             │
│      - Pipeline queues                │
│   4. Save progress state              │
│   5. Close Telegram connections       │
│   6. Clean exit(0)                    │
└───────────────────────────────────────┘
        ↓
    Success → Exit 0

If Ctrl+C again within 5 seconds:
        ↓
┌───────────────────────────────────────┐
│   Second SIGINT Received (Force)      │
│   - Print: "Force shutdown!"          │
│   - Minimal cleanup                   │
│   - Immediate exit(1)                 │
└───────────────────────────────────────┘
```

---

## 🔧 Implementation Plan

### Step 1: Global Shutdown State (30 min)

**File:** `src/shutdown_manager.py` (новый модуль)

```python
"""
Shutdown manager for graceful application termination.
Coordinates cleanup across all subsystems.
"""
import asyncio
import signal
import time
from typing import Callable, List, Optional
from rich import print as rprint

class ShutdownManager:
    """
    Manages graceful shutdown process.
    
    Features:
    - Two-stage Ctrl+C (graceful → force)
    - Cleanup hook registration
    - Async-safe shutdown coordination
    """
    
    def __init__(self, force_shutdown_timeout: float = 5.0):
        self.shutdown_requested = False
        self.force_shutdown = False
        self.first_sigint_time: Optional[float] = None
        self.force_shutdown_timeout = force_shutdown_timeout
        self._cleanup_hooks: List[Callable] = []
        self._async_cleanup_hooks: List[Callable] = []
        
    def register_cleanup_hook(self, hook: Callable) -> None:
        """Register sync cleanup function."""
        self._cleanup_hooks.append(hook)
        
    def register_async_cleanup_hook(self, hook: Callable) -> None:
        """Register async cleanup coroutine."""
        self._async_cleanup_hooks.append(hook)
        
    def handle_sigint(self, signum: int, frame) -> None:
        """Handle SIGINT signal with two-stage shutdown."""
        if not self.shutdown_requested:
            # First Ctrl+C - graceful shutdown
            self.shutdown_requested = True
            self.first_sigint_time = time.time()
            rprint("\n[bold yellow]⏸️  Graceful shutdown initiated...[/bold yellow]")
            rprint("[cyan]ℹ️  Press Ctrl+C again within 5 seconds to force shutdown.[/cyan]")
        else:
            # Second Ctrl+C - force shutdown
            elapsed = time.time() - (self.first_sigint_time or 0)
            if elapsed < self.force_shutdown_timeout:
                self.force_shutdown = True
                rprint("\n[bold red]⚠️  FORCE SHUTDOWN - immediate exit![/bold red]")
                self._run_minimal_cleanup()
                import sys
                sys.exit(1)
                
    def _run_minimal_cleanup(self) -> None:
        """Minimal cleanup on force shutdown."""
        try:
            # Only critical cleanup
            from src.logging.global_batcher import global_batcher
            global_batcher.flush()  # Flush logs immediately
        except Exception as e:
            print(f"Warning: minimal cleanup error: {e}")
            
    async def run_graceful_cleanup(self) -> None:
        """Execute all registered cleanup hooks."""
        rprint("[cyan]🧹 Running cleanup hooks...[/cyan]")
        
        # Run sync hooks
        for hook in self._cleanup_hooks:
            try:
                hook()
            except Exception as e:
                rprint(f"[yellow]⚠️  Cleanup hook error: {e}[/yellow]")
                
        # Run async hooks
        for hook in self._async_cleanup_hooks:
            try:
                await hook()
            except Exception as e:
                rprint(f"[yellow]⚠️  Async cleanup hook error: {e}[/yellow]")
                
        rprint("[green]✅ Cleanup complete[/green]")

# Global instance
shutdown_manager = ShutdownManager()
```

---

### Step 2: Integration into main.py (1 hour)

**Changes in main.py:**

```python
# Add import
from src.shutdown_manager import shutdown_manager

# Replace handle_sigint
def handle_sigint(signum, frame):
    """Delegate to ShutdownManager."""
    shutdown_manager.handle_sigint(signum, frame)

# In async_main():
async def async_main():
    # ... existing setup ...
    
    # Register cleanup hooks
    if core_manager:
        shutdown_manager.register_cleanup_hook(
            lambda: core_manager.get_cache_manager().close()
        )
    
    if telegram_manager:
        shutdown_manager.register_async_cleanup_hook(
            telegram_manager.disconnect
        )
        
    if http_session:
        shutdown_manager.register_async_cleanup_hook(
            http_session.close
        )
        
    # Add to global_batcher shutdown
    from src.logging.global_batcher import global_batcher
    shutdown_manager.register_cleanup_hook(global_batcher.flush)
    
    try:
        # ... existing export logic ...
        
        # Check shutdown_requested periodically
        if shutdown_manager.shutdown_requested:
            rprint("[yellow]⏸️  Shutdown requested, finishing current batch...[/yellow]")
            break  # Exit main loop gracefully
            
    finally:
        # Run cleanup
        await shutdown_manager.run_graceful_cleanup()
```

---

### Step 3: Exporter Integration (2 hours)

**File:** `src/export/exporter.py`

Add shutdown checks in hot paths:

```python
class Exporter:
    async def _export_regular_target(self, ...):
        # In main message loop
        for batch_number, batch in enumerate(batches):
            # Check shutdown every batch
            from src.shutdown_manager import shutdown_manager
            if shutdown_manager.shutdown_requested:
                logger.info("🛑 Shutdown requested, stopping message fetch")
                break
                
            # ... process batch ...
            
    async def _process_batch(self, ...):
        # Check before heavy processing
        if shutdown_manager.shutdown_requested:
            return  # Skip processing
```

---

### Step 4: AsyncPipeline Integration (1 hour)

**File:** `src/export/pipeline.py`

```python
async def run(self, ...):
    from src.shutdown_manager import shutdown_manager
    
    try:
        # In fetch loop
        async for msg in self._fetch_messages():
            if shutdown_manager.shutdown_requested:
                logger.info("Pipeline: shutdown requested, stopping fetch")
                break
            await fetch_queue.put(msg)
            
        # In worker loops
        while True:
            if shutdown_manager.shutdown_requested:
                break  # Exit worker loop
```

---

### Step 5: Buffer Flushing (1 hour)

Ensure all buffers are flushed on shutdown:

**1. LogBatcher:**
```python
# Already handled via cleanup hook:
shutdown_manager.register_cleanup_hook(global_batcher.flush)
```

**2. AsyncBufferedSaver:**
```python
# In exporter cleanup:
async def _cleanup_saver(self):
    if self.saver:
        await self.saver.flush()
        
shutdown_manager.register_async_cleanup_hook(self._cleanup_saver)
```

**3. Pipeline Queues:**
```python
# Drain queues before exit
async def _drain_queue(queue: asyncio.Queue):
    while not queue.empty():
        try:
            item = queue.get_nowait()
            # Process or discard
        except asyncio.QueueEmpty:
            break
```

---

### Step 6: Progress State Saving (1 hour)

Save progress on graceful shutdown:

```python
# In exporter
async def _save_progress_on_shutdown(self):
    """Save current progress for resume."""
    if self.last_processed_id:
        progress_file = self.config.get_progress_file(self.entity_id)
        with open(progress_file, 'w') as f:
            json.dump({
                'last_message_id': self.last_processed_id,
                'timestamp': time.time(),
                'messages_processed': self.stats.messages_processed
            }, f)
            
shutdown_manager.register_async_cleanup_hook(self._save_progress_on_shutdown)
```

---

### Step 7: Tests (2-3 дня)

**tests/test_shutdown_manager.py:**

```python
import pytest
import signal
import time
from src.shutdown_manager import ShutdownManager

def test_first_sigint_sets_graceful_flag():
    """First Ctrl+C should set shutdown_requested."""
    mgr = ShutdownManager()
    mgr.handle_sigint(signal.SIGINT, None)
    
    assert mgr.shutdown_requested is True
    assert mgr.force_shutdown is False
    
def test_second_sigint_within_timeout_forces_shutdown():
    """Second Ctrl+C within 5s should force shutdown."""
    mgr = ShutdownManager(force_shutdown_timeout=1.0)
    
    # First SIGINT
    mgr.handle_sigint(signal.SIGINT, None)
    assert mgr.shutdown_requested is True
    
    # Second SIGINT immediately
    with pytest.raises(SystemExit) as exc:
        mgr.handle_sigint(signal.SIGINT, None)
    assert exc.value.code == 1
    assert mgr.force_shutdown is True
    
def test_cleanup_hooks_executed():
    """Cleanup hooks should be called."""
    mgr = ShutdownManager()
    executed = []
    
    mgr.register_cleanup_hook(lambda: executed.append('sync'))
    
    import asyncio
    asyncio.run(mgr.run_graceful_cleanup())
    
    assert 'sync' in executed
```

**tests/test_graceful_shutdown_integration.py:**

```python
@pytest.mark.asyncio
async def test_exporter_stops_on_shutdown():
    """Exporter should stop gracefully when shutdown requested."""
    # Mock setup
    config = Config(...)
    telegram_manager = Mock()
    
    exporter = Exporter(config, telegram_manager)
    
    # Trigger shutdown mid-export
    async def trigger_shutdown():
        await asyncio.sleep(0.5)
        shutdown_manager.shutdown_requested = True
        
    await asyncio.gather(
        exporter.export(entity),
        trigger_shutdown()
    )
    
    # Verify: export stopped gracefully, buffers flushed
    assert exporter.stats.messages_processed > 0
    # ... more assertions
```

---

## ✅ Acceptance Criteria

- [ ] First Ctrl+C triggers graceful shutdown
- [ ] Second Ctrl+C (within 5 sec) forces immediate exit
- [ ] All buffers flushed before exit (logs, export files)
- [ ] Telegram connections closed cleanly
- [ ] Progress state saved for resume
- [ ] No data loss on graceful shutdown
- [ ] Unit tests pass (shutdown_manager)
- [ ] Integration tests pass (exporter + pipeline)
- [ ] Manual testing: export interrupted gracefully

---

## 🚀 Implementation Order

**Day 1:**
- ✅ Design document (this file)
- Step 1: ShutdownManager module
- Step 2: main.py integration
- Initial tests

**Day 2:**
- Step 3: Exporter integration
- Step 4: AsyncPipeline integration
- More tests

**Day 3:**
- Step 5: Buffer flushing
- Step 6: Progress state saving
- Integration tests

**Day 4-5:**
- Full test suite
- Manual testing
- Bug fixes
- Documentation

---

## 📝 Notes

**Edge Cases:**
- Force shutdown while cleanup in progress → minimal cleanup only
- Shutdown during Takeout init → cleanup Takeout session
- Shutdown during media download → stop downloads, keep downloaded files
- Shutdown during transcription → stop transcription, mark incomplete

**Compatibility:**
- Must work with existing AsyncPipeline
- Must work with ShardedTelegramManager
- Must not break sequential exporter fallback

**Rollback Plan:**
- If issues found, temporarily disable graceful shutdown
- Revert to simple sys.exit(0) handler
- Feature flag: ENABLE_GRACEFUL_SHUTDOWN=false

---

**Design Status:** ✅ READY FOR IMPLEMENTATION  
**Next Action:** Create `src/shutdown_manager.py`
