# TOBS Architecture Analysis

## Executive Summary

**TOBS** (Telegram Exporter to Markdown) is a high-performance, enterprise-grade tool for exporting Telegram conversations to Markdown format. The architecture is modular, asynchronous, and designed for scalability with multiple optimization layers.

**Current Performance Baseline:**
- Throughput: ~200 msg/s (target: 300+ msg/s)
- CPU Utilization: ~40% (target: 70% in aggressive mode)
- Cache Hit Rate: Variable (target: >80%)

---

## 1. Core Architecture Components

### 1.1 Entry Point (`main.py`)

**Responsibilities:**
- Application initialization and lifecycle management
- Configuration loading from `.env`
- Core system initialization (CacheManager, ConnectionManager, PerformanceMonitor)
- Telegram client setup (standard or sharded)
- Takeout session management
- Export orchestration

**Key Flows:**
```
main() → async_main() → CoreSystemManager.initialize()
                      → TelegramManager.connect()
                      → run_interactive_configuration()
                      → run_export()
```

**Dependencies:**
- `CoreSystemManager`: Manages core systems lifecycle
- `TelegramManager` / `ShardedTelegramManager`: Telegram API client
- `Config`: Configuration management
- `run_export`: Export orchestration

---

### 1.2 Configuration System (`src/config.py`)

**Architecture:**
- Dataclass-based configuration with environment variable support
- Performance profiles: `conservative`, `balanced`, `aggressive`, `custom`
- Auto-configuration based on system resources (CPU, memory)
- Export target management with path resolution

**Key Classes:**
- `Config`: Main configuration container
- `PerformanceSettings`: Performance tuning parameters
- `ExportTarget`: Export target definition (channel, chat, user, forum topic)
- `TranscriptionConfig`: Audio transcription settings

**Configuration Hierarchy:**
1. Environment variables (`.env` file)
2. Default values in dataclass fields
3. Auto-configuration based on system resources
4. Runtime overrides

---

### 1.3 Core Systems (`src/core/`)

#### 1.3.1 CacheManager (`src/core/cache.py`)

**Architecture:**
- Multi-strategy caching: `SIMPLE`, `LRU`, `TTL`
- Compression support: `GZIP`, `PICKLE`, `NONE`
- Persistent storage with async I/O
- Background auto-save with TaskGroup management

**Key Features:**
- LRU eviction for memory management
- TTL-based expiration
- Compression threshold (default: 1KB)
- Async lock-based thread safety
- Statistics tracking (hits, misses, evictions)

**Storage:**
- In-memory: `OrderedDict` (LRU) or `Dict` (simple/TTL)
- Persistent: JSON file with base64-encoded compressed data
- Backup mechanism: `.backup` file for atomic writes

#### 1.3.2 ConnectionManager (`src/core/connection.py`)

**Architecture:**
- Connection pooling for HTTP/Telegram API
- Pool types: `HTTP`, `TELEGRAM`, `MIXED`
- Backoff strategies: `EXPONENTIAL`, `LINEAR`, `FIXED`
- Health monitoring and automatic recovery

**Key Features:**
- Connection reuse across requests
- Automatic retry with backoff
- Health checks and connection validation
- Metrics collection (connection count, pool utilization)

#### 1.3.3 PerformanceMonitor (`src/core/performance.py`)

**Architecture:**
- Real-time resource monitoring (CPU, memory, I/O)
- Alert system with configurable thresholds
- Performance profiling decorators
- Metrics aggregation and reporting

**Key Features:**
- Process-level metrics (CPU %, memory MB)
- System-level metrics (disk I/O, network)
- Alert levels: `INFO`, `WARNING`, `CRITICAL`
- Resource state classification: `normal`, `high`, `overloaded`

---

### 1.4 Telegram Client Layer

#### 1.4.1 TelegramManager (`src/telegram_client.py`)

**Responsibilities:**
- Standard Telegram API client wrapper
- Message fetching with batch optimization
- Entity resolution (channels, chats, users)
- Forum topic support

**Key Methods:**
- `fetch_messages()`: Batch message fetching (replaces `iter_messages`)
- `get_topic_messages_stream()`: Forum topic message fetching
- `resolve_entity()`: Entity ID resolution

**Optimizations:**
- Batch fetching (default: 100 messages per request)
- FloodWaitError handling with exponential backoff
- Connection pooling integration

#### 1.4.2 ShardedTelegramManager (`src/telegram_sharded_client.py`)

**Architecture:**
- Extends `TelegramManager` with parallel worker support
- Takeout session management for high-speed exports
- Worker client pool for parallel message fetching
- DC-aware routing (planned)

**Key Features:**
- Multiple worker clients (default: 8)
- Takeout session reuse across workers
- Lightweight message schema for reduced memory
- Compression for inter-worker communication (zlib, level 1)

**Worker Management:**
- Session cloning for worker isolation
- Takeout ID propagation to all workers
- Worker statistics tracking
- Graceful shutdown and cleanup

**Sharding Strategy:**
- ID range partitioning (e.g., 0-1000, 1000-2000, ...)
- Adaptive chunking based on message density
- Hot zone detection for high-density ranges
- Slow chunk splitting for performance optimization

---

### 1.5 Export System (`src/export/`)

#### 1.5.1 Exporter (`src/export/exporter.py`)

**Architecture:**
- Main export orchestration
- Message processing and Markdown generation
- Media handling coordination
- Progress tracking and reporting

**Key Components:**
- `BloomFilter`: Efficient message deduplication
- `TakeoutSessionWrapper`: Takeout session lifecycle management
- `AsyncBufferedSaver`: Buffered file writing (512KB default)
- `Exporter`: Main export class

**Export Flow:**
```
1. Entity resolution
2. Message fetching (batch or sharded)
3. Message processing (formatting, media extraction)
4. Markdown generation
5. File writing (buffered)
6. Media download (async queue)
7. Statistics collection
```

#### 1.5.2 AsyncPipeline (`src/export/pipeline.py`)

**Architecture:**
- 3-stage async pipeline: `fetch → process → write`
- Bounded queues for backpressure
- Message ordering preservation
- Worker-based parallelism

**Pipeline Stages:**
1. **Fetch Stage**: Message fetching from Telegram API
   - Workers: 1 (default, configurable)
   - Queue size: 64 (default)
   
2. **Process Stage**: Message processing (formatting, media extraction)
   - Workers: Auto (derived from performance settings)
   - Queue size: 256 (default)
   
3. **Write Stage**: Markdown file writing
   - Workers: 1 (preserves ordering)
   - Sequential write with buffering

**Features:**
- Sequence number assignment for ordering
- Failure handling (skip failed messages, continue)
- Statistics collection (processed count, errors, queue metrics)
- Graceful shutdown with sentinel messages

**Status:** Partially implemented, requires optimization and integration improvements

---

### 1.6 Media Processing System (`src/media/`)

#### 1.6.1 MediaProcessor (`src/media/manager.py`)

**Architecture:**
- Modular component composition
- Thread pool executors for I/O and CPU-bound tasks
- Hardware acceleration detection (VA-API)
- Background download queue

**Key Components:**
- `MediaDownloader`: File downloading with deduplication
- `MediaDownloadQueue`: Async background download queue
- `VideoProcessor`, `AudioProcessor`, `ImageProcessor`: Type-specific processors
- `WhisperTranscriber`: Audio transcription (optional)
- `MediaCache`: Media file caching
- `MetadataExtractor`: Media metadata extraction

**Processing Flow:**
```
1. Media detection in message
2. Cache check (deduplication)
3. Download (if not cached)
4. Processing (transcoding, optimization)
5. Storage in entity media directory
6. Metadata extraction
```

**Thread Pools:**
- `io_executor`: I/O operations (default: max_workers)
- `cpu_executor`: CPU-bound tasks (default: max_workers // 2)
- `ffmpeg_executor`: FFmpeg operations (default: max_workers // 2)

#### 1.6.2 MediaDownloader (`src/media/downloader.py`)

**Architecture:**
- Download with retry logic
- Part size autotuning (128KB small, 512KB large files)
- Connection pooling integration
- Worker client support for parallel downloads

**Key Features:**
- File deduplication (doc_id/photo_id based)
- Persistent cache integration
- Adaptive timeout based on file size
- Persistent download mode (never give up)

**Optimizations:**
- Part size autotuning (10-20% improvement)
- Media deduplication (40-80% bandwidth savings)
- Connection reuse via ConnectionManager

---

### 1.7 Core System Manager (`src/core_manager.py`)

**Architecture:**
- Singleton pattern for core systems
- Lifecycle management (initialize, shutdown)
- Performance profile management
- System health monitoring

**Managed Systems:**
- `CacheManager`: Caching system
- `ConnectionManager`: Connection pooling
- `PerformanceMonitor`: Resource monitoring

**Initialization Flow:**
```
CoreSystemManager.initialize()
  → CacheManager.start()
  → ConnectionManager.start()
  → PerformanceMonitor.start()
  → Background tasks started
```

---

## 2. Data Flow Architecture

### 2.1 Standard Export Flow

```
User Input (Config)
    ↓
CoreSystemManager.initialize()
    ↓
TelegramManager.connect()
    ↓
Exporter.export_entity()
    ↓
TelegramManager.fetch_messages() [Batch: 100 msgs]
    ↓
Exporter.process_message()
    ↓
NoteGenerator.generate_markdown()
    ↓
AsyncBufferedSaver.write() [Buffer: 512KB]
    ↓
MediaProcessor.download_and_process_media() [Async Queue]
    ↓
Statistics Collection
```

### 2.2 Sharded Export Flow

```
User Input (Config with sharding enabled)
    ↓
ShardedTelegramManager.setup_workers()
    ↓
Takeout Session Initialization
    ↓
ID Range Partitioning (e.g., 8 workers × 4 chunks = 32 chunks)
    ↓
Parallel Worker Execution
    ├─ Worker 1: IDs 0-1000
    ├─ Worker 2: IDs 1000-2000
    └─ ...
    ↓
Message Fetching (Takeout API, 1000 msgs/chunk)
    ↓
Compression (zlib, level 1) [50-80% IO reduction]
    ↓
Master Process Aggregation
    ↓
Message Processing & Writing
    ↓
Statistics Aggregation
```

### 2.3 Async Pipeline Flow (When Enabled)

```
Entity Export Request
    ↓
AsyncPipeline.run()
    ↓
[Stage 1: Fetch] → fetch_queue (size: 64)
    ↓
[Stage 2: Process] → process_queue (size: 256)
    ↓
[Stage 3: Write] → Sequential write (ordered)
    ↓
Statistics Return
```

---

## 3. Optimization Layers

### 3.1 Network Layer
- **Batch Message Fetching**: 30-50% improvement
- **Connection Pooling**: Reuse connections, reduce overhead
- **Takeout API**: High-speed export mode
- **Media Deduplication**: 40-80% bandwidth savings

### 3.2 Memory Layer
- **BloomFilter**: Efficient message tracking (~1.2MB for 1M items)
- **LRU Cache**: Memory-efficient caching with eviction
- **Lightweight Shard Schema**: 20-30% memory reduction
- **Buffered Writing**: 512KB buffer reduces syscalls

### 3.3 I/O Layer
- **Shard Compression**: 50-80% IO reduction (zlib, level 1)
- **Async Media Downloads**: Non-blocking background queue
- **Part Size Autotuning**: 10-20% download improvement
- **Metadata Caching**: 5-15% improvement

### 3.4 Processing Layer
- **Thread Pool Executors**: Parallel I/O and CPU tasks
- **Hardware Acceleration**: VA-API for video encoding
- **Worker-based Parallelism**: Sharded fetching, async pipeline
- **Deferred Processing**: Process media after export

---

## 4. Component Interactions

### 4.1 Export → Telegram Client
- `Exporter` calls `TelegramManager.fetch_messages()`
- Batch size controlled by `config.batch_fetch_size`
- Sharding controlled by `config.enable_shard_fetch`

### 4.2 Export → Media Processor
- `Exporter` calls `MediaProcessor.download_and_process_media()`
- Async queue for non-blocking downloads
- Cache integration for deduplication

### 4.3 Telegram Client → Connection Manager
- `TelegramManager` uses `ConnectionManager` for connection pooling
- Connection reuse across requests
- Health monitoring and retry logic

### 4.4 Media Processor → Cache Manager
- `MediaDownloader` checks `CacheManager` for file paths
- Cache keys: `media_file_{doc_id}_{access_hash}`
- Persistent cache across sessions

### 4.5 All Components → Performance Monitor
- Decorators: `@profile_async`, `@profile_sync`
- Resource monitoring (CPU, memory)
- Alert generation on thresholds

---

## 5. Configuration Architecture

### 5.1 Performance Profiles

**Conservative:**
- Workers: min(4, CPU)
- Memory: 20% available RAM
- Batch sizes: Reduced
- Timeouts: Increased

**Balanced (Default):**
- Workers: min(8, CPU)
- Memory: 40% available RAM
- Batch sizes: Standard
- Timeouts: Standard

**Aggressive:**
- Workers: min(16, CPU × 2)
- Memory: 60% available RAM
- Batch sizes: Increased
- Timeouts: Extended

### 5.2 Feature Flags

- `async_pipeline_enabled`: Enable async pipeline (default: False)
- `dc_aware_routing_enabled`: DC-aware worker routing (default: False)
- `enable_shard_fetch`: Enable sharded fetching (default: False)
- `async_media_download`: Background media downloads (default: True)
- `deferred_processing`: Process media after export (default: True)

---

## 6. Error Handling Architecture

### 6.1 Retry Strategies
- **Exponential Backoff**: For transient errors
- **Fixed Delay**: For rate limiting (FloodWaitError)
- **Persistent Mode**: Never give up for large files

### 6.2 Error Categories
- **Transient**: Network errors, timeouts → Retry
- **Rate Limiting**: FloodWaitError → Wait and retry
- **Permanent**: Invalid entity, access denied → Skip and log

### 6.3 Failure Recovery
- **Message-level**: Skip failed messages, continue export
- **Batch-level**: Retry entire batch on failure
- **Worker-level**: Restart worker on critical error
- **Session-level**: Reconnect on connection loss

---

## 7. Monitoring and Observability

### 7.1 Metrics Collection
- **Performance**: CPU %, memory MB, throughput (msg/s)
- **Cache**: Hit rate, evictions, size
- **Network**: Connection count, pool utilization
- **Export**: Messages processed, errors, duration

### 7.2 Logging
- **Structured Logging**: Via `loguru`
- **Context**: Worker ID, entity ID, message ID
- **Levels**: DEBUG, INFO, WARNING, ERROR

### 7.3 Progress Tracking
- **Rich Progress Bars**: Real-time export progress
- **Statistics**: Messages, media, errors, duration
- **Resource Usage**: CPU, memory, disk I/O

---

## 8. Scalability Considerations

### 8.1 Horizontal Scaling
- **Sharded Fetching**: Parallel workers for message fetching
- **Worker Clients**: Multiple Telegram clients for parallel operations
- **Async Pipeline**: Parallel processing stages

### 8.2 Vertical Scaling
- **Performance Profiles**: Auto-configuration based on resources
- **Thread Pools**: Configurable worker counts
- **Memory Management**: LRU eviction, BloomFilter efficiency

### 8.3 Resource Limits
- **Memory**: Configurable limits with eviction
- **CPU**: Thread pool limits prevent over-subscription
- **Disk**: File size limits, total size limits
- **Network**: Connection pool limits, rate limiting

---

## 9. Security Architecture

### 9.1 Session Management
- **Session Files**: Encrypted Telegram session storage
- **Session Cloning**: For worker isolation
- **Takeout Sessions**: Secure high-speed export

### 9.2 Data Privacy
- **Local Storage**: All data stored locally
- **No External Services**: No data sent to third parties
- **Cache Encryption**: Optional (not currently implemented)

---

## 10. Testing Architecture

### 10.1 Test Structure
- **Unit Tests**: Component-level testing
- **Integration Tests**: System-level testing
- **Benchmarks**: Performance testing

### 10.2 Test Coverage
- **Core Systems**: Cache, Connection, Performance
- **Export System**: Message processing, Markdown generation
- **Media Processing**: Download, processing, caching

---

## 11. Known Architecture Limitations

### 11.1 Current Bottlenecks
1. **Sequential Processing**: Default export is sequential (async pipeline disabled by default)
2. **DC-Aware Routing**: Not implemented (planned P1)
3. **Connection Pool**: Fixed size, not adaptive
4. **Logging Overhead**: No rate limiting (planned P2)

### 11.2 Scalability Limits
1. **Memory**: BloomFilter grows with message count
2. **Disk I/O**: Multiple small writes (mitigated by buffering)
3. **Network**: Telegram API rate limits
4. **CPU**: Single-threaded event loop (mitigated by thread pools)

---

## 12. Future Architecture Improvements

### 12.1 Planned Optimizations (P1)
- **DC-Aware Worker Assignment**: Route tasks to optimal DC
- **Async Pipeline Optimization**: Tune queue sizes, worker counts
- **Connection Pool Adaptation**: Dynamic pool sizing

### 12.2 Planned Optimizations (P2)
- **Logging Rate-Limiting**: Reduce CPU overhead
- **Memory Optimization**: Streaming processing for large exports
- **Enhanced Deduplication**: File hash-based deduplication

---

## Conclusion

TOBS has a well-architected, modular design with clear separation of concerns. The system is designed for performance with multiple optimization layers. Key strengths include:

- **Modularity**: Clear component boundaries
- **Asynchrony**: Extensive use of async/await
- **Optimization**: Multiple implemented optimizations
- **Scalability**: Support for parallel processing

Areas for improvement:
- **Async Pipeline**: Needs optimization and better integration
- **DC-Aware Routing**: Critical for multi-DC performance
- **Resource Management**: More adaptive tuning needed
- **Monitoring**: Enhanced metrics for optimization decisions
