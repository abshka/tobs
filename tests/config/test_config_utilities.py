"""
Batch 7: Tests for Config utility functions.

This module tests the standalone utility functions in src/config.py:
- _parse_bool(): Boolean value parsing from strings
- get_optimal_workers(): Worker count optimization based on system resources
- validate_proxy_config(): Proxy configuration validation
"""

import pytest

from src.config import _parse_bool, get_optimal_workers, validate_proxy_config
from src.exceptions import ConfigError


class TestParseBool:
    """Test _parse_bool utility function."""

    def test_parse_bool_none_with_default_false(self):
        """None should return the default value (False)."""
        assert _parse_bool(None, default=False) is False

    def test_parse_bool_none_with_default_true(self):
        """None should return the default value (True)."""
        assert _parse_bool(None, default=True) is True

    def test_parse_bool_true_value(self):
        """Boolean True should be returned as-is."""
        assert _parse_bool(True) is True
        assert _parse_bool(True, default=False) is True

    def test_parse_bool_false_value(self):
        """Boolean False should be returned as-is."""
        assert _parse_bool(False) is False
        assert _parse_bool(False, default=True) is False

    def test_parse_bool_string_true_lowercase(self):
        """String 'true' should return True."""
        assert _parse_bool("true") is True

    def test_parse_bool_string_true_uppercase(self):
        """String 'TRUE' should return True."""
        assert _parse_bool("TRUE") is True

    def test_parse_bool_string_true_mixedcase(self):
        """String 'TrUe' should return True."""
        assert _parse_bool("TrUe") is True

    def test_parse_bool_string_one(self):
        """String '1' should return True."""
        assert _parse_bool("1") is True

    def test_parse_bool_string_yes(self):
        """String 'yes' (case-insensitive) should return True."""
        assert _parse_bool("yes") is True
        assert _parse_bool("YES") is True
        assert _parse_bool("Yes") is True

    def test_parse_bool_string_y(self):
        """String 'y' (case-insensitive) should return True."""
        assert _parse_bool("y") is True
        assert _parse_bool("Y") is True

    def test_parse_bool_string_on(self):
        """String 'on' (case-insensitive) should return True."""
        assert _parse_bool("on") is True
        assert _parse_bool("ON") is True
        assert _parse_bool("On") is True

    def test_parse_bool_string_false(self):
        """String 'false' should return False (not in true list)."""
        assert _parse_bool("false") is False

    def test_parse_bool_string_zero(self):
        """String '0' should return False (not in true list)."""
        assert _parse_bool("0") is False

    def test_parse_bool_string_no(self):
        """String 'no' should return False (not in true list)."""
        assert _parse_bool("no") is False

    def test_parse_bool_string_off(self):
        """String 'off' should return False (not in true list)."""
        assert _parse_bool("off") is False

    def test_parse_bool_empty_string(self):
        """Empty string should return False (not in true list)."""
        assert _parse_bool("") is False

    def test_parse_bool_random_string(self):
        """Random string should return False (not in true list)."""
        assert _parse_bool("random") is False
        assert _parse_bool("maybe") is False

    def test_parse_bool_invalid_string_ignores_default(self):
        """Invalid strings return False, not the default value."""
        # This is important behavior: default only applies to None
        assert _parse_bool("false", default=True) is False
        assert _parse_bool("invalid", default=True) is False


class TestGetOptimalWorkers:
    """Test get_optimal_workers utility function."""

    def test_get_optimal_workers_balanced_default(self):
        """Balanced profile (default) with typical resources."""
        result = get_optimal_workers(memory_gb=8.0, cpu_count=4, profile="balanced")

        # multiplier = 1.0 for balanced
        # base_workers = min(4 * 1.0, 8.0 * 2) = min(4, 16) = 4
        expected_base = 4

        assert result["workers"] == max(2, expected_base)  # 4
        assert result["download_workers"] == max(
            4, int(expected_base * 1.5)
        )  # max(4, 6) = 6
        assert result["io_workers"] == max(4, int(expected_base * 2))  # max(4, 8) = 8
        assert result["ffmpeg_workers"] == max(1, expected_base // 2)  # max(1, 2) = 2

    def test_get_optimal_workers_conservative(self):
        """Conservative profile with typical resources."""
        result = get_optimal_workers(memory_gb=8.0, cpu_count=4, profile="conservative")

        # multiplier = 0.5 for conservative
        # base_workers = min(4 * 0.5, 8.0 * 2) = min(2, 16) = 2
        expected_base = 2

        assert result["workers"] == max(2, expected_base)  # 2
        assert result["download_workers"] == max(
            4, int(expected_base * 1.5)
        )  # max(4, 3) = 4
        assert result["io_workers"] == max(4, int(expected_base * 2))  # max(4, 4) = 4
        assert result["ffmpeg_workers"] == max(1, expected_base // 2)  # max(1, 1) = 1

    def test_get_optimal_workers_aggressive(self):
        """Aggressive profile with typical resources."""
        result = get_optimal_workers(memory_gb=8.0, cpu_count=4, profile="aggressive")

        # multiplier = 2.0 for aggressive
        # base_workers = min(4 * 2.0, 8.0 * 2) = min(8, 16) = 8
        expected_base = 8

        assert result["workers"] == max(2, expected_base)  # 8
        assert result["download_workers"] == max(
            4, int(expected_base * 1.5)
        )  # max(4, 12) = 12
        assert result["io_workers"] == max(4, int(expected_base * 2))  # max(4, 16) = 16
        assert result["ffmpeg_workers"] == max(1, expected_base // 2)  # max(1, 4) = 4

    def test_get_optimal_workers_low_cpu_low_memory(self):
        """Low resources should respect minimums."""
        result = get_optimal_workers(memory_gb=1.0, cpu_count=1, profile="balanced")

        # multiplier = 1.0 for balanced
        # base_workers = min(1 * 1.0, 1.0 * 2) = min(1, 2) = 1

        # All values should be clamped to minimums
        assert result["workers"] == 2  # max(2, 1) = 2
        assert result["download_workers"] == 4  # max(4, 1) = 4
        assert result["io_workers"] == 4  # max(4, 2) = 4
        assert result["ffmpeg_workers"] == 1  # max(1, 0) = 1

    def test_get_optimal_workers_high_cpu_low_memory(self):
        """High CPU, low memory - memory becomes bottleneck."""
        result = get_optimal_workers(memory_gb=2.0, cpu_count=16, profile="balanced")

        # multiplier = 1.0 for balanced
        # base_workers = min(16 * 1.0, 2.0 * 2) = min(16, 4) = 4
        expected_base = 4

        assert result["workers"] == 4
        assert result["download_workers"] == max(4, int(expected_base * 1.5))  # 6
        assert result["io_workers"] == max(4, int(expected_base * 2))  # 8
        assert result["ffmpeg_workers"] == max(1, expected_base // 2)  # 2

    def test_get_optimal_workers_low_cpu_high_memory(self):
        """Low CPU, high memory - CPU becomes bottleneck."""
        result = get_optimal_workers(memory_gb=64.0, cpu_count=2, profile="balanced")

        # multiplier = 1.0 for balanced
        # base_workers = min(2 * 1.0, 64.0 * 2) = min(2, 128) = 2
        expected_base = 2

        assert result["workers"] == max(2, expected_base)  # 2
        assert result["download_workers"] == max(
            4, int(expected_base * 1.5)
        )  # max(4, 3) = 4
        assert result["io_workers"] == max(4, int(expected_base * 2))  # max(4, 4) = 4
        assert result["ffmpeg_workers"] == max(1, expected_base // 2)  # max(1, 1) = 1

    def test_get_optimal_workers_aggressive_high_resources(self):
        """Aggressive profile with high resources."""
        result = get_optimal_workers(memory_gb=32.0, cpu_count=16, profile="aggressive")

        # multiplier = 2.0 for aggressive
        # base_workers = min(16 * 2.0, 32.0 * 2) = min(32, 64) = 32
        expected_base = 32

        assert result["workers"] == 32
        assert result["download_workers"] == int(expected_base * 1.5)  # 48
        assert result["io_workers"] == int(expected_base * 2)  # 64
        assert result["ffmpeg_workers"] == expected_base // 2  # 16

    def test_get_optimal_workers_conservative_high_resources(self):
        """Conservative profile with high resources."""
        result = get_optimal_workers(
            memory_gb=32.0, cpu_count=16, profile="conservative"
        )

        # multiplier = 0.5 for conservative
        # base_workers = min(16 * 0.5, 32.0 * 2) = min(8, 64) = 8
        expected_base = 8

        assert result["workers"] == 8
        assert result["download_workers"] == int(expected_base * 1.5)  # 12
        assert result["io_workers"] == int(expected_base * 2)  # 16
        assert result["ffmpeg_workers"] == expected_base // 2  # 4

    def test_get_optimal_workers_returns_all_keys(self):
        """Result should contain all expected worker types."""
        result = get_optimal_workers(memory_gb=8.0, cpu_count=4)

        assert "workers" in result
        assert "download_workers" in result
        assert "io_workers" in result
        assert "ffmpeg_workers" in result
        assert len(result) == 4

    def test_get_optimal_workers_all_values_positive(self):
        """All worker counts should be positive integers."""
        result = get_optimal_workers(memory_gb=0.5, cpu_count=1, profile="conservative")

        assert result["workers"] >= 1
        assert result["download_workers"] >= 1
        assert result["io_workers"] >= 1
        assert result["ffmpeg_workers"] >= 1


class TestValidateProxyConfig:
    """Test validate_proxy_config utility function."""

    def test_validate_proxy_no_proxy(self):
        """No proxy type means no proxy - should return True."""
        assert validate_proxy_config(None, None, None) is True
        assert validate_proxy_config("", None, None) is True

    def test_validate_proxy_socks4_valid(self):
        """Valid socks4 proxy configuration."""
        assert validate_proxy_config("socks4", "127.0.0.1", 1080) is True

    def test_validate_proxy_socks5_valid(self):
        """Valid socks5 proxy configuration."""
        assert validate_proxy_config("socks5", "proxy.example.com", 9050) is True

    def test_validate_proxy_http_valid(self):
        """Valid http proxy configuration."""
        assert validate_proxy_config("http", "192.168.1.1", 8080) is True

    def test_validate_proxy_unsupported_type(self):
        """Unsupported proxy type should raise ConfigError."""
        with pytest.raises(ConfigError, match="Unsupported proxy type: https"):
            validate_proxy_config("https", "proxy.example.com", 8080)

    def test_validate_proxy_invalid_type(self):
        """Invalid proxy type should raise ConfigError."""
        with pytest.raises(ConfigError, match="Unsupported proxy type: invalid"):
            validate_proxy_config("invalid", "proxy.example.com", 8080)

    def test_validate_proxy_missing_address(self):
        """Missing proxy address should raise ConfigError."""
        with pytest.raises(ConfigError, match="Proxy address is required"):
            validate_proxy_config("socks5", None, 1080)

    def test_validate_proxy_empty_address(self):
        """Empty proxy address should raise ConfigError."""
        with pytest.raises(ConfigError, match="Proxy address is required"):
            validate_proxy_config("socks5", "", 1080)

    def test_validate_proxy_missing_port(self):
        """Missing proxy port should raise ConfigError."""
        with pytest.raises(ConfigError, match="Invalid proxy port"):
            validate_proxy_config("socks5", "proxy.example.com", None)

    def test_validate_proxy_port_zero(self):
        """Port 0 is invalid and should raise ConfigError."""
        with pytest.raises(ConfigError, match="Invalid proxy port: 0"):
            validate_proxy_config("socks5", "proxy.example.com", 0)

    def test_validate_proxy_port_negative(self):
        """Negative port should raise ConfigError."""
        with pytest.raises(ConfigError, match="Invalid proxy port: -1"):
            validate_proxy_config("socks5", "proxy.example.com", -1)

    def test_validate_proxy_port_too_high(self):
        """Port > 65535 should raise ConfigError."""
        with pytest.raises(ConfigError, match="Invalid proxy port: 65536"):
            validate_proxy_config("socks5", "proxy.example.com", 65536)

    def test_validate_proxy_port_boundary_valid_low(self):
        """Port 1 (lower boundary) should be valid."""
        assert validate_proxy_config("socks5", "proxy.example.com", 1) is True

    def test_validate_proxy_port_boundary_valid_high(self):
        """Port 65535 (upper boundary) should be valid."""
        assert validate_proxy_config("socks5", "proxy.example.com", 65535) is True

    def test_validate_proxy_all_types_valid(self):
        """All supported proxy types should be accepted."""
        for proxy_type in ["socks4", "socks5", "http"]:
            assert validate_proxy_config(proxy_type, "proxy.example.com", 8080) is True
