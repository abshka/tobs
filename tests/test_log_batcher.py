# tests for the LogBatcher utility
# These tests describe the expected behavior:
#  - INFO/DEBUG/WARNING messages are batched and flushed on demand
#  - ERROR/CRITICAL messages are logged immediately, not batched
#  - Batches use the "(×N)" suffix only when N > 1
#
# NOTE: These tests use the public API:
#   LogBatcher(logger: logging.Logger | None = None, flush_interval: float = 2.0)
#   Methods:
#     - lazy_log(level: str, message: str) -> None
#     - flush() -> None  # force synchronous flush
#
# The tests intentionally call .flush() explicitly to keep them deterministic.

import logging

import pytest
from src.logging.log_batcher import LogBatcher  # class under test


def test_info_messages_batch_and_flush(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("tobs.tests.logbatcher.info")
    lb = LogBatcher(logger=logger)

    # Emit the same INFO message multiple times
    lb.lazy_log("INFO", "hello world")
    lb.lazy_log("INFO", "hello world")
    lb.lazy_log("INFO", "hello world")

    # Explicit flush to force emission
    lb.flush()

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert info_records, "Expected at least one INFO log record after flush"
    # Expect aggregated message with multiplicity suffix
    assert any("hello world (×3)" in r.getMessage() for r in info_records)


def test_error_and_critical_logged_immediately(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("tobs.tests.logbatcher.errcrit")
    lb = LogBatcher(logger=logger)

    # These should be logged immediately and not require flush
    lb.lazy_log("ERROR", "urgent failure")
    lb.lazy_log("CRITICAL", "system down")

    # No flush called
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    crit_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]

    assert any("urgent failure" in r.getMessage() for r in error_records)
    assert any("system down" in r.getMessage() for r in crit_records)


def test_levels_separate_batches(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("tobs.tests.logbatcher.levels")
    lb = LogBatcher(logger=logger)

    # INFO: two occurrences
    lb.lazy_log("INFO", "same")
    lb.lazy_log("INFO", "same")

    # DEBUG: three occurrences
    lb.lazy_log("DEBUG", "same")
    lb.lazy_log("DEBUG", "same")
    lb.lazy_log("DEBUG", "same")

    lb.flush()

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]

    assert any("same (×2)" in r.getMessage() for r in info_records)
    assert any("same (×3)" in r.getMessage() for r in debug_records)


def test_single_message_no_suffix(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("tobs.tests.logbatcher.single")
    lb = LogBatcher(logger=logger)

    lb.lazy_log("INFO", "single")
    lb.flush()

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any(r.getMessage() == "single" for r in info_records), (
        "Single occurrence should be logged without multiplicity suffix"
    )


def test_flush_clears_previous_counts(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("tobs.tests.logbatcher.clear")
    lb = LogBatcher(logger=logger)

    # First flush with single occurrence
    lb.lazy_log("INFO", "round")
    lb.flush()
    assert any(
        r.getMessage() == "round" for r in caplog.records if r.levelno == logging.INFO
    )

    # Second phase: two occurrences -> expect multiplicity suffix
    lb.lazy_log("INFO", "round")
    lb.lazy_log("INFO", "round")
    lb.flush()
    assert any(
        "round (×2)" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO
    )
