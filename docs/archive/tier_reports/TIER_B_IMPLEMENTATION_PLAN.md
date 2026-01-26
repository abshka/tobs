# TIER B: Strategic Performance Improvements

**Status:** 🟡 In Progress  
**Timeline:** 3 weeks (~120 hours)  
**Target:** 300-360 msg/s → 400+ msg/s (+15-25%)  
**Dependencies:** TIER S ✅ Complete, TIER A ✅ Complete

---

## 🎯 Overview

TIER B focuses on strategic performance improvements that provide long-term throughput gains through architectural optimizations. These changes require more effort than TIER A but deliver sustained 15-25% performance improvements.

**Key Areas:**
1. **Unified Thread Pool** - Eliminate thread pool contention
2. **Parallel Media Processing** - Process multiple media files concurrently
3. **Hash-Based Deduplication** - Reduce network traffic via content hashing
4. **Zero-Copy Media** - Optimize large file I/O
5. **Pagination Fixes** - Eliminate duplicate messages
6. **TTY-Aware Modes** - Better UX for different output contexts

---

## 📊 Expected Results

| Metric | Before TIER B | After TIER B | Improvement |
|--------|---------------|--------------|-------------|
| Throughput | 300-360 msg/s | 400+ msg/s | +15-25% |
| Memory Usage | Baseline | Stable | No regression |
| Network Traffic | Baseline | -10-20% | Hash dedup |
| I/O Performance | Baseline | +10-20% | Zero-copy |
| UX | Basic | Enhanced | TTY modes |

---

## 🗓️ Timeline

### Week 4 (Days 1-5)
- **B-1:** Thread Pool Unification (2 days)
- **B-2:** Zero-Copy Media (2 days)
- **B-3:** Parallel Media Processing - Design (1 day)

### Week 5 (Days 6-10)
- **B-3:** Parallel Media Processing - Implementation (3-4 days)
- **B-4:** Pagination Fixes (2 days)
- **B-5:** TTY-Aware Modes (1 day)

### Week 6 (Days 11-15)
- **B-6:** Hash-Based Deduplication (3-4 days)
- Integration Testing (2 days)
- Benchmarks & Metrics (1 day)

---

## 📦 Task Details


### B-1: Thread Pool Unification 🔧

**Priority:** P1 (Blocking for B-3)  
**Effort:** 2 days (~16 hours)  
**Impact:** +5-10% throughput, eliminate contention  
**Status:** 🔴 Not Started

#### Problem
Current architecture has 3 separate thread pools:
- `MediaDownloader` - local thread pool for I/O operations
- `MediaProcessor` - local thread pool for image/video processing
- `WhisperTranscriber` - local thread pool for audio transcription

This causes thread contention, inefficient resource utilization, and unpredictable performance.

#### Solution
Create a unified thread pool (`UnifiedThreadPool`) that all components share:
1. Single `ThreadPoolExecutor` with configurable size
2. Task prioritization (high/normal/low)
3. Metrics for monitoring (active threads, queue size, task latency)
4. Auto-tuning based on CPU cores

#### Implementation Steps

**Step 1:** Create `src/core/thread_pool.py` (4 hours)
```python
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Any
import asyncio
from enum import IntEnum

class TaskPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2

@dataclass
class ThreadPoolMetrics:
    active_threads: int
    queue_size: int
    completed_tasks: int
    failed_tasks: int
    avg_task_latency_ms: float

class UnifiedThreadPool:
    """Thread pool singleton для всех CPU-bound операций"""
    
    def __init__(self, max_workers: int | None = None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers or self._default_workers())
        self._metrics = ThreadPoolMetrics(0, 0, 0, 0, 0.0)
        
    def _default_workers(self) -> int:
        """Auto-tune: CPU cores * 2 (I/O bound) or CPU cores (CPU bound)"""
        import os
        return os.cpu_count() or 4
        
    async def submit(
        self, 
        fn: Callable, 
        *args, 
        priority: TaskPriority = TaskPriority.NORMAL,
        **kwargs
    ) -> Any:
        """Submit task to thread pool with priority"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args, **kwargs)
        
    def get_metrics(self) -> ThreadPoolMetrics:
        """Get current thread pool metrics"""
        return self._metrics
        
    def shutdown(self, wait: bool = True):
        """Shutdown thread pool"""
        self._executor.shutdown(wait=wait)

# Global singleton
_thread_pool: UnifiedThreadPool | None = None

def get_thread_pool() -> UnifiedThreadPool:
    """Get or create global thread pool singleton"""
    global _thread_pool
    if _thread_pool is None:
        _thread_pool = UnifiedThreadPool()
    return _thread_pool
```

**Step 2:** Update `src/config.py` (1 hour)
```python
# Add to Config class
MAX_THREADS: int = Field(
    default=0,  # 0 = auto-detect
    description="Max threads for unified thread pool (0=auto)"
)
THREAD_POOL_METRICS_ENABLED: bool = Field(
    default=True,
    description="Enable thread pool metrics collection"
)
```

**Step 3:** Update `src/media/downloader.py` (3 hours)
Replace local thread pool with unified pool:
```python
from src.core.thread_pool import get_thread_pool

class MediaDownloader:
    def __init__(self, ...):
        # REMOVE: self._executor = ThreadPoolExecutor(max_workers=4)
        self._thread_pool = get_thread_pool()
        
    async def _download_media_async(self, ...):
        # REPLACE: await loop.run_in_executor(self._executor, ...)
        return await self._thread_pool.submit(self._download_part, ...)
```

**Step 4:** Update `src/media/processor.py` (2 hours)
```python
from src.core.thread_pool import get_thread_pool, TaskPriority

class MediaProcessor:
    def __init__(self, ...):
        # REMOVE: self._executor = ThreadPoolExecutor(...)
        self._thread_pool = get_thread_pool()
        
    async def process_image(self, ...):
        # High priority for image processing
        return await self._thread_pool.submit(
            self._process_image_sync, 
            ..., 
            priority=TaskPriority.HIGH
        )
```

**Step 5:** Update `src/transcription.py` (2 hours)
```python
from src.core.thread_pool import get_thread_pool, TaskPriority

class WhisperTranscriber:
    def __init__(self, ...):
        # REMOVE: self._executor = ...
        self._thread_pool = get_thread_pool()
        
    async def transcribe(self, ...):
        # Normal priority for transcription (can be delayed)
        return await self._thread_pool.submit(
            self._transcribe_sync, 
            ..., 
            priority=TaskPriority.NORMAL
        )
```

**Step 6:** Create tests `tests/test_thread_pool.py` (4 hours)
```python
import pytest
import asyncio
from src.core.thread_pool import UnifiedThreadPool, TaskPriority

def test_thread_pool_singleton():
    """Test that get_thread_pool returns same instance"""
    from src.core.thread_pool import get_thread_pool
    pool1 = get_thread_pool()
    pool2 = get_thread_pool()
    assert pool1 is pool2

@pytest.mark.asyncio
async def test_submit_task():
    """Test task submission and execution"""
    pool = UnifiedThreadPool(max_workers=2)
    
    def cpu_bound_task(x):
        return x * 2
    
    result = await pool.submit(cpu_bound_task, 21)
    assert result == 42

@pytest.mark.asyncio
async def test_multiple_consumers():
    """Test multiple components using same pool"""
    pool = UnifiedThreadPool(max_workers=4)
    
    async def consumer_a():
        return await pool.submit(lambda: "A", priority=TaskPriority.HIGH)
    
    async def consumer_b():
        return await pool.submit(lambda: "B", priority=TaskPriority.NORMAL)
    
    results = await asyncio.gather(consumer_a(), consumer_b())
    assert set(results) == {"A", "B"}

@pytest.mark.asyncio
async def test_metrics():
    """Test metrics collection"""
    pool = UnifiedThreadPool(max_workers=2)
    await pool.submit(lambda: None)
    metrics = pool.get_metrics()
    assert metrics.completed_tasks >= 1
```

#### Acceptance Criteria
- [ ] Unit tests for `UnifiedThreadPool` pass
- [ ] All 3 components (downloader, processor, transcription) use unified pool
- [ ] Metrics show pool utilization (active threads, queue size)
- [ ] No throughput regression (baseline benchmark)
- [ ] py_compile passes for all modified files

#### Rollback Plan
Revert to local thread pools in each component if regressions occur.

---


### B-3: Parallel Media Processing 🚀

**Priority:** P1 (Depends on B-1)  
**Effort:** 4 days (~32 hours)  
**Impact:** +15-25% throughput  
**Status:** 🔴 Not Started

#### Problem
Media processing (download + format + transcode) happens sequentially. While messages are fetched in parallel (sharding), media is processed one-by-one, bottlenecking the pipeline.

#### Solution
Process multiple media files concurrently:
1. Semaphore to control max parallel media operations
2. Process messages in parallel while preserving order for writing
3. ENV parameter `MAX_PARALLEL_MEDIA` for control
4. Memory monitoring to prevent OOM

#### Implementation Steps

**Step 1:** Create `src/media/parallel_processor.py` (8 hours)
```python
import asyncio
from typing import List
from dataclasses import dataclass

@dataclass
class ParallelMediaConfig:
    max_concurrent: int = 4  # Max parallel media operations
    memory_limit_mb: int = 2048  # Abort if memory exceeds this
    enable_parallel: bool = True

class ParallelMediaProcessor:
    """Process media files in parallel with concurrency control"""
    
    def __init__(self, config: ParallelMediaConfig):
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._active_tasks = 0
        
    async def process_batch(
        self, 
        messages: List[Message], 
        process_fn: Callable
    ) -> List[ProcessedMessage]:
        """Process messages with media in parallel"""
        tasks = []
        for msg in messages:
            if self._has_media(msg):
                task = self._process_with_semaphore(msg, process_fn)
            else:
                task = process_fn(msg)  # No media, process immediately
            tasks.append(task)
        
        return await asyncio.gather(*tasks)
        
    async def _process_with_semaphore(self, msg, process_fn):
        """Process single message with concurrency control"""
        async with self._semaphore:
            self._active_tasks += 1
            try:
                # Check memory before processing
                if not self._check_memory():
                    logger.warning("Memory limit exceeded, throttling")
                    await asyncio.sleep(1)
                    
                return await process_fn(msg)
            finally:
                self._active_tasks -= 1
    
    def _check_memory(self) -> bool:
        """Check if memory usage is under limit"""
        import psutil
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        return mem_mb < self._config.memory_limit_mb
```

**Step 2:** Update `src/config.py` (1 hour)
```python
MAX_PARALLEL_MEDIA: int = Field(
    default=4,  # Conservative default
    description="Max concurrent media processing operations"
)
PARALLEL_MEDIA_PROCESSING: bool = Field(
    default=True,
    description="Enable parallel media processing"
)
PARALLEL_MEDIA_MEMORY_LIMIT_MB: int = Field(
    default=2048,
    description="Memory limit for parallel processing (MB)"
)
```

**Step 3:** Integrate into `src/export/exporter.py` (12 hours)
```python
from src.media.parallel_processor import ParallelMediaProcessor, ParallelMediaConfig

class Exporter:
    def __init__(self, ...):
        parallel_config = ParallelMediaConfig(
            max_concurrent=self.config.max_parallel_media,
            memory_limit_mb=self.config.parallel_media_memory_limit_mb,
            enable_parallel=self.config.parallel_media_processing
        )
        self._parallel_processor = ParallelMediaProcessor(parallel_config)
        
    async def _export_regular_target(self, ...):
        # Replace sequential processing with parallel
        if self.config.parallel_media_processing:
            processed = await self._parallel_processor.process_batch(
                batch, 
                self._process_message_parallel
            )
        else:
            # Fallback to sequential
            processed = [await self._process_message_parallel(m) for m in batch]
```

**Step 4:** Add metrics (4 hours)
```python
@dataclass
class ParallelMediaMetrics:
    total_media_processed: int
    concurrent_peak: int
    avg_concurrency: float
    memory_throttles: int
    
# Track in ExportStatistics
parallel_media_metrics: ParallelMediaMetrics | None = None
```

**Step 5:** Create tests `tests/test_parallel_media.py` (7 hours)
```python
@pytest.mark.asyncio
async def test_parallel_processing():
    """Test parallel media processing with mock messages"""
    config = ParallelMediaConfig(max_concurrent=2)
    processor = ParallelMediaProcessor(config)
    
    # Mock messages with media
    messages = [create_mock_message(has_media=True) for _ in range(10)]
    
    start = time.time()
    results = await processor.process_batch(messages, mock_process_fn)
    duration = time.time() - start
    
    # Should be faster than sequential (10 * 0.1s = 1s vs ~0.5s parallel)
    assert duration < 0.7
    assert len(results) == 10

@pytest.mark.asyncio
async def test_memory_throttling():
    """Test memory limit enforcement"""
    # Mock memory check to exceed limit
    ...
```

#### Acceptance Criteria
- [ ] Unit tests pass
- [ ] Benchmark shows +15-25% throughput on media-heavy chat
- [ ] Memory usage stable (no leaks)
- [ ] ENV parameter `PARALLEL_MEDIA_PROCESSING` works
- [ ] Metrics show concurrent peak and throttles

#### Rollback Plan
Set `PARALLEL_MEDIA_PROCESSING=False` to revert to sequential processing.

---


### B-6: Hash-Based Media Deduplication 🔐

**Priority:** P2 (Independent)  
**Effort:** 4 days (~32 hours)  
**Impact:** -10-20% network traffic  
**Status:** 🔴 Not Started

#### Problem
Current ID-based deduplication misses files with same content but different IDs. Identical photos/videos uploaded multiple times are downloaded repeatedly.

#### Solution
Use Telethon's `upload.GetFileHashes` to compute content hashes:
1. Fetch file hash before download
2. Check if hash exists in persistent cache
3. Reuse existing file if hash matches
4. Fallback to ID-based if hash unavailable

#### Implementation Steps

**Step 1:** Create `src/media/hash_dedup.py` (12 hours)
```python
from telethon.tl.functions.upload import GetFileHashes
from typing import Dict, Optional
import hashlib

class HashBasedDeduplicator:
    """Content-based media deduplication using file hashes"""
    
    def __init__(self, cache_path: Path, max_cache_size: int = 10000):
        self._cache_path = cache_path
        self._hash_cache: Dict[str, Path] = {}  # hash -> file_path
        self._max_cache_size = max_cache_size
        self._load_cache()
        
    async def get_file_hash(self, client, media) -> Optional[str]:
        """Get file hash using Telethon API"""
        try:
            # Use upload.GetFileHashes to get content hash
            hashes = await client(GetFileHashes(
                location=media,
                offset=0
            ))
            if hashes:
                # Combine all chunk hashes into single hash
                combined = hashlib.sha256()
                for h in hashes:
                    combined.update(h.hash)
                return combined.hexdigest()
        except Exception as e:
            logger.debug(f"Failed to get file hash: {e}")
            return None
    
    async def check_cache(self, file_hash: str) -> Optional[Path]:
        """Check if file with this hash already exists"""
        return self._hash_cache.get(file_hash)
        
    def add_to_cache(self, file_hash: str, file_path: Path):
        """Add file to hash cache"""
        if len(self._hash_cache) >= self._max_cache_size:
            # Evict oldest entry (simple FIFO)
            self._hash_cache.pop(next(iter(self._hash_cache)))
        self._hash_cache[file_hash] = file_path
        self._save_cache()
    
    def _load_cache(self):
        """Load hash cache from disk"""
        if self._cache_path.exists():
            import msgpack
            with open(self._cache_path, 'rb') as f:
                data = msgpack.unpackb(f.read(), raw=False)
                self._hash_cache = {k: Path(v) for k, v in data.items()}
    
    def _save_cache(self):
        """Save hash cache to disk"""
        import msgpack
        data = {k: str(v) for k, v in self._hash_cache.items()}
        with open(self._cache_path, 'wb') as f:
            f.write(msgpack.packb(data, use_bin_type=True))
```

**Step 2:** Integrate into `src/media/downloader.py` (8 hours)
```python
from src.media.hash_dedup import HashBasedDeduplicator

class MediaDownloader:
    def __init__(self, ...):
        self._hash_dedup = HashBasedDeduplicator(
            cache_path=cache_dir / "media_hash_cache.bin",
            max_cache_size=10000
        )
        
    async def download_media(self, message, ...):
        # Try hash-based dedup first
        if self.config.hash_based_deduplication:
            file_hash = await self._hash_dedup.get_file_hash(
                self.telegram_manager.client, 
                message.media
            )
            if file_hash:
                cached_path = await self._hash_dedup.check_cache(file_hash)
                if cached_path and cached_path.exists():
                    logger.info(f"Hash-based cache HIT: {file_hash[:16]}")
                    self._stats['hash_dedup_hits'] += 1
                    return cached_path
        
        # Fallback to ID-based dedup
        if doc_id in self._id_cache:
            logger.debug(f"ID-based cache HIT: {doc_id}")
            return self._id_cache[doc_id]
        
        # Download file
        path = await self._download_file(...)
        
        # Add to both caches
        self._id_cache[doc_id] = path
        if file_hash:
            self._hash_dedup.add_to_cache(file_hash, path)
        
        return path
```

**Step 3:** Update `src/config.py` (1 hour)
```python
HASH_BASED_DEDUPLICATION: bool = Field(
    default=True,
    description="Enable hash-based media deduplication"
)
HASH_CACHE_MAX_SIZE: int = Field(
    default=10000,
    description="Max entries in hash cache"
)
```

**Step 4:** Add metrics (3 hours)
```python
@dataclass
class MediaDeduplicationStats:
    id_based_hits: int = 0
    hash_based_hits: int = 0
    downloads: int = 0
    bytes_saved: int = 0
    
    def hit_rate(self) -> float:
        total = self.id_based_hits + self.hash_based_hits + self.downloads
        if total == 0:
            return 0.0
        return (self.id_based_hits + self.hash_based_hits) / total
```

**Step 5:** Create tests `tests/test_hash_dedup.py` (8 hours)
```python
@pytest.mark.asyncio
async def test_hash_dedup_cache_hit():
    """Test hash-based cache hit"""
    dedup = HashBasedDeduplicator(tmp_path / "cache.bin")
    
    # Add file to cache
    file_hash = "abc123"
    file_path = tmp_path / "test.jpg"
    file_path.write_bytes(b"test data")
    dedup.add_to_cache(file_hash, file_path)
    
    # Check cache
    cached = await dedup.check_cache(file_hash)
    assert cached == file_path

@pytest.mark.asyncio
async def test_fallback_to_id_based():
    """Test fallback when hash unavailable"""
    # Mock GetFileHashes to fail
    # Verify ID-based dedup is used
    ...
```

#### Acceptance Criteria
- [ ] Hash-based dedup works correctly
- [ ] Persistent cache survives restarts
- [ ] Fallback to ID-based when hash fails
- [ ] Benchmark shows 10-20% traffic reduction
- [ ] Metrics show hit rates for both methods

#### Rollback Plan
Set `HASH_BASED_DEDUPLICATION=False` to disable hash-based dedup.

---


### B-2: Zero-Copy Media (os.sendfile) 💨

**Priority:** P3  
**Effort:** 2 days (~16 hours)  
**Impact:** +10-20% I/O performance  
**Status:** 🔴 Not Started

**Brief:** Use `os.sendfile()` for large file copies (>1MB) to avoid userspace buffering.

---

### B-4: Pagination Fixes 📄

**Priority:** P3  
**Effort:** 2 days (~16 hours)  
**Impact:** Eliminate duplicates  
**Status:** 🔴 Not Started

**Brief:** Fix message deduplication logic to track `last_message_id` across batches and use BloomFilter for resume.

---

### B-5: TTY-Aware Modes 🖥️

**Priority:** P4  
**Effort:** 1 day (~8 hours)  
**Impact:** Better UX  
**Status:** 🔴 Not Started

**Brief:** Detect TTY/pipe context and adapt output (rich progress vs JSON logs).

---

## 📈 Success Metrics

### Performance Targets
- **Throughput:** 300-360 msg/s → 400+ msg/s (+15-25%)
- **CPU Usage:** 35% → 45% (controlled increase)
- **Memory:** Stable (no leaks)
- **Network Traffic:** -10-20% (hash dedup)
- **I/O Performance:** +10-20% (zero-copy)

### Quality Gates
- [ ] All unit tests pass (coverage >80%)
- [ ] Integration tests pass
- [ ] Benchmarks show expected improvements
- [ ] No regressions in baseline scenarios
- [ ] py_compile passes for all files
- [ ] Documentation updated

---

## 🔄 Progress Tracking

Update this section as tasks complete:

**Week 4 Progress:**
- [ ] B-1: Thread Pool Unification
- [ ] B-2: Zero-Copy Media
- [ ] B-3: Parallel Media (Design)

**Week 5 Progress:**
- [ ] B-3: Parallel Media (Implementation)
- [ ] B-4: Pagination Fixes
- [ ] B-5: TTY-Aware Modes

**Week 6 Progress:**
- [ ] B-6: Hash-Based Deduplication
- [ ] Integration Testing
- [ ] Benchmarks & Metrics

---

## 🚀 Getting Started

To begin TIER B implementation:

1. **Create feature branch:**
   ```bash
   git checkout -b feature/tier-b-performance
   ```

2. **Start with B-1 (Thread Pool):**
   ```bash
   # Create module
   touch src/core/thread_pool.py
   
   # Create tests
   touch tests/test_thread_pool.py
   
   # Verify baseline
   pytest tests/
   python -m py_compile src/**/*.py
   ```

3. **Follow the task order:** B-1 → B-3 → B-6 → B-2 → B-4 → B-5

4. **Update progress in Memory after each task**

---

**Last Updated:** 2025-01-05  
**Status:** 🟡 Ready to Begin  
**Next Action:** Start B-1 implementation

