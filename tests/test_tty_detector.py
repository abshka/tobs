"""
Unit tests for TTY detection module (TIER B - B-5).
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

from src.ui.tty_detector import TTYDetector, TTYMode, initialize_tty_detector, is_tty


class TestTTYDetector:
    """Test TTYDetector class"""
    
    def test_force_tty_mode(self):
        """Test force-tty mode always returns True"""
        detector = TTYDetector(mode="force-tty")
        assert detector.is_tty() is True
        assert detector.get_mode_name() == "force-tty"
    
    def test_force_non_tty_mode(self):
        """Test force-non-tty mode always returns False"""
        detector = TTYDetector(mode="force-non-tty")
        assert detector.is_tty() is False
        assert detector.get_mode_name() == "force-non-tty"
    
    def test_invalid_mode_defaults_to_auto(self):
        """Test invalid mode falls back to auto"""
        detector = TTYDetector(mode="invalid-mode")
        assert detector.get_mode_name() == "auto"
    
    @patch('sys.stdout')
    def test_auto_detect_tty(self, mock_stdout):
        """Test auto-detection when stdout is TTY"""
        mock_stdout.isatty.return_value = True
        
        # Mock environment (no CI vars, valid TERM)
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=False):
            detector = TTYDetector(mode="auto")
            assert detector.is_tty() is True
    
    @patch('sys.stdout')
    def test_auto_detect_non_tty_stdout(self, mock_stdout):
        """Test auto-detection when stdout is not TTY"""
        mock_stdout.isatty.return_value = False
        
        detector = TTYDetector(mode="auto")
        assert detector.is_tty() is False
    
    @patch('sys.stdout')
    def test_auto_detect_ci_environment(self, mock_stdout):
        """Test auto-detection rejects CI environments"""
        mock_stdout.isatty.return_value = True
        
        # Mock CI environment variable
        with patch.dict(os.environ, {"CI": "true", "TERM": "xterm"}, clear=False):
            detector = TTYDetector(mode="auto")
            assert detector.is_tty() is False
    
    @patch('sys.stdout')
    def test_auto_detect_dumb_terminal(self, mock_stdout):
        """Test auto-detection rejects TERM=dumb"""
        mock_stdout.isatty.return_value = True
        
        with patch.dict(os.environ, {"TERM": "dumb"}, clear=False):
            detector = TTYDetector(mode="auto")
            assert detector.is_tty() is False
    
    @patch('sys.stdout')
    def test_auto_detect_no_term_variable(self, mock_stdout):
        """Test auto-detection rejects missing TERM"""
        mock_stdout.isatty.return_value = True
        
        with patch.dict(os.environ, {}, clear=True):
            detector = TTYDetector(mode="auto")
            # Should be False because TERM is not set
            assert detector.is_tty() is False
    
    def test_get_detection_info(self):
        """Test get_detection_info returns debug dict"""
        detector = TTYDetector(mode="force-tty")
        info = detector.get_detection_info()
        
        assert "mode" in info
        assert "is_tty" in info
        assert "stdout_isatty" in info
        assert "term" in info
        assert "ci_detected" in info
        
        assert info["mode"] == "force-tty"
        assert info["is_tty"] is True


class TestTTYDetectorGlobals:
    """Test global singleton functions"""
    
    def test_initialize_and_get(self):
        """Test initialize_tty_detector and get_tty_detector"""
        detector = initialize_tty_detector(mode="force-non-tty")
        
        # Should be able to get the same instance
        from src.ui.tty_detector import get_tty_detector
        retrieved = get_tty_detector()
        
        assert retrieved is detector
        assert retrieved.is_tty() is False
    
    def test_get_without_initialize_raises(self):
        """Test get_tty_detector raises if not initialized"""
        # Reset global state
        import src.ui.tty_detector as tty_module
        tty_module._global_detector = None
        
        from src.ui.tty_detector import get_tty_detector
        with pytest.raises(RuntimeError, match="not initialized"):
            get_tty_detector()
    
    @patch('sys.stdout')
    def test_is_tty_convenience_function(self, mock_stdout):
        """Test is_tty() convenience function"""
        mock_stdout.isatty.return_value = True
        
        # Initialize detector
        initialize_tty_detector(mode="force-tty")
        
        # Test convenience function
        assert is_tty() is True
    
    @patch('sys.stdout')
    def test_is_tty_fallback_without_init(self, mock_stdout):
        """Test is_tty() falls back to auto-detect if not initialized"""
        # Reset global state
        import src.ui.tty_detector as tty_module
        tty_module._global_detector = None
        
        mock_stdout.isatty.return_value = False
        
        # Should fallback without error
        result = is_tty()
        assert result is False
