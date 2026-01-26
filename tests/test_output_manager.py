"""
Unit tests for Output Manager module (TIER B - B-5).
"""

import json
import io
import sys
import pytest
from unittest.mock import patch, MagicMock

from src.ui.output_manager import (
    ProgressUpdate,
    TTYOutputAdapter,
    NonTTYOutputAdapter,
    OutputManager,
    initialize_output_manager,
    get_output_manager
)


class TestProgressUpdate:
    """Test ProgressUpdate dataclass"""
    
    def test_progress_update_creation(self):
        """Test creating ProgressUpdate"""
        update = ProgressUpdate(
            entity_name="TestChat",
            messages_processed=100,
            total_messages=1000,
            stage="downloading",
            percentage=10.0
        )
        
        assert update.entity_name == "TestChat"
        assert update.messages_processed == 100
        assert update.total_messages == 1000
        assert update.stage == "downloading"
        assert update.percentage == 10.0
    
    def test_progress_update_to_dict(self):
        """Test to_dict conversion"""
        update = ProgressUpdate(
            entity_name="Test",
            messages_processed=50,
            total_messages=None,
            stage="processing"
        )
        
        data = update.to_dict()
        assert isinstance(data, dict)
        assert data["entity_name"] == "Test"
        assert data["messages_processed"] == 50
        assert data["total_messages"] is None


class TestTTYOutputAdapter:
    """Test TTY output adapter"""
    
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_show_progress_with_percentage(self, mock_stdout):
        """Test show_progress with percentage"""
        adapter = TTYOutputAdapter()
        update = ProgressUpdate(
            entity_name="TestChat",
            messages_processed=500,
            total_messages=1000,
            stage="processing",
            percentage=50.0
        )
        
        adapter.show_progress(update)
        output = mock_stdout.getvalue()
        
        # Check output contains key elements
        assert "TestChat" in output
        assert "500" in output
        assert "50.0%" in output
        assert "processing" in output
    
    @patch('sys.stdout', new_callable=io.StringIO)
    def test_show_progress_without_percentage(self, mock_stdout):
        """Test show_progress without percentage"""
        adapter = TTYOutputAdapter()
        update = ProgressUpdate(
            entity_name="TestChat",
            messages_processed=100,
            total_messages=None,
            stage="processing",
            percentage=None
        )
        
        adapter.show_progress(update)
        output = mock_stdout.getvalue()
        
        assert "TestChat" in output
        assert "100" in output
        assert "processing" in output
    
    def test_show_message_levels(self, capsys):
        """Test show_message with different levels"""
        adapter = TTYOutputAdapter()
        
        for level in ["info", "success", "warning", "error", "debug"]:
            adapter.show_message(f"Test {level}", level=level)
        
        captured = capsys.readouterr()
        output = captured.out
        
        assert "Test info" in output
        assert "Test success" in output
        assert "Test warning" in output
        assert "Test error" in output
        assert "Test debug" in output
    
    def test_show_error(self, capsys):
        """Test show_error method"""
        adapter = TTYOutputAdapter()
        adapter.show_error("Something went wrong")
        
        captured = capsys.readouterr()
        assert "Something went wrong" in captured.out
    
    def test_start_export(self, capsys):
        """Test start_export message"""
        adapter = TTYOutputAdapter()
        adapter.start_export("TestChat", total_messages=1000)
        
        captured = capsys.readouterr()
        output = captured.out
        
        assert "Starting export" in output
        assert "TestChat" in output
        assert "1000" in output
    
    def test_finish_export_success(self, capsys):
        """Test finish_export with success"""
        adapter = TTYOutputAdapter()
        adapter.finish_export("TestChat", success=True)
        
        captured = capsys.readouterr()
        output = captured.out
        
        assert "Completed" in output
        assert "TestChat" in output
    
    def test_finish_export_failure(self, capsys):
        """Test finish_export with failure"""
        adapter = TTYOutputAdapter()
        adapter.finish_export("TestChat", success=False)
        
        captured = capsys.readouterr()
        output = captured.out
        
        assert "Failed" in output
        assert "TestChat" in output


class TestNonTTYOutputAdapter:
    """Test Non-TTY output adapter"""
    
    def test_show_progress_json(self, capsys):
        """Test show_progress outputs JSON"""
        adapter = NonTTYOutputAdapter()
        update = ProgressUpdate(
            entity_name="TestChat",
            messages_processed=100,
            total_messages=1000,
            stage="processing",
            percentage=10.0
        )
        
        adapter.show_progress(update)
        captured = capsys.readouterr()
        output = captured.out.strip()
        
        # Parse JSON
        data = json.loads(output)
        assert data["type"] == "progress"
        assert data["entity_name"] == "TestChat"
        assert data["messages_processed"] == 100
        assert data["total_messages"] == 1000
        assert data["percentage"] == 10.0
    
    def test_show_message_json(self, capsys):
        """Test show_message outputs JSON"""
        adapter = NonTTYOutputAdapter()
        adapter.show_message("Test message", level="info")
        
        captured = capsys.readouterr()
        output = captured.out.strip()
        
        data = json.loads(output)
        assert data["type"] == "message"
        assert data["level"] == "info"
        assert data["message"] == "Test message"
    
    def test_show_error_json(self, capsys):
        """Test show_error outputs JSON"""
        adapter = NonTTYOutputAdapter()
        adapter.show_error("Error message")
        
        captured = capsys.readouterr()
        output = captured.out.strip()
        
        data = json.loads(output)
        assert data["type"] == "error"
        assert data["message"] == "Error message"
    
    def test_start_export_json(self, capsys):
        """Test start_export outputs JSON"""
        adapter = NonTTYOutputAdapter()
        adapter.start_export("TestChat", total_messages=1000)
        
        captured = capsys.readouterr()
        output = captured.out.strip()
        
        data = json.loads(output)
        assert data["type"] == "export_start"
        assert data["entity_name"] == "TestChat"
        assert data["total_messages"] == 1000
    
    def test_finish_export_json(self, capsys):
        """Test finish_export outputs JSON"""
        adapter = NonTTYOutputAdapter()
        adapter.finish_export("TestChat", success=True)
        
        captured = capsys.readouterr()
        output = captured.out.strip()
        
        data = json.loads(output)
        assert data["type"] == "export_finish"
        assert data["entity_name"] == "TestChat"
        assert data["success"] is True


class TestOutputManager:
    """Test OutputManager class"""
    
    @patch('src.ui.output_manager.is_tty', return_value=True)
    def test_auto_select_tty_adapter(self, mock_is_tty):
        """Test auto-selection of TTY adapter"""
        manager = OutputManager()
        assert isinstance(manager._adapter, TTYOutputAdapter)
    
    @patch('src.ui.output_manager.is_tty', return_value=False)
    def test_auto_select_non_tty_adapter(self, mock_is_tty):
        """Test auto-selection of non-TTY adapter"""
        manager = OutputManager()
        assert isinstance(manager._adapter, NonTTYOutputAdapter)
    
    def test_custom_adapter(self):
        """Test providing custom adapter"""
        custom_adapter = NonTTYOutputAdapter()
        manager = OutputManager(adapter=custom_adapter)
        
        assert manager._adapter is custom_adapter
    
    def test_delegation_methods(self):
        """Test all delegation methods call adapter"""
        mock_adapter = MagicMock()
        manager = OutputManager(adapter=mock_adapter)
        
        # Test show_progress
        update = ProgressUpdate("Test", 100, None, "stage")
        manager.show_progress(update)
        mock_adapter.show_progress.assert_called_once_with(update)
        
        # Test show_message
        manager.show_message("msg", level="info")
        mock_adapter.show_message.assert_called_once_with("msg", level="info")
        
        # Test show_error
        manager.show_error("error")
        mock_adapter.show_error.assert_called_once_with("error")
        
        # Test start_export
        manager.start_export("Chat", 1000)
        mock_adapter.start_export.assert_called_once_with("Chat", 1000)
        
        # Test finish_export
        manager.finish_export("Chat", True)
        mock_adapter.finish_export.assert_called_once_with("Chat", True)


class TestOutputManagerGlobals:
    """Test global singleton functions"""
    
    def test_initialize_and_get(self):
        """Test initialize_output_manager and get_output_manager"""
        manager = initialize_output_manager()
        
        # Should be able to get the same instance
        retrieved = get_output_manager()
        
        assert retrieved is manager
    
    @patch('src.ui.output_manager.is_tty', return_value=False)
    def test_auto_initialize_on_get(self, mock_is_tty):
        """Test get_output_manager auto-initializes if needed"""
        # Reset global state
        import src.ui.output_manager as output_module
        output_module._global_output_manager = None
        
        # Should auto-initialize without error
        manager = get_output_manager()
        assert manager is not None
        assert isinstance(manager._adapter, NonTTYOutputAdapter)
