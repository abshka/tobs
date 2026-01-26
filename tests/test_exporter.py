import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from pathlib import Path

from src.export.exporter import Exporter
from src.config import ExportTarget

# Mock Message class to simulate Telethon messages
class MockMessage:
    def __init__(self, id, text=None, date=None, sender_id=123, media=None, reactions=None):
        self.id = id
        self.text = text
        self.date = date or datetime.now()
        self.sender_id = sender_id
        self.sender = MagicMock()
        self.sender.first_name = "Test"
        self.sender.last_name = "User"
        self.media = media
        self.reactions = reactions
        self.file = MagicMock() if media else None

@pytest.mark.asyncio
async def test_export_regular_target(exporter, mock_config, mock_telegram_manager):
    
    target = ExportTarget(id=12345, name="Test Chat", type="regular")
    
    # Mock messages
    messages = [
        MockMessage(id=1, text="Hello world"),
        MockMessage(id=2, text="Second message")
    ]
    
    # Configure mock manager to yield these messages
    async def _fetch_messages(*args, **kwargs):
        for msg in messages:
            yield msg
    mock_telegram_manager.fetch_messages = _fetch_messages
    
    # Mock resolve_entity
    mock_entity = MagicMock()
    mock_entity.title = "Test Chat"
    mock_telegram_manager.resolve_entity.return_value = mock_entity

    # Run export
    stats = await exporter.export_target(target)
    
    # Verify
    assert stats.messages_processed == 2
    assert stats.errors_encountered == 0
    # Check if directory was created (mocked config returns tmp_path/export/test_chat)
    output_dir = mock_config.get_export_path_for_entity(12345)
    assert (output_dir / "Test_Chat.md").exists()
    
    # Verify content
    with open(output_dir / "Test_Chat.md", "r") as f:
        content = f.read()
        assert "Hello world" in content
        assert "Second message" in content

@pytest.mark.asyncio
async def test_export_reactions(exporter, mock_config, mock_telegram_manager):
    
    target = ExportTarget(id=12345, name="Test Chat", type="regular")
    
    # Mock reactions
    mock_reaction_count = MagicMock()
    mock_reaction_count.count = 5
    mock_reaction_count.reaction = MagicMock()
    mock_reaction_count.reaction.emoticon = "👍"
    
    mock_reactions = MagicMock()
    mock_reactions.results = [mock_reaction_count]
    
    messages = [
        MockMessage(id=1, text="Message with reaction", reactions=mock_reactions)
    ]
    
    async def _fetch_messages(*args, **kwargs):
        for msg in messages:
            yield msg
    mock_telegram_manager.fetch_messages = _fetch_messages
    mock_telegram_manager.resolve_entity.return_value = MagicMock(title="Test Chat")

    # Run export
    await exporter.export_target(target)
    
    # Verify content
    output_dir = mock_config.get_export_path_for_entity(12345)
    with open(output_dir / "Test_Chat.md", "r") as f:
        content = f.read()
        assert "**Reactions:** 👍 5" in content

@pytest.mark.asyncio
async def test_export_sender_resolves_when_sender_missing(exporter, mock_config, mock_telegram_manager):
    target = ExportTarget(id=12345, name="Test Chat", type="regular")

    # Prepare message with missing sender object
    msg = MockMessage(id=1, text="Hello resolved sender")
    msg.sender = None
    msg.sender_id = 999

    async def _fetch_messages(*args, **kwargs):
        yield msg

    mock_telegram_manager.fetch_messages = _fetch_messages

    # Mock resolve_entity: first call returns entity title, second call returns sender
    mock_entity = MagicMock()
    mock_entity.title = "Test Chat"
    mock_sender = MagicMock()
    mock_sender.first_name = "Resolved"
    mock_sender.last_name = "User"

    async def _resolve(entity_id):
        if str(entity_id) == str(target.id):
            return mock_entity
        if str(entity_id) == str(msg.sender_id):
            return mock_sender
        return None

    mock_telegram_manager.resolve_entity = AsyncMock(side_effect=_resolve)

    # Run export
    await exporter.export_target(target)

    output_dir = mock_config.get_export_path_for_entity(12345)
    with open(output_dir / "Test_Chat.md", "r") as f:
        content = f.read()
        assert "Resolved User" in content

@pytest.mark.asyncio
async def test_export_forum(exporter, mock_config, mock_telegram_manager):
    
    target = ExportTarget(id=999, name="Forum Chat", type="forum")
    
    # Mock topics
    mock_topic1 = MagicMock()
    mock_topic1.topic_id = 1
    mock_topic1.title = "General"
    
    mock_topic2 = MagicMock()
    mock_topic2.topic_id = 2
    mock_topic2.title = "Random"
    
    mock_telegram_manager.get_forum_topics.return_value = [mock_topic1, mock_topic2]
    mock_telegram_manager.resolve_entity.return_value = MagicMock(title="Forum Chat")
    
    # Mock messages per topic
    async def _get_topic_messages_stream(entity, topic_id):
        if topic_id == 1:
            yield MockMessage(id=10, text="General msg 1")
            yield MockMessage(id=11, text="General msg 2")
        elif topic_id == 2:
            yield MockMessage(id=20, text="Random msg 1")
            
    mock_telegram_manager.get_topic_messages_stream = _get_topic_messages_stream

    # Config export path for forum (needs to handle the forum structure)
    # The Exporter uses config.export_path / sanitized_name for forums
    # We need to make sure mock_config.export_path is set correctly in fixture
    
    # Run export
    stats = await exporter.export_target(target)
    
    # Verify
    assert stats.messages_processed == 3
    
    forum_dir = mock_config.export_path / "Forum_Chat"
    topics_dir = forum_dir / "topics"
    
    assert topics_dir.exists()
    assert (topics_dir / "General.md").exists()
    assert (topics_dir / "Random.md").exists()
    
    with open(topics_dir / "General.md", "r") as f:
        content = f.read()
        assert "General msg 1" in content
        assert "General msg 2" in content
        
    with open(topics_dir / "Random.md", "r") as f:
        content = f.read()
        assert "Random msg 1" in content

