# tests for DC-aware routing configuration parsing (src/config.py)
#
# These tests ensure `Config.from_env` correctly parses DC-related environment
# variables and exposes them on the Config object. They also verify that an
# invalid numeric value raises `ConfigError`.
#
# Note: Config.from_env enforces API credentials (API_ID / API_HASH) so the helper
# sets minimal valid values for those environment variables to avoid accidental
# validation failures unrelated to DC settings.

import pytest

from src.config import Config
from src.exceptions import ConfigError

ENV_PATH_NO_FILE = ".env.test_missing_nonexistent_please_ignore"


def clear_dc_env(monkeypatch):
    """Clear DC-related environment variables and set minimal API credentials."""
    keys = [
        "DC_AWARE_ROUTING_ENABLED",
        "DC_ROUTING_STRATEGY",
        "DC_PREWARM_ENABLED",
        "DC_PREWARM_TIMEOUT",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)

    # Provide minimal valid API credentials required for Config validation
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "a" * 32)  # must be at least 32 chars per validation


def test_dc_routing_defaults(monkeypatch):
    """When no DC env vars are set, Config should expose sensible defaults."""
    clear_dc_env(monkeypatch)

    cfg = Config.from_env(env_path=ENV_PATH_NO_FILE)

    assert hasattr(cfg, "dc_aware_routing_enabled")
    assert cfg.dc_aware_routing_enabled is False

    assert hasattr(cfg, "dc_routing_strategy")
    assert cfg.dc_routing_strategy == "smart"

    assert hasattr(cfg, "dc_prewarm_enabled")
    assert cfg.dc_prewarm_enabled is True

    assert hasattr(cfg, "dc_prewarm_timeout")
    assert cfg.dc_prewarm_timeout == 5


def test_dc_routing_env_overrides(monkeypatch):
    """Explicit DC-related env vars should be parsed and override defaults."""
    clear_dc_env(monkeypatch)

    monkeypatch.setenv("DC_AWARE_ROUTING_ENABLED", "true")
    monkeypatch.setenv("DC_ROUTING_STRATEGY", "sticky")
    monkeypatch.setenv("DC_PREWARM_ENABLED", "false")
    monkeypatch.setenv("DC_PREWARM_TIMEOUT", "10")

    cfg = Config.from_env(env_path=ENV_PATH_NO_FILE)

    assert cfg.dc_aware_routing_enabled is True
    assert cfg.dc_routing_strategy == "sticky"
    assert cfg.dc_prewarm_enabled is False
    assert cfg.dc_prewarm_timeout == 10


def test_invalid_prewarm_timeout_raises_configerror(monkeypatch):
    """Non-integer DC_PREWARM_TIMEOUT should surface as a ConfigError."""
    clear_dc_env(monkeypatch)

    monkeypatch.setenv("DC_PREWARM_TIMEOUT", "not-an-int")

    with pytest.raises(ConfigError):
        Config.from_env(env_path=ENV_PATH_NO_FILE)
