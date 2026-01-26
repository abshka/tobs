# TIER B-6: Hash-Based Media Deduplication - Environment Variables

Add these variables to your `.env` and `.env.example` files:

```bash
# ===================================================================
# TIER B-6: Hash-Based Media Deduplication
# ===================================================================
# Content-based deduplication using file hashes (vs ID-based only).
# Enables reuse of identical files across different message IDs.
#
# Example: Same meme reposted 20 times → download once, reuse 19 times
# Bandwidth savings: 10-20% typical, up to 80-95% on heavy reposts
#
# Uses Telethon's upload.GetFileHashes API to compute SHA256 hashes
# before downloading. Gracefully falls back to ID-based on API failure.
# ===================================================================

# Enable hash-based deduplication (content matching)
# Default: true (recommended for bandwidth savings)
# Set to false to use ID-based deduplication only
HASH_BASED_DEDUPLICATION=true

# Maximum hash cache entries (LRU eviction when full)
# Default: 10000 (≈1MB memory, enough for typical usage)
# Increase for large archives with many unique files
HASH_CACHE_MAX_SIZE=10000

# Timeout for GetFileHashes API call (seconds)
# Default: 5.0 (balance between reliability and speed)
# Decrease if API is slow, increase for unstable connections
HASH_API_TIMEOUT=5.0
```

## Integration Instructions

### 1. Add to `.env.example`:
Copy the above block to your `.env.example` file after the B-3 (Parallel Media Processing) section.

### 2. Add to `.env`:
Copy the following to your `.env` file:

```bash
# B-6: Hash-Based Deduplication
HASH_BASED_DEDUPLICATION=true
HASH_CACHE_MAX_SIZE=10000
HASH_API_TIMEOUT=5.0
```

### 3. Verify Configuration:
```bash
# Check that config parsing works
python3 -c "from src.config import Config; c = Config(); print(f'Hash dedup: {c.performance.hash_based_deduplication}')"
```

Expected output: `Hash dedup: True`

## Usage Examples

### Scenario 1: Enable (Default)
```bash
# Use hash-based deduplication for maximum bandwidth savings
HASH_BASED_DEDUPLICATION=true
```

### Scenario 2: Disable (Fallback to ID-only)
```bash
# Disable if GetFileHashes API is unreliable
HASH_BASED_DEDUPLICATION=false
```

### Scenario 3: Large Archive
```bash
# Increase cache size for projects with 50k+ unique media files
HASH_CACHE_MAX_SIZE=50000
```

### Scenario 4: Slow API
```bash
# Decrease timeout to fail fast on slow Telegram API
HASH_API_TIMEOUT=2.0
```

## Performance Impact

### Expected Bandwidth Savings:
- **Light reposts:** 5-10% reduction
- **Medium reposts:** 10-20% reduction (target)
- **Heavy reposts:** 80-95% reduction (meme channels, viral content)

### API Overhead:
- `GetFileHashes` call: ~100-500ms per file
- Amortized: negligible (called once per unique file)
- Cache hit: 0ms (no API call required)

### Memory Footprint:
- Hash cache: ~10,000 entries × 100 bytes ≈ 1MB
- Negligible compared to existing cache managers

## Troubleshooting

### Issue: API timeouts
**Symptom:** Logs show "GetFileHashes timeout after 5.0s"
**Solution:** Increase `HASH_API_TIMEOUT=10.0` or disable with `HASH_BASED_DEDUPLICATION=false`

### Issue: No bandwidth savings
**Symptom:** Hash cache always misses
**Solution:** Verify `HASH_BASED_DEDUPLICATION=true` in `.env` and check logs for "Hash cache HIT"

### Issue: High API failure rate
**Symptom:** Many "Failed to get file hash" messages
**Solution:** Telegram API might be unstable, fallback to ID-based: `HASH_BASED_DEDUPLICATION=false`

## Rollback

To completely disable hash-based deduplication:
```bash
HASH_BASED_DEDUPLICATION=false
```

This reverts to ID-based deduplication only (existing, proven behavior).
