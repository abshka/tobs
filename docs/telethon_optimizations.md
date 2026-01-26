# Telethon-based optimizations for TOBS

TL;DR

- Этот документ перечисляет оптимизации, которые можно реализовать на базе возможностей Telethon и существующей архитектуры TOBS.
- Фокус: уменьшение I/O, экономия памяти, увеличение throughput (msg/s), улучшение стабильности при шардировании и загрузке медиа.
- Каждый пункт содержит: краткое описание, почему это поможет, trade-offs, где менять код (файлы и строки), и как тестировать/оценивать влияние.

Goals

- Максимизировать пропускную способность (сообщений в секунду).
- Уменьшить peak memory при экспорте больших чатов.
- Снизить лишние syscalls и сетевой трафик (меньше дублирующих загрузок).
- Минимизировать вероятность блокировок/pauses (FloodWait / DC migration).

---

## High-priority optimizations (High impact, low/medium risk)

1. Батчевый fetch сообщений в `TelegramManager` (iter_messages → get_messages)

- Где: `tobs/src/telegram_client.py#L320-370`
- Что: заменить per-message `iter_messages` на пакетный генератор, использующий `get_messages(limit=100)`, и yield сообщений из батчей.
- Почему: уменьшение Python-level await/overhead на каждое сообщение → увеличение throughput.
- Trade-offs: нужно корректно обрабатывать offset_id/min_id/reverse, и настроить batch_size (default 100 = Telethon limit).
- Как тестировать: unit test для fetch_messages на малых/больших чатах; benchmark: msg/sec и requests/msg.

2. Дедупликация медиа на уровне file-id

- Где: `tobs/src/media/downloader.py#L292-356` и `tobs/src/media/manager.py`
- Что: перед скачиванием проверять, был ли уже скачан тот же файл по `message.media.document.id` или по `photo.id` и `access_hash` — хранить map `file_key -> local_path`.
- Почему: избежать повторных скачиваний медиа (репосты/пересылки), уменьшить сетевой трафик и загрузки диска.
- Trade-offs: нужно выбрать уникальный и надежный `file_key`. Плюс — кэшировать результаты `cache_manager` для re-use при перезапуске.
- Как тестировать: unit-test для MediaDownloader: 2 сообщения с одним документом возвращают тот же path и не дублируют скачивание.

3. Сжатие/оптимизация сериализации сообщений в шардировании (IO overhead)

- Где: `tobs/src/telegram_sharded_client.py#L420-640` (`_fetch_chunk`) и `tobs/src/telegram_sharded_client.py#L860-980` (merge)
- Что: использовать `zlib` или `orjson`+msgpack compression для снижения размера chunk'ов; опция: сереализация только минимальной структуры сообщений (минимальная metadata) вместо полных `Message` объектов.
- Почему: уменьшение IO при записи/чтении временных файлов → менее затратный merge → меньше времени на экспорт.
- Trade-offs: CPU overhead для компрессии; в некоторых контекстах перестроение `Message` на merge потребует дополнительных API-запросов (re-fetch), если сохранить только meta.
- Как тестировать: сравнить bytes written, merge time и total time с/без сжатия.

---

## Medium-priority optimizations (Moderate impact)

4. Пересмотр timeout/await модели и `wait_time` в `iter_messages`

- Где: `tobs/src/telegram_client.py#L320-370`
- Что: заменить per-message `asyncio.wait_for` на batch-level timeouts (timeout for get_messages) — убираем лишние per-message await overhead.
- Почему: уменьшение overhead на исключения и таймауты, упрощение логики retries.
- Trade-offs: нужно аккуратно протестировать edge-cases: slow network, single message stall.

5. Сжатие данных/сериализация shard → master: lightweight schema

- Где: `tobs/src/telegram_sharded_client.py#L420-640` — pickle -> minimal schema
- Что: в worker сохранять сжатую минимальную структуру сообщений (id, sender_id, date, text, media_meta), а master при необходимости rehydrate сообщения.
- Почему: маленький payload на диск, меньше IO.
- Trade-offs: master не сможет вызывать `download_media` напрямую на тех объектах; возможны re-fetches для media download. Это подход для распределения: worker скачивает и записывает/обрабатывает медиа, мастер только пишет текст.

6. Autotuning `part_size_kb` and download concurrency

- Где: `tobs/src/media/downloader.py#L440` (download_file) and `class MediaDownloader`
- Что: динамически выбирать `part_size_kb` based on file size & connection; expose as config/env var.
- Почему: оптимальная chunk size снижает request overhead or improves throughput (varies per environment).
- Trade-offs: requires benchmarking across NVMe/NAS/HDD and network conditions.

7. Metadata caching and `GetFullChannelRequest` for message counts

- Где: `tobs/src/telegram_client.py#L970-989` (get topic message count) and other places that read `total` counts.
- Что: use `GetFullChannelRequest` and `client.get_entity` for stable metadata (if available), and fallback to `get_messages(limit=0)`.
- Почему: sometimes `get_messages(limit=0)` is ambiguous; full channel info may return official totals.

---

## Low-priority optimizations (Lower immediate impact; potential edge benefits)

8. Persist BloomFilter options vs exact IDs

- Где: `tobs/src/export/exporter.py#L100-180` (EntityCacheData)
- Что: make storing of the BloomFilter optionally persistent (store bitarray compressed to disk), OR provide an exact 'processed IDs' LRU for safe resume.
- Trade-offs: BloomFilter false positives might hide messages on resume. Decide by use-case (safety vs speed).

9. Reduce logging overhead / add log rate-limits

- Где: `tobs/src/export/exporter.py#L468-520` `_lazy_log` — batched logging
- Что: make `log_batch_interval` and thresholds config-driven; add heuristics to reduce logging in high-throughput runs.
- Trade-offs: less actionable logs during debug runs; keep telemetry for monitoring.

10. DC-aware worker assignment and pre-warming

- Где: `tobs/src/telegram_sharded_client.py` (DC detection with `self._extract_dc_id`) and worker setup
- Что: route chunk tasks to the worker with the closest DC, and pre-connect worker clients in a warm pool to avoid initiation latency.
- Trade-offs: requires mapping sessions to datacenter info; complexity grows slightly.

---

## Testing & Benchmarks (How to measure effect)

- Add a small benchmarking harness (in `tests/benchmarks/`):
    - Target chat sizes: 1k, 10k, 50k, 100k messages.
    - Metric: total time, msg/sec, peak memory, total network bytes read, number of API requests
    - For media: also MB/sec, average file download time, and disk io per file
- Unit tests for each optimization:
    - `fetch_messages` equivalence (chunk-based vs iter) using a mock Telethon client.
    - Media dedupe test: 2 messages referencing same document return same local path.
    - Sharded serialization round-trip (worker writes, merge reads) verify no data loss and integrity.

---

## Implementation checklist for PRs

- [ ] Add config options for any new tuning variables (PREFETCH_BATCH_SIZE, PART_SIZE_KB, COMPRESSION_ENABLED, etc.) in `.env.example` and config struct.
- [ ] Implement changes incrementally: unit-tests, then integration+benchmark.
- [ ] Run benchmark suite vs baseline and record metrics.
- [ ] Update performance guide `PERFORMANCE_GUIDE.md` with recommended defaults.
- [ ] Add basic metrics/monitoring for new features (e.g., dedupe cache hits/misses, compressed chunks saved bytes).

---

## Next Steps (recommended first two items)

- Implement `get_messages` batch-based generator in `TelegramManager.fetch_messages` and add tests.
- Implement `MediaDownloader` dedupe by `file_key` and persist cache into `cache_manager`.

_Notes: Line references are approximate — please verify during implementation; they've been added to help navigation._

## Benchmarks and performance verification

- Baseline: run export for small/medium/large test chats, save logs and metrics (time, throughput, memory, IO, API requests).
- Measure: Before and After for every optimization; use benchmarking harness in `tests/benchmarks/`.
- Baseline scripts (conceptual):
    - `python scripts/benchmark_export.py --target test_chat_10k --mode baseline` (captures logs and metrics)
    - Rerun with optimizations toggled ON and produce comparison CSV.

## Definition of Done (DoD) for each PR

- Unit tests covering core logic.
- Integration test that asserts no message loss on a 1k-message chat.
- Benchmark results show measurable improvement (or neutral) for the given prioritized metric.
- Configuration knobs added to `.env.example`.
- Documentation updated: `PERFORMANCE_GUIDE.md` and this doc (telethon_optimizations.md).

## PR Review & Testing checklist

- [ ] Linting and py_compile passes
- [ ] Unit tests for core logic added/updated
- [ ] Benchmark script outputs included in PR description (baseline vs new)
- [ ] Potential fallbacks for reliability (e.g., return to iter_messages when something fails)

## Step-by-step Implementation Plan (top items)

### 1) Batch fetch messages (TelegramManager)

1. Add a config option for `BATCH_FETCH_SIZE` (default 100).
2. Write helper `get_messages_batch_generator(entity, min_id, batch_size)` in `TelegramManager`.
3. Replace `iter_messages` usage in `fetch_messages` with a generator built on `get_messages`.
4. Add unit tests/mocks to validate semantics: no duplicates, correct ordering, correct stops at `min_id`.
5. Run benchmark, tune `BATCH_FETCH_SIZE` and `request_delay` options.
6. Document in report.

### 2) Media dedupe in downloader

1. Add `downloaded_cache` dict (in-memory per-run + persist via cache_manager).
2. Create stable `file_key` by prioritizing `document.id + access_hash`, fallback to `photo.id`, fallback to `message.file.name + file.size`.
3. Before starting download, check `downloaded_cache`, return existing path if match.
4. After successful download, persist to `cache_manager` with `key = file_key`.
5. Add tests for dedupe logic, including cross-run persistence test (cache manager stubbed).

### 3) Compress shard chunk data

1. Add config flag `SHARD_COMPRESSION_ENABLED` and compression level.
2. In `_fetch_chunk`: `zlib.compress(pickle.dumps(...), level=config.compression_level)` while writing.
3. In `fetch_messages` merging: `pickle.loads(zlib.decompress(body))`.
4. Add new unit test: create synthetic chunk, compress, merge, validate messages round-trip.
5. Add benchmark to measure disk IO/merge times with/without compression.

## Feature: Export Reactions (implementation plan)

Goal

- Экспортировать количество реакций (summary) для каждого сообщения и опционально — список пользователей, поставивших реакцию.

Telethon resources / types (reference)

- `Message.reactions` (type `types.MessageReactions`) — summary (counts, emoji types) may be present on `Message`.
- `types.MessagePeerReaction` / `types.MessageReactions` — to inspect individual reactors where available.
- Optional API: `messages.getMessageReactionList` (if available via Telethon functions) — to fetch detailed list of reactors for a message.

Implementation steps

1. Add config flags: `EXPORT_REACTIONS` (default False), `EXPORT_REACTORS_FULL` (default False).
2. Minimal (cheap) path: Read `message.reactions` directly during `_process_message_parallel` and add a line in the message export (eg. `Reactions: 👍2 ❤️1`).

- File: `tobs/src/export/exporter.py#L660-724` (where processing occurs)

3. Detailed (optional): For `EXPORT_REACTORS_FULL`, fetch per-message reactor list via API if needed:

- Use `functions.messages.GetMessageReactionListRequest` (if present) or Telethon helper to fetch reactors.
- This is API-expensive; implement caching (LRU, per-entity) and run in parallel with `Semaphore`.

4. Cache/metrics: record `reactions` counts in `EntityReporter` (metrics), also add counters for `reactions_api_calls`, `reactions_cache_hits`.
5. Sharding: if `EXPORT_REACTORS_FULL` enabled in sharded mode, run reactors fetch on workers (along with messages) to avoid extra master step.
6. Output format: add reaction summary inline in message text; optionally create `reactions.csv` or JSON side-file for structured analysis.

Trade-offs and notes

- Basic summary is cheap (only reading the field on `Message` if Telethon returned it); detailed user lists require additional API calls and must be optional.
- For privacy-conscious exports, allow `EXPORT_REACTORS_REDIRECT` to export user-id only or user-opaque fields (avoid personal data).

---

## Feature: Forum improvements & Topic export (implementation plan)

Goal

- Экспортировать форумы и топики как отдельные файлы/папки; включить метаданные топика, сообщения топика, и опционально — статистику/участников.

Telethon resources / types (reference)

- `functions.messages.GetForumTopicsRequest` — Telethon wrapper для получения списка топиков форума.
- `GetHistoryRequest` / `client.get_messages(entity, reply_to=topic_id)` — для получения сообщений конкретного топика (используется также `reply_to` в Telethon).
- `types.ForumTopic` — содержит id, title, date, pinned/closed flags.

Implementation steps

1. Add `ExportTarget` type for forum topic selection or reuse existing `forum_topic` option (already exists in exporter flow).
2. Implement `_export_forum_topic()` in `Exporter`:
    - Create folder `export_path/<forum_name>/topics/` and file `topic_<topic_id>.md` per topic or `topic_<safe_title>.md`.
    - Use `TelegramManager.get_forum_topics(entity)` to list topics (already in code).
    - Use generator: `telegram_manager.fetch_messages(entity, limit=None, min_id=None, reply_to=topic_id)` or `client.get_messages(entity, reply_to=topic_id)` to fetch messages for topic; process them via `_process_message_parallel`.
    - Include topic metadata at top of file (title, creator, date, pinned/closed, message_count).
3. Permissions/Visibility: ensure `GetForumTopicsRequest` is wrapped in `connection_manager.execute_with_retry` (similar to other calls) and add fallback if forum is private.
4. Pagination and very large topics: support pagination via `page/offset` (lazy) or sharding per topic if extremely large.
5. Indexing & metadata: create `index.md` for forum that lists topics with message counts and export file path.
6. Optional: For each topic, provide a `topic_metrics.json` file with aggregated stats (views, reactions, poll results in the topic).

Sharding and concurrency

- For large topics, re-use shard manager logic: spawn workers to fetch topic ID range using `_worker_task` and chunking, then merge results as usual. This allows consistent tooling for both channel-level and topic-level exports.

How to integrate into exporter

- The current `_export_forum()` in `tobs/src/export/exporter.py#L1168-1180` is the correct entry point; replace the `pass` with iterating through `all_topics` and call `_export_forum_topic` per topic.

Tests

- Unit test for `get_forum_topics()` returns topics list (mock `GetForumTopicsRequest`).
- Integration test for `_export_forum()` exports per-topic files and counts (simulate a small forum in tests or with mocked client).
