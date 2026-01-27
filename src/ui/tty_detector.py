"""
TTY Detection and Mode Selection

Detects whether the application is running in an interactive terminal (TTY)
or in a non-interactive context (pipe, redirect, background job, CI).

Supports three modes:
- auto: automatic detection via sys.stdout.isatty()
- force-tty: always use TTY mode (rich output)
- force-non-tty: always use non-TTY mode (minimal output)

Part of TIER B-5: TTY-Aware Modes optimization.
"""

import os
import sys
from enum import Enum
from typing import Optional


class TTYMode(Enum):
    """TTY detection modes"""
    AUTO = "auto"
    FORCE_TTY = "force-tty"
    FORCE_NON_TTY = "force-non-tty"


class TTYDetector:
    """
    Detects TTY mode and provides unified interface for querying.
    
    Usage:
        detector = TTYDetector(mode="auto")
        if detector.is_tty():
            # Use rich output (colors, progress bars)
        else:
            # Use minimal output (JSON, structured logs)
    """
    
    def __init__(self, mode: str = "auto"):
        """
        Initialize TTY detector.
        
        Args:
            mode: Detection mode ("auto", "force-tty", "force-non-tty")
        """
        self._mode = self._parse_mode(mode)
        self._is_tty = self._detect_tty()
        
    def _parse_mode(self, mode_str: str) -> TTYMode:
        """Parse mode string to TTYMode enum"""
        mode_str = mode_str.lower().strip()
        try:
            return TTYMode(mode_str)
        except ValueError:
            # Invalid mode, default to auto
            return TTYMode.AUTO
    
    def _detect_tty(self) -> bool:
        """
        Detect if running in TTY mode.
        
        Returns:
            True if TTY, False if non-TTY
        """
        if self._mode == TTYMode.FORCE_TTY:
            return True
        elif self._mode == TTYMode.FORCE_NON_TTY:
            return False
        
        # AUTO mode: perform actual detection
        return self._auto_detect()
    
    def _auto_detect(self) -> bool:
        """
        Automatic TTY detection logic.
        
        Checks:
        1. sys.stdout.isatty() - primary check
        2. CI environment variables (GITHUB_ACTIONS, CI, etc.)
        3. TERM environment variable
        
        Returns:
            True if interactive TTY, False otherwise
        """
        # Check if stdout is a TTY
        if not hasattr(sys.stdout, 'isatty'):
            return False
        
        if not sys.stdout.isatty():
            return False
        
        # Check for CI environments (non-interactive)
        ci_vars = [
            'CI', 'CONTINUOUS_INTEGRATION',
            'GITHUB_ACTIONS', 'GITLAB_CI', 'CIRCLECI',
            'TRAVIS', 'JENKINS_URL', 'BUILDKITE'
        ]
        for var in ci_vars:
            if os.environ.get(var):
                return False
        
        # Check TERM variable (should be set in real terminals)
        # Note: Windows PowerShell/CMD may not have TERM set, so we check platform
        import platform
        if platform.system() != 'Windows':
            # On Unix-like systems, TERM should be set
            term = os.environ.get('TERM', '').lower()
            if not term or term == 'dumb':
                return False
        
        return True
    
    def is_tty(self) -> bool:
        """
        Check if running in TTY mode.
        
        Returns:
            True if TTY (interactive), False if non-TTY
        """
        return self._is_tty
    
    def get_mode_name(self) -> str:
        """
        Get human-readable mode name.
        
        Returns:
            Mode name string
        """
        return self._mode.value
    
    def get_detection_info(self) -> dict:
        """
        Get detailed detection information for debugging.
        
        Returns:
            Dict with detection details
        """
        return {
            'mode': self._mode.value,
            'is_tty': self._is_tty,
            'stdout_isatty': sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else None,
            'term': os.environ.get('TERM'),
            'ci_detected': any(os.environ.get(var) for var in [
                'CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'CIRCLECI'
            ])
        }


# Global singleton
_global_detector: Optional[TTYDetector] = None


def initialize_tty_detector(mode: str = "auto") -> TTYDetector:
    """
    Initialize global TTY detector.
    
    Args:
        mode: Detection mode
        
    Returns:
        TTYDetector instance
    """
    global _global_detector
    _global_detector = TTYDetector(mode=mode)
    return _global_detector


def get_tty_detector() -> TTYDetector:
    """
    Get global TTY detector instance.
    
    Returns:
        TTYDetector instance
        
    Raises:
        RuntimeError: If detector not initialized
    """
    if _global_detector is None:
        raise RuntimeError("TTY detector not initialized. Call initialize_tty_detector() first.")
    return _global_detector


def is_tty() -> bool:
    """
    Convenience function to check TTY mode.
    
    Returns:
        True if TTY mode
    """
    try:
        return get_tty_detector().is_tty()
    except RuntimeError:
        # Fallback: auto-detect if not initialized
        return sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False
