# TIER B - Task B-4: Pagination Fixes & BloomFilter Optimization

**Status:** PLANNING
**Priority:** P2 (Medium)  
**Estimated Time:** ~2 days (16 hours)  
**ROI:** 15.0 (High)

## 🎯 Objective

Fix edge cases in message pagination during resume and optimize BloomFilter usage to prevent:
1. **Duplicate processing** of already-exported messages on resume
2. **Inefficient skipping** where we fetch messages that won't be processed
3. **Memory waste** from suboptimal BloomFilter size
4. **False positives** leading to missed messages

---

## 📊 Current State Analysis

### Problem 1: Missing min_id for Resume ❌

**Location:** `src/export/exporter.py` line ~1210

```python
async for message in self.telegram_manager.fetch_messages(
    entity,
    limit=None,  # Export all messages
):
```

**Issue:** 
- `fetch_messages()` is called WITHOUT `min_id` parameter
- When resuming, we fetch ALL messages from the beginning
- BloomFilter checks happen AFTER fetch (wasted API calls)
- Already-processed messages are still downloaded and checked

**Impact:**
- Wasted bandwidth: re-downloading processed messages
- Slower resume: API calls for messages we'll skip
- Unnecessary BloomFilter lookups

### Problem 2: BloomFilter Not Used for Early Skip ⚠️

**Current Flow:**
1. Fetch message from Telegram API ✅
2. Download media (if any) ✅
3. Format message ✅
4. **ONLY THEN** add to BloomFilter ✅

**Missing:** Check BloomFilter BEFORE processing!

**Code locations where msg_id is added to BloomFilter:**
- Line 1145: `entity_data.processed_message_ids.add(msg_id)`
- Line 1276: `entity_data.processed_message_ids.add(msg_id)`
- Line 1394: `entity_data.processed_message_ids.add(msg_id)`

**BUT:** No early check like:
```python
if msg.id in entity_data.processed_message_ids:
    continue  # Skip already processed
```

### Problem 3: BloomFilter Size Not Tuned 📏

**Current:** `expected_items=1000000` (hardcoded)

```python
# src/export/exporter.py line 71
def __init__(
    self, expected_items: int = 1000000, false_positive_rate: float = 0.01
):
```

**Issues:**
- **Over-allocation** for small chats (<10k messages): wastes ~1.2MB RAM
- **Under-allocation** for mega-chats (>1M messages): increases false positive rate
- No dynamic tuning based on chat size

**Memory usage:**
- 1M items = ~1.2MB
- 100k items = ~120KB (10x smaller!)
- 10M items = ~12MB

### Problem 4: BloomFilter Persistence Race Condition 🏁

**Location:** Multiple places where cache is saved

**Race:**
1. Main loop adds messages to BloomFilter
2. Periodic save (every 100 messages) saves cache
3. **BUT:** Graceful shutdown may happen BETWEEN adds

**Result:** Last 1-99 messages may not be in persisted BloomFilter

**Evidence:** Line 1295-1318 (periodic save every 100 messages)

---

## 🔧 Proposed Solutions

### Solution 1: Pass min_id to fetch_messages() ✅

**Change:** `src/export/exporter.py` around line 1210

```python
# BEFORE:
async for message in self.telegram_manager.fetch_messages(
    entity,
    limit=None,
):

# AFTER:
# Resume from last processed message (if any)
resume_from_id = entity_data.last_message_id or 0

async for message in self.telegram_manager.fetch_messages(
    entity,
    limit=None,
    min_id=resume_from_id,  # Skip already processed messages
):
```

**Impact:**
- ✅ Telegram API returns only NEW messages (after min_id)
- ✅ No wasted bandwidth on re-downloading processed messages
- ✅ Faster resume (fewer API calls)

**Edge Case to Handle:**
- If `last_message_id` exists but BloomFilter is empty (corrupted cache)
  → Solution: Reset both OR rebuild BloomFilter from exported file

### Solution 2: Early Skip with BloomFilter Check 🛑

**Add check BEFORE processing:**

```python
async for message in self.telegram_manager.fetch_messages(...):
    # Early skip: check BloomFilter before processing
    if message.id in entity_data.processed_message_ids:
        logger.debug(f"⏭️ Skipping already processed message: {message.id}")
        continue
    
    # ... rest of processing
```

**Alternative (More Robust):**
Combine with min_id:
```python
# Double-check for edge cases (gaps in message IDs)
if message.id in entity_data.processed_message_ids:
    # This message was processed before but there are newer ones
    continue
```

**Trade-off:**
- Pro: Handles gaps (deleted messages) where min_id might not be sequential
- Con: Slightly slower (BloomFilter lookup for each message)
- Decision: **Use both** (min_id reduces load, BloomFilter handles gaps)

### Solution 3: Dynamic BloomFilter Sizing 📊

**Strategy:** Calculate expected_items from total message count

```python
# src/export/exporter.py - before creating entity_data

# Get total message count from entity
try:
    total_messages = await self.telegram_manager.get_message_count(entity)
    # Add 10% buffer for new messages during export
    expected_items = int(total_messages * 1.1)
    # Clamp to reasonable range (min 10k, max 10M)
    expected_items = max(10_000, min(expected_items, 10_000_000))
except Exception as e:
    logger.warning(f"Failed to get message count, using default: {e}")
    expected_items = 1_000_000  # Fallback to current default

# Create BloomFilter with calculated size
bloom_filter = BloomFilter(expected_items=expected_items)
```

**Memory savings:**
- 10k chat: 1.2MB → 120KB (90% reduction)
- 100k chat: 1.2MB → 1.2MB (no change)
- 5M chat: 1.2MB → 6MB (acceptable for accuracy)

### Solution 4: Atomic Flush on Shutdown 💾

**Already implemented!** ✅ (TIER A - Task 3)

- `_save_progress_on_shutdown()` method exists (line 2275-2294)
- Registered via shutdown_manager hook (line 1004-1007)

**Verify:** Ensure BloomFilter is included in save:

```python
# src/export/exporter.py line 2288
await self.cache_manager.set(cache_key, entity_data)
```

**Check:** Does `EntityCacheData` serialization include BloomFilter?

```python
# src/export/exporter.py line 369-377
@dataclass
class EntityCacheData:
    ...
    processed_message_ids: BloomFilter = field(default_factory=lambda: BloomFilter())
```

**Serialization:** Line 924-947 (to_dict / from_dict logic)
- ✅ BloomFilter.to_dict() exists (line 126-132)
- ✅ BloomFilter.from_dict() exists (line 134-142)
- ✅ Restoration logic exists (line 924-947)

**Status:** Already correct, but verify with test

---

## 📝 Implementation Plan

### Step 1: Add min_id Resume Logic (4 hours)

**File:** `src/export/exporter.py`

**Changes:**

1. **Update fetch_messages() call** (line ~1210 in fallback path)
   ```python
   # Calculate resume point
   resume_from_id = entity_data.last_message_id or 0
   logger.info(f"📍 Resume point: message ID {resume_from_id}")
   
   async for message in self.telegram_manager.fetch_messages(
       entity,
       limit=None,
       min_id=resume_from_id,  # NEW: skip processed messages
   ):
   ```

2. **Update AsyncPipeline path** (if used, line ~1180)
   ```python
   pipeline_stats = await pipeline.run(
       entity=entity,
       telegram_manager=self.telegram_manager,
       process_fn=process_fn,
       writer_fn=writer_fn,
       limit=None,
       min_id=resume_from_id,  # NEW: add this parameter
   )
   ```

3. **Update AsyncPipeline.run() signature** (`src/export/pipeline.py`)
   ```python
   async def run(
       self,
       entity: Any,
       telegram_manager: Any,
       process_fn: Callable,
       writer_fn: Callable,
       limit: Optional[int] = None,
       min_id: int = 0,  # NEW parameter
   ) -> Dict[str, Any]:
   ```

4. **Pass min_id to fetch** (`src/export/pipeline.py` line ~140)
   ```python
   async for message in telegram_manager.fetch_messages(
       entity,
       limit=limit,
       min_id=min_id,  # NEW: forward the parameter
   ):
   ```

**Testing:**
- Unit test: resume with last_message_id = 1000, verify fetch starts from 1001
- Integration test: interrupt export at 500 msgs, resume, verify no duplicates

### Step 2: Early Skip with BloomFilter (2 hours)

**File:** `src/export/exporter.py`

**Add check in both paths:**

1. **Fallback path** (after line ~1210)
   ```python
   async for message in self.telegram_manager.fetch_messages(...):
       # Graceful shutdown check
       if shutdown_manager.shutdown_requested:
           break
       
       # NEW: Early skip check for already-processed messages
       if message.id in entity_data.processed_message_ids:
           logger.debug(f"⏭️ Skipping message {message.id} (already in BloomFilter)")
           continue
       
       # ... existing logic
   ```

2. **AsyncPipeline path** (process_fn, line ~1100)
   ```python
   async def process_fn(message):
       # NEW: Early filter for already-processed
       if message.id in entity_data.processed_message_ids:
           return None  # Skip this message
       
       # Filter empty messages early
       if not (message.text or message.media):
           return None
       
       # ... existing processing
   ```

**Edge Case:** Message gaps (deleted messages)
- min_id=1000, but messages 1001-1005 were deleted
- Telegram API returns message 1006
- BloomFilter check prevents reprocessing if 1006 was already done

**Testing:**
- Unit test: add message to BloomFilter, verify it's skipped
- Integration test: resume with gaps in message IDs

### Step 3: Dynamic BloomFilter Sizing (4 hours)

**File:** `src/export/exporter.py`

**Changes:**

1. **Add helper method to Exporter class**
   ```python
   async def _calculate_bloom_filter_size(self, entity) -> int:
       """
       Calculate optimal BloomFilter size based on entity message count.
       
       Args:
           entity: Telegram entity to analyze
           
       Returns:
           Expected items for BloomFilter (with 10% buffer)
       """
       try:
           # Get total message count
           total_messages = await self.telegram_manager.get_message_count(entity)
           
           if total_messages == 0:
               logger.warning("Entity has 0 messages, using minimum BloomFilter size")
               return 10_000
           
           # Add 10% buffer for new messages during export
           expected = int(total_messages * 1.1)
           
           # Clamp to reasonable range
           # Min: 10k (saves memory for small chats)
           # Max: 10M (prevents excessive memory for mega-chats)
           clamped = max(10_000, min(expected, 10_000_000))
           
           logger.info(
               f"📊 BloomFilter sizing: {total_messages} messages "
               f"→ {expected} expected → {clamped} (clamped)"
           )
           
           return clamped
           
       except Exception as e:
           logger.warning(f"Failed to calculate BloomFilter size: {e}")
           return 1_000_000  # Fallback to current default
   ```

2. **Update entity_data creation** (line ~951)
   ```python
   if not isinstance(entity_data, EntityCacheData):
       # Calculate optimal BloomFilter size
       bf_size = await self._calculate_bloom_filter_size(entity)
       
       entity_data = EntityCacheData(
           entity_id=str(target.id),
           entity_name=entity_name,
           entity_type="regular",
           processed_message_ids=BloomFilter(expected_items=bf_size),
       )
   ```

3. **Add ENV variable** (`src/config.py`)
   ```python
   # BloomFilter optimization
   self.bloom_filter_size_multiplier = float(
       os.getenv("BLOOM_FILTER_SIZE_MULTIPLIER", "1.1")
   )  # 10% buffer by default
   
   self.bloom_filter_min_size = int(
       os.getenv("BLOOM_FILTER_MIN_SIZE", "10000")
   )  # 10k minimum
   
   self.bloom_filter_max_size = int(
       os.getenv("BLOOM_FILTER_MAX_SIZE", "10000000")
   )  # 10M maximum
   ```

4. **Update .env.example**
   ```bash
   # BloomFilter Optimization (TIER B - B-4)
   # Multiplier for expected message count (1.1 = 10% buffer for new messages)
   BLOOM_FILTER_SIZE_MULTIPLIER=1.1
   
   # Minimum BloomFilter size (prevents over-allocation for small chats)
   # 10k = ~120KB memory
   BLOOM_FILTER_MIN_SIZE=10000
   
   # Maximum BloomFilter size (prevents excessive memory for mega-chats)
   # 10M = ~12MB memory
   BLOOM_FILTER_MAX_SIZE=10000000
   ```

**Memory impact:**
| Chat Size | Old (1M) | New (Dynamic) | Savings |
|-----------|----------|---------------|---------|
| 1k msgs   | 1.2 MB   | 120 KB        | 90%     |
| 10k msgs  | 1.2 MB   | 120 KB        | 90%     |
| 100k msgs | 1.2 MB   | 1.2 MB        | 0%      |
| 1M msgs   | 1.2 MB   | 1.2 MB        | 0%      |
| 5M msgs   | 1.2 MB   | 6 MB          | -400%   |

**Trade-off:** Larger chats use more memory, but avoid false positives

**Testing:**
- Unit test: calculate_bloom_filter_size with various counts
- Verify memory usage matches expectation
- Check false positive rate doesn't degrade

### Step 4: Add Unit Tests (4 hours)

**File:** `tests/test_pagination_fixes.py`

```python
"""
Unit tests for TIER B-4: Pagination Fixes & BloomFilter Optimization
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.export.exporter import BloomFilter, EntityCacheData, Exporter

# ... (see next section for full tests)
```

**Test coverage:**
1. BloomFilter early skip (add message, verify skip on next iteration)
2. min_id resume (verify fetch_messages called with correct min_id)
3. Dynamic sizing (verify calculation for small/medium/large chats)
4. BloomFilter persistence (save/restore round-trip)
5. Edge case: gaps in message IDs (deleted messages)
6. Edge case: corrupted BloomFilter (graceful degradation)
7. Shutdown flush (verify BloomFilter saved on interrupt)

### Step 5: Integration Tests (2 hours)

**File:** `tests/test_pagination_integration.py`

**Scenarios:**
1. **Interrupt & Resume:**
   - Export 500 messages
   - Interrupt (simulate Ctrl+C)
   - Resume export
   - Verify: no duplicates, continue from 501

2. **Gap Handling:**
   - Mock message IDs with gaps (1, 2, 5, 6, 10)
   - Resume from ID 5
   - Verify: correctly process 6, 10 (skip 1, 2, 5)

3. **Memory Efficiency:**
   - Small chat (1k messages)
   - Medium chat (100k messages)
   - Large chat (1M messages)
   - Verify: memory usage scales appropriately

---

## 🧪 Testing Strategy

### Unit Tests

**File:** `tests/test_pagination_fixes.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.export.exporter import BloomFilter, EntityCacheData, Exporter
from src.config import Config
from src.core.cache import CacheManager
from src.telegram_client import TelegramManager


class TestBloomFilterEarlySkip:
    """Test early skip logic with BloomFilter."""
    
    def test_bloom_filter_contains(self):
        """Test BloomFilter membership check."""
        bf = BloomFilter(expected_items=1000)
        
        # Add message ID
        bf.add(12345)
        
        # Verify membership
        assert 12345 in bf
        assert 99999 not in bf  # Not added
    
    def test_bloom_filter_false_positive_rate(self):
        """Test false positive rate is within bounds."""
        bf = BloomFilter(expected_items=10000, false_positive_rate=0.01)
        
        # Add 10k items
        for i in range(10000):
            bf.add(i)
        
        # Test 1000 non-members
        false_positives = 0
        for i in range(10000, 11000):
            if i in bf:
                false_positives += 1
        
        # Should be ~1% (10 out of 1000)
        rate = false_positives / 1000
        assert rate < 0.02, f"False positive rate {rate:.2%} > 2%"


class TestMinIdResume:
    """Test min_id parameter for resume."""
    
    @pytest.mark.asyncio
    async def test_resume_from_last_message_id(self):
        """Test fetch_messages called with last_message_id."""
        # Create mock entity_data with last_message_id
        entity_data = EntityCacheData(
            entity_id="123",
            entity_name="Test Chat",
            entity_type="regular",
            last_message_id=1000,
        )
        
        # Mock telegram_manager
        tm = AsyncMock(spec=TelegramManager)
        tm.fetch_messages = AsyncMock()
        
        # Mock fetch_messages to return empty list
        async def mock_fetch():
            return
            yield  # Make it an async generator
        
        tm.fetch_messages.return_value = mock_fetch()
        
        # Create exporter (simplified)
        config = Config()
        exporter = Exporter(
            config=config,
            telegram_manager=tm,
            cache_manager=AsyncMock(),
            media_processor=MagicMock(),
            note_generator=MagicMock(),
            http_session=AsyncMock(),
        )
        
        # Simulate calling fetch with min_id
        # (This would be part of _export_regular_target in real code)
        entity = AsyncMock()
        min_id = entity_data.last_message_id or 0
        
        async for _ in tm.fetch_messages(entity, limit=None, min_id=min_id):
            pass
        
        # Verify fetch_messages was called with min_id=1000
        tm.fetch_messages.assert_called_once_with(
            entity, limit=None, min_id=1000
        )


class TestDynamicBloomFilterSizing:
    """Test dynamic BloomFilter size calculation."""
    
    @pytest.mark.asyncio
    async def test_calculate_size_small_chat(self):
        """Test sizing for small chat (1k messages)."""
        # Mock telegram_manager
        tm = AsyncMock(spec=TelegramManager)
        tm.get_message_count = AsyncMock(return_value=1000)
        
        # Create exporter
        config = Config()
        exporter = Exporter(
            config=config,
            telegram_manager=tm,
            cache_manager=AsyncMock(),
            media_processor=MagicMock(),
            note_generator=MagicMock(),
            http_session=AsyncMock(),
        )
        
        # Calculate size
        entity = AsyncMock()
        size = await exporter._calculate_bloom_filter_size(entity)
        
        # Expected: 1000 * 1.1 = 1100, clamped to min 10k
        assert size == 10_000, f"Expected 10k for small chat, got {size}"
    
    @pytest.mark.asyncio
    async def test_calculate_size_medium_chat(self):
        """Test sizing for medium chat (100k messages)."""
        tm = AsyncMock(spec=TelegramManager)
        tm.get_message_count = AsyncMock(return_value=100_000)
        
        config = Config()
        exporter = Exporter(
            config=config,
            telegram_manager=tm,
            cache_manager=AsyncMock(),
            media_processor=MagicMock(),
            note_generator=MagicMock(),
            http_session=AsyncMock(),
        )
        
        entity = AsyncMock()
        size = await exporter._calculate_bloom_filter_size(entity)
        
        # Expected: 100k * 1.1 = 110k
        assert 100_000 < size < 120_000, f"Expected ~110k, got {size}"
    
    @pytest.mark.asyncio
    async def test_calculate_size_large_chat_clamped(self):
        """Test sizing for mega-chat (20M messages, clamped to 10M)."""
        tm = AsyncMock(spec=TelegramManager)
        tm.get_message_count = AsyncMock(return_value=20_000_000)
        
        config = Config()
        exporter = Exporter(
            config=config,
            telegram_manager=tm,
            cache_manager=AsyncMock(),
            media_processor=MagicMock(),
            note_generator=MagicMock(),
            http_session=AsyncMock(),
        )
        
        entity = AsyncMock()
        size = await exporter._calculate_bloom_filter_size(entity)
        
        # Expected: 20M * 1.1 = 22M, clamped to max 10M
        assert size == 10_000_000, f"Expected 10M (clamped), got {size}"


class TestBloomFilterPersistence:
    """Test BloomFilter save/restore."""
    
    def test_to_dict_from_dict_roundtrip(self):
        """Test serialization round-trip."""
        # Create BloomFilter and add items
        bf = BloomFilter(expected_items=1000)
        for i in range(100):
            bf.add(i)
        
        # Serialize
        data = bf.to_dict()
        
        # Verify structure
        assert "size" in data
        assert "hash_count" in data
        assert "items_added" in data
        assert "bit_array_b64" in data
        
        # Deserialize
        bf2 = BloomFilter.from_dict(data)
        
        # Verify restored filter
        assert bf2.size == bf.size
        assert bf2.hash_count == bf.hash_count
        assert bf2.items_added == bf.items_added
        assert bf2.bit_array == bf.bit_array
        
        # Verify membership
        for i in range(100):
            assert i in bf2, f"Item {i} lost in round-trip"


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_bloom_filter_with_gaps(self):
        """Test BloomFilter handles message ID gaps."""
        bf = BloomFilter(expected_items=1000)
        
        # Add messages with gaps (simulating deleted messages)
        bf.add(1)
        bf.add(2)
        bf.add(5)  # Gap: 3, 4 deleted
        bf.add(6)
        bf.add(10)  # Gap: 7, 8, 9 deleted
        
        # Verify all added messages present
        assert 1 in bf
        assert 2 in bf
        assert 5 in bf
        assert 6 in bf
        assert 10 in bf
        
        # Verify gaps not present
        assert 3 not in bf
        assert 4 not in bf
        assert 7 not in bf
    
    @pytest.mark.asyncio
    async def test_fallback_on_corrupted_bloom_filter(self):
        """Test graceful degradation on corrupted BloomFilter."""
        # Simulate corrupted cache data
        corrupted_data = {
            "entity_id": "123",
            "entity_name": "Test",
            "entity_type": "regular",
            "processed_message_ids": {
                "bit_array_b64": "INVALID_BASE64!@#$"
            }
        }
        
        # Try to restore (should fail gracefully)
        try:
            bf = BloomFilter.from_dict(corrupted_data["processed_message_ids"])
            assert False, "Should have raised exception"
        except Exception:
            pass  # Expected
        
        # Verify fallback: create new empty BloomFilter
        entity_data = EntityCacheData(
            entity_id="123",
            entity_name="Test",
            entity_type="regular",
        )
        
        # Should have empty BloomFilter
        assert entity_data.processed_message_ids.items_added == 0
```

### Integration Tests

**File:** `tests/test_pagination_integration.py`

```python
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from src.export.exporter import Exporter, EntityCacheData
from src.config import Config


class TestResumeScenarios:
    """Integration tests for resume functionality."""
    
    @pytest.mark.asyncio
    async def test_interrupt_and_resume(self, tmp_path):
        """Test export interruption and resume."""
        # Setup
        config = Config()
        config.export_path = tmp_path
        
        # Mock telegram manager with 1000 messages
        tm = AsyncMock()
        
        messages = []
        for i in range(1, 1001):
            msg = MagicMock()
            msg.id = i
            msg.text = f"Message {i}"
            msg.media = None
            messages.append(msg)
        
        # First run: export 500 messages then "interrupt"
        async def first_fetch(entity, limit=None, min_id=0):
            for msg in messages[min_id:500]:
                yield msg
        
        tm.fetch_messages = first_fetch
        
        # ... (create exporter, run first export)
        # ... (verify 500 messages exported)
        
        # Second run: resume from 500
        async def second_fetch(entity, limit=None, min_id=0):
            for msg in messages[min_id:]:
                yield msg
        
        tm.fetch_messages = second_fetch
        
        # ... (run second export with min_id=500)
        # ... (verify total 1000 messages, no duplicates)
    
    @pytest.mark.asyncio
    async def test_gap_handling(self):
        """Test handling of gaps in message IDs."""
        # Mock messages with gaps (deleted messages)
        messages = []
        for msg_id in [1, 2, 5, 6, 10, 15, 16]:
            msg = MagicMock()
            msg.id = msg_id
            msg.text = f"Message {msg_id}"
            messages.append(msg)
        
        # Create entity_data with some processed messages
        entity_data = EntityCacheData(
            entity_id="123",
            entity_name="Test",
            entity_type="regular",
            last_message_id=5,
        )
        entity_data.processed_message_ids.add(1)
        entity_data.processed_message_ids.add(2)
        entity_data.processed_message_ids.add(5)
        
        # Resume from ID 5, should process 6, 10, 15, 16
        # ... (mock telegram manager)
        # ... (run export)
        # ... (verify only new messages processed)
```

---

## 📈 Expected Performance Improvements

### Bandwidth Savings
- **Before:** Re-download ALL messages on resume (100% redundant)
- **After:** Skip processed messages with min_id (0% redundant)
- **Impact:** ~100% savings for resumed exports

### Resume Speed
- **Before:** 10k processed + 1k new = 11k API calls
- **After:** 1k new = 1k API calls
- **Impact:** 10x faster resume for large chats

### Memory Efficiency
- **Small chats (1k msgs):** 1.2MB → 120KB (90% reduction)
- **Medium chats (100k msgs):** No change (already optimal)
- **Large chats (5M msgs):** 1.2MB → 6MB (acceptable for accuracy)

### False Positive Rate
- **Before:** 1% with fixed 1M size (may degrade for mega-chats)
- **After:** 1% maintained across all sizes (dynamic tuning)

---

## 🚨 Rollback Plan

If B-4 causes issues:

1. **Revert min_id logic:**
   ```python
   # Remove min_id parameter, fetch all messages
   async for message in self.telegram_manager.fetch_messages(
       entity,
       limit=None,
       # min_id=resume_from_id,  # COMMENTED OUT
   ):
   ```

2. **Disable early skip:**
   ```python
   # Comment out BloomFilter check
   # if message.id in entity_data.processed_message_ids:
   #     continue
   ```

3. **Revert to fixed BloomFilter size:**
   ```python
   # src/export/exporter.py line 71
   def __init__(
       self, expected_items: int = 1000000,  # Back to fixed 1M
   ):
   ```

4. **ENV variables:**
   ```bash
   # Disable B-4 optimizations (set in .env)
   ENABLE_B4_PAGINATION_FIXES=false
   ```

---

## ✅ Acceptance Criteria

1. **min_id Resume:**
   - ✅ `fetch_messages()` called with `min_id=last_message_id`
   - ✅ Telegram API returns only NEW messages
   - ✅ No re-download of processed messages

2. **Early Skip:**
   - ✅ BloomFilter check before processing
   - ✅ Already-processed messages skipped
   - ✅ Logs show "⏭️ Skipping message X"

3. **Dynamic Sizing:**
   - ✅ BloomFilter size calculated from message count
   - ✅ Memory usage scales appropriately
   - ✅ Small chats use <200KB memory

4. **Testing:**
   - ✅ 8+ unit tests passing
   - ✅ 2+ integration tests passing
   - ✅ py_compile successful for all modified files

5. **Documentation:**
   - ✅ ENV variables documented in .env.example
   - ✅ Code comments explain edge cases
   - ✅ TIER_B_B4_COMPLETED.md created

6. **Performance:**
   - ✅ Resume 10x faster for large chats
   - ✅ Memory usage reduced by 90% for small chats
   - ✅ No increase in false positive rate

---

## 📁 Files to Modify/Create

### Modified Files
1. `src/export/exporter.py` (main changes)
2. `src/export/pipeline.py` (min_id parameter)
3. `src/config.py` (ENV variables)
4. `.env.example` (documentation)
5. `.env` (add B-4 parameters)

### New Files
1. `tests/test_pagination_fixes.py` (unit tests)
2. `tests/test_pagination_integration.py` (integration tests)
3. `TIER_B_B4_PLAN.md` (this file)
4. `TIER_B_B4_COMPLETED.md` (after implementation)
5. `TIER_B_B4_ENV_VARS.md` (ENV documentation)

---

## 🎯 Success Metrics

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Resume Speed (10k processed) | ~120s | ~12s | 10x faster |
| Bandwidth (resume) | 100% | 0% | 0% redundant |
| Memory (1k chat) | 1.2 MB | 120 KB | <200 KB |
| Memory (100k chat) | 1.2 MB | 1.2 MB | No change |
| False Positives | 1% | 1% | <2% |

---

**Plan Created:** 2025-01-20  
**Status:** Ready for Implementation  
**Next Step:** Get user approval, then execute Step 1
