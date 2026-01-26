# tests for ASYNC_PIPELINE_* environment parsing in src/config.py
#
# These tests ensure that the Config.from_env method correctly parses
# ASYNC_PIPELINE_* environment variables and exposes them as attributes
# on the returned Config object. They also assert that invalid numeric
# values raise a ConfigError.
#
# Note: We pass a non-existent `env_path` to `Config.from_env()` to avoid
# accidental loading of a repository `.env` file during test runs.

import pytest

from src.config import Config
from src.exceptions import ConfigError

ENV_PATH_NO_FILE = ".env.test_missing_nonexistent_please_ignore"


def clear_async_pipeline_env(monkeypatch):
    """Helper to clear any ASYNC_PIPELINE_* env vars for a clean test state.

    Also sets minimal dummy API credentials required by Config.from_env() so
    tests do not fail on required-field validation.
    """
    keys = [
        "ASYNC_PIPELINE_ENABLED",
        "ASYNC_PIPELINE_FETCH_WORKERS",
        "ASYNC_PIPELINE_PROCESS_WORKERS",
        "ASYNC_PIPELINE_WRITE_WORKERS",
        "ASYNC_PIPELINE_FETCH_QUEUE_SIZE",
        "ASYNC_PIPELINE_PROCESS_QUEUE_SIZE",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)

    # Provide minimal valid API credentials for test runs
    monkeypatch.setenv("API_ID", "1")
    # API_HASH must be at least 32 characters (validation in config), use simple filler
    monkeypatch.setenv("API_HASH", "a" * 32)


def test_async_pipeline_defaults(monkeypatch):
    """When no env vars are set, Config should expose sensible defaults."""
    clear_async_pipeline_env(monkeypatch)

    cfg = Config.from_env(env_path=ENV_PATH_NO_FILE)

    assert hasattr(cfg, "async_pipeline_enabled")
    assert cfg.async_pipeline_enabled is False

    assert hasattr(cfg, "async_pipeline_fetch_workers")
    assert cfg.async_pipeline_fetch_workers == 1

    assert hasattr(cfg, "async_pipeline_process_workers")
    assert cfg.async_pipeline_process_workers == 0  # 0 means 'auto'

    assert hasattr(cfg, "async_pipeline_write_workers")
    assert cfg.async_pipeline_write_workers == 1

    assert hasattr(cfg, "async_pipeline_fetch_queue_size")
    assert cfg.async_pipeline_fetch_queue_size == 64

    assert hasattr(cfg, "async_pipeline_process_queue_size")
    assert cfg.async_pipeline_process_queue_size == 256


@pytest.mark.parametrize("true_value", ["1", "true", "True", "yes", "ON", "y"])
def test_async_pipeline_enabled_true_variants(monkeypatch, true_value):
    """Several boolean-like strings should be interpreted as True."""
    clear_async_pipeline_env(monkeypatch)
    monkeypatch.setenv("ASYNC_PIPELINE_ENABLED", true_value)

    cfg = Config.from_env(env_path=ENV_PATH_NO_FILE)
    assert cfg.async_pipeline_enabled is True


def test_async_pipeline_custom_values(monkeypatch):
    """Numeric and size env vars should be parsed and exposed correctly."""
    clear_async_pipeline_env(monkeypatch)

    monkeypatch.setenv("ASYNC_PIPELINE_ENABLED", "true")
    monkeypatch.setenv("ASYNC_PIPELINE_FETCH_WORKERS", "3")
    monkeypatch.setenv("ASYNC_PIPELINE_PROCESS_WORKERS", "4")
    monkeypatch.setenv("ASYNC_PIPELINE_WRITE_WORKERS", "2")
    monkeypatch.setenv("ASYNC_PIPELINE_FETCH_QUEUE_SIZE", "128")
    monkeypatch.setenv("ASYNC_PIPELINE_PROCESS_QUEUE_SIZE", "512")

    cfg = Config.from_env(env_path=ENV_PATH_NO_FILE)

    assert cfg.async_pipeline_enabled is True
    assert cfg.async_pipeline_fetch_workers == 3
    assert cfg.async_pipeline_process_workers == 4
    assert cfg.async_pipeline_write_workers == 2
    assert cfg.async_pipeline_fetch_queue_size == 128
    assert cfg.async_pipeline_process_queue_size == 512


def test_async_pipeline_invalid_numbers_raise_configerror(monkeypatch):
    """Invalid integer environment variables should raise a ConfigError."""
    clear_async_pipeline_env(monkeypatch)

    # Non-integer value for an integer field
    monkeypatch.setenv("ASYNC_PIPELINE_PROCESS_WORKERS", "not-an-int")

    with pytest.raises(ConfigError):
        Config.from_env(env_path=ENV_PATH_NO_FILE)
