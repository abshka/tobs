"""
Unit tests for BaseProcessor.

Tests the abstract base class for all media processors.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.media.models import ProcessingSettings, ProcessingTask
from src.media.processors.base import BaseProcessor

pytestmark = pytest.mark.unit


class ConcreteProcessor(BaseProcessor):
    """Concrete implementation of BaseProcessor for testing."""

    async def process(self, task: ProcessingTask, worker_name: str) -> bool:
        """Minimal process implementation."""
        return True

    def needs_processing(self, file_path: Path, settings: ProcessingSettings) -> bool:
        """Minimal needs_processing implementation."""
        return False


class TestBaseProcessor:
    """Tests for BaseProcessor abstract class."""

    @pytest.fixture
    def base_processor(self, io_executor, cpu_executor):
        """Create ConcreteProcessor instance for tests."""
        return ConcreteProcessor(
            io_executor=io_executor,
            cpu_executor=cpu_executor,
        )

    def test_initialization(self, base_processor):
        """Test BaseProcessor initialization."""
        # TODO: Implement test
        # - Verify io_executor is stored
        # - Verify cpu_executor is stored
        # - Verify settings can be provided or defaults
        pass

    def test_initialization_with_settings(self, io_executor, cpu_executor):
        """Test initialization with custom settings."""
        # TODO: Implement test
        # - Create BaseProcessor with custom ProcessingSettings
        # - Verify settings are stored
        pass

    async def test_process_method_exists(self, base_processor, processing_task):
        """Test that process() method exists in concrete class."""
        # TODO: Implement test
        # - Call process() on concrete implementation
        # - Verify it executes without error
        pass

    def test_needs_processing_method_exists(self, base_processor, tmp_path):
        """Test that needs_processing() method exists."""
        # TODO: Implement test
        # - Call needs_processing() on concrete implementation
        # - Verify it returns boolean
        pass

    def test_abstract_class_cannot_instantiate(self):
        """Test that BaseProcessor cannot be instantiated directly."""
        # TODO: Implement test
        # - Try to instantiate BaseProcessor directly
        # - Verify TypeError is raised
        # - Verify error message about abstract methods
        pass

    def test_settings_property(self, base_processor):
        """Test settings property access."""
        # TODO: Implement test
        # - Access base_processor.settings
        # - Verify returns ProcessingSettings instance
        pass

    def test_executors_accessible(self, base_processor):
        """Test that executors are accessible."""
        # TODO: Implement test
        # - Verify io_executor is accessible
        # - Verify cpu_executor is accessible
        # - Verify they are ThreadPoolExecutor instances
        pass


# No integration tests needed for abstract base class
