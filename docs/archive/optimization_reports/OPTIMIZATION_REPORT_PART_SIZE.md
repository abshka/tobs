# Optimization Report: Autotuning `part_size_kb` for Media Downloads

## Overview
We have implemented the "Autotuning `part_size_kb`" optimization, which dynamically adjusts the part size used for downloading files from Telegram. This addresses a Medium-priority optimization (item 6) from `docs/telethon_optimizations.md`.

## Rationale
The `part_size_kb` parameter in Telethon's `download_file` method determines the size of chunks in which a file is downloaded. Choosing an optimal `part_size_kb` can significantly impact download speed. A fixed size might be suboptimal for all file sizes and network conditions. Dynamic adjustment allows for better performance by using smaller parts for small files (reducing latency) and larger parts for big files (reducing request overhead).

## Implemented Changes

### 1. Configuration (`src/config.py`)
-   Added `part_size_kb` to the `PerformanceSettings` dataclass (default: 512 KB). A value of `0` or `None` signifies auto-tuning.
-   Modified `Config.__post_init__` to allow `part_size_kb` to be overridden by the `PART_SIZE_KB` environment variable, ensuring user-defined values take precedence over auto-tuned ones.

### 2. `MediaDownloader` (`src/media/downloader.py`)
-   **Config Injection:** Updated `MediaDownloader.__init__` to accept the global `config` object, making performance settings accessible.
-   **`_get_part_size` Method:**
    -   A new helper method `_get_part_size(file_size: int)` was added.
    -   It first checks if a `part_size_kb` is explicitly configured (and non-zero); if so, it uses that value.
    -   If not configured, it dynamically calculates an optimal `part_size_kb` based on the `file_size`:
        -   `< 10MB`: 128 KB
        -   `< 100MB`: 256 KB
        -   `>= 100MB`: 512 KB
-   **Integration:**
    -   Updated `_persistent_download` to use `self._get_part_size(expected_size)` when calling `download_client.download_file`.
    -   Updated `_standard_download` to also use `self._get_part_size(expected_size)` when attempting `download_client.download_file`. It maintains the fallback to `download_client.download_media` if `download_file` fails (as `download_media` does not accept `part_size_kb`).

## Impact Analysis
-   **Download Speed:** Expected to improve download speeds by optimizing chunk sizes for various file sizes, reducing API call overhead or improving responsiveness.
-   **Flexibility:** Users can still override the auto-tuning with a fixed `PART_SIZE_KB` if needed.
-   **Robustness:** Maintains the fallback to `download_media` for compatibility and resilience.

## Verification
-   **Logic Check:** The `_get_part_size` method correctly applies configured values or the dynamic heuristic.
-   **Integration:** `part_size_kb` is now used in both main download paths (`_persistent_download`, `_standard_download`).
