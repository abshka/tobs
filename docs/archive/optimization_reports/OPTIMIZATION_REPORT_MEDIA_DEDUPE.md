# Optimization Report: Media Deduplication

## Overview
We have implemented a media deduplication system to prevent redundant downloads of the same file (e.g., forwarded messages, identical attachments). This addresses the second high-priority optimization from `docs/telethon_optimizations.md`.

## Rationale
Previously, every message with media triggered a new download, even if that exact file had already been downloaded (e.g., in a forwarded message chain or duplicate post). This wasted bandwidth, disk space, and time.

## Implemented Changes

### 1. `MediaDownloader` (`src/media/downloader.py`)
-   **File Key Generation:** Implemented `_get_file_key(message)` which generates a unique signature:
    -   Documents: `doc_{id}_{access_hash}`
    -   Photos: `photo_{id}_{access_hash}`
-   **Cache Check:** Before downloading, the downloader now checks:
    1.  **In-Memory Cache:** `self._downloaded_cache` (fastest, per-session).
    2.  **Persistent Cache:** `self.cache_manager.get_file_path(key)` (across sessions).
-   **Cache Storage:** After a successful download, the path is stored in both caches.

### 2. `MediaProcessor` (`src/media/manager.py`)
-   Updated initialization to pass the `cache_manager` instance to the `MediaDownloader`.

### 3. `CacheManager` (`src/core/cache.py`)
-   Added specific async methods `get_file_path` and `store_file_path` to handle the `media_file_` prefixed keys.

## Impact Analysis
-   **Bandwidth:** significantly reduced for channels with frequent forwards or reposts.
-   **Storage:** duplicates are eliminated, saving disk space.
-   **Speed:** duplicate files are "downloaded" instantly (0s).

## Verification
-   **Logic Check:** Confirmed that `file_key` is unique and stable.
-   **Async Flow:** Confirmed that async cache methods are correctly awaited.
-   **Fallback:** If cache lookup fails or file is missing, standard download proceeds.
