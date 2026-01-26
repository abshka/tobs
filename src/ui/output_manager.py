"""
Output Manager - Unified Interface for TTY/Non-TTY Output

Provides adaptive output based on TTY mode:
- TTY mode: rich progress bars, colored output, interactive elements
- Non-TTY mode: minimal output, JSON progress, structured logs

Part of TIER B-5: TTY-Aware Modes optimization.
"""

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional, Any, Dict

from src.ui.tty_detector import get_tty_detector, is_tty


@dataclass(slots=True)
class ProgressUpdate:
    """Progress update data structure"""
    entity_name: str
    messages_processed: int
    total_messages: Optional[int]
    stage: str
    percentage: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization"""
        return asdict(self)


class OutputAdapter(ABC):
    """Abstract base class for output adapters"""
    
    @abstractmethod
    def show_progress(self, update: ProgressUpdate) -> None:
        """Show progress update"""
        pass
    
    @abstractmethod
    def show_message(self, message: str, level: str = "info") -> None:
        """Show a message"""
        pass
    
    @abstractmethod
    def show_error(self, error: str) -> None:
        """Show error message"""
        pass
    
    @abstractmethod
    def start_export(self, entity_name: str, total_messages: Optional[int]) -> None:
        """Called when export starts"""
        pass
    
    @abstractmethod
    def finish_export(self, entity_name: str, success: bool) -> None:
        """Called when export finishes"""
        pass


class TTYOutputAdapter(OutputAdapter):
    """
    TTY mode output adapter - rich, colorful, interactive.
    
    Uses ANSI escape codes for colors and progress bars.
    """
    
    # ANSI color codes
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    def __init__(self):
        self._last_progress_line = ""
    
    def _colorize(self, text: str, color: str) -> str:
        """Add color to text"""
        return f"{color}{text}{self.RESET}"
    
    def show_progress(self, update: ProgressUpdate) -> None:
        """Show progress with colored bar"""
        if update.percentage is not None:
            bar_width = 30
            filled = int(bar_width * update.percentage / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            
            progress_text = (
                f"\r{self._colorize('▶', self.CYAN)} "
                f"{self._colorize(update.entity_name, self.BOLD)} "
                f"[{self._colorize(bar, self.GREEN)}] "
                f"{self._colorize(f'{update.percentage:.1f}%', self.YELLOW)} "
                f"({update.messages_processed}"
            )
            
            if update.total_messages:
                progress_text += f"/{update.total_messages}"
            
            progress_text += f") - {self._colorize(update.stage, self.DIM)}"
            
            # Overwrite previous line
            sys.stdout.write(progress_text)
            sys.stdout.flush()
            self._last_progress_line = progress_text
        else:
            # No percentage available
            progress_text = (
                f"\r{self._colorize('▶', self.CYAN)} "
                f"{self._colorize(update.entity_name, self.BOLD)}: "
                f"{update.messages_processed} messages - "
                f"{self._colorize(update.stage, self.DIM)}"
            )
            sys.stdout.write(progress_text)
            sys.stdout.flush()
            self._last_progress_line = progress_text
    
    def show_message(self, message: str, level: str = "info") -> None:
        """Show colored message"""
        # Clear progress line if present
        if self._last_progress_line:
            sys.stdout.write("\r" + " " * len(self._last_progress_line) + "\r")
            self._last_progress_line = ""
        
        color_map = {
            "info": self.BLUE,
            "success": self.GREEN,
            "warning": self.YELLOW,
            "error": self.RED,
            "debug": self.DIM
        }
        color = color_map.get(level, self.WHITE)
        
        icon_map = {
            "info": "ℹ",
            "success": "✓",
            "warning": "⚠",
            "error": "✗",
            "debug": "•"
        }
        icon = icon_map.get(level, "•")
        
        print(f"{self._colorize(icon, color)} {message}")
    
    def show_error(self, error: str) -> None:
        """Show error in red"""
        self.show_message(error, level="error")
    
    def start_export(self, entity_name: str, total_messages: Optional[int]) -> None:
        """Show export start message"""
        msg = f"Starting export: {entity_name}"
        if total_messages:
            msg += f" ({total_messages} messages)"
        self.show_message(msg, level="info")
    
    def finish_export(self, entity_name: str, success: bool) -> None:
        """Show export completion"""
        # Clear progress line
        if self._last_progress_line:
            sys.stdout.write("\r" + " " * len(self._last_progress_line) + "\r")
            self._last_progress_line = ""
        
        if success:
            self.show_message(f"Completed: {entity_name}", level="success")
        else:
            self.show_message(f"Failed: {entity_name}", level="error")


class NonTTYOutputAdapter(OutputAdapter):
    """
    Non-TTY mode output adapter - minimal, JSON-based, parseable.
    
    Designed for:
    - Pipes and redirects
    - CI/CD environments
    - Background jobs
    - Log parsing tools
    """
    
    def show_progress(self, update: ProgressUpdate) -> None:
        """Output progress as JSON line"""
        data = update.to_dict()
        data['type'] = 'progress'
        print(json.dumps(data), flush=True)
    
    def show_message(self, message: str, level: str = "info") -> None:
        """Output message as JSON line"""
        data = {
            'type': 'message',
            'level': level,
            'message': message
        }
        print(json.dumps(data), flush=True)
    
    def show_error(self, error: str) -> None:
        """Output error as JSON line"""
        data = {
            'type': 'error',
            'message': error
        }
        print(json.dumps(data), flush=True)
    
    def start_export(self, entity_name: str, total_messages: Optional[int]) -> None:
        """Output export start as JSON"""
        data = {
            'type': 'export_start',
            'entity_name': entity_name,
            'total_messages': total_messages
        }
        print(json.dumps(data), flush=True)
    
    def finish_export(self, entity_name: str, success: bool) -> None:
        """Output export finish as JSON"""
        data = {
            'type': 'export_finish',
            'entity_name': entity_name,
            'success': success
        }
        print(json.dumps(data), flush=True)


class OutputManager:
    """
    Unified output manager that delegates to appropriate adapter.
    
    Usage:
        output = OutputManager()
        output.show_progress(ProgressUpdate(...))
    """
    
    def __init__(self, adapter: Optional[OutputAdapter] = None):
        """
        Initialize output manager.
        
        Args:
            adapter: Custom adapter (if None, auto-select based on TTY)
        """
        if adapter is None:
            # Auto-select adapter based on TTY detection
            if is_tty():
                self._adapter = TTYOutputAdapter()
            else:
                self._adapter = NonTTYOutputAdapter()
        else:
            self._adapter = adapter
    
    def show_progress(self, update: ProgressUpdate) -> None:
        """Delegate to adapter"""
        self._adapter.show_progress(update)
    
    def show_message(self, message: str, level: str = "info") -> None:
        """Delegate to adapter"""
        self._adapter.show_message(message, level=level)
    
    def show_error(self, error: str) -> None:
        """Delegate to adapter"""
        self._adapter.show_error(error)
    
    def start_export(self, entity_name: str, total_messages: Optional[int] = None) -> None:
        """Delegate to adapter"""
        self._adapter.start_export(entity_name, total_messages)
    
    def finish_export(self, entity_name: str, success: bool = True) -> None:
        """Delegate to adapter"""
        self._adapter.finish_export(entity_name, success)


# Global singleton
_global_output_manager: Optional[OutputManager] = None


def initialize_output_manager(adapter: Optional[OutputAdapter] = None) -> OutputManager:
    """
    Initialize global output manager.
    
    Args:
        adapter: Custom adapter (if None, auto-select)
        
    Returns:
        OutputManager instance
    """
    global _global_output_manager
    _global_output_manager = OutputManager(adapter=adapter)
    return _global_output_manager


def get_output_manager() -> OutputManager:
    """
    Get global output manager instance.
    
    Returns:
        OutputManager instance
    """
    global _global_output_manager
    if _global_output_manager is None:
        # Auto-initialize if not done
        _global_output_manager = OutputManager()
    return _global_output_manager
