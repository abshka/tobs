"""
Unit tests for CacheManager legacy API methods.

These methods provide backwards compatibility with the old cache interface.

Tests cover:
- is_processed(): Check if message was processed
- add_processed_message_async(): Add message to entity
- update_entity_info_async(): Update entity metadata
- get_last_processed_message_id_async(): Get last message ID
- get_all_processed_messages_async(): Get all processed messages
- cache property: Access cache in old format
- flush_all_pending(): Force save
"""

import time

import pytest

from src.core.cache import CacheManager

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def cache_manager(tmp_path):
    """Create a CacheManager with temp path."""
    cache_path = tmp_path / "test_cache.json"
    return CacheManager(cache_path)


@pytest.fixture
async def manager_with_entity(cache_manager):
    """Manager pre-populated with entity data."""
    await cache_manager.add_processed_message_async(
        message_id=100,
        entity_id="chat_123",
        text="Test message",
        timestamp=time.time()
    )
    await cache_manager.update_entity_info_async(
        entity_id="chat_123",
        title="Test Chat",
        entity_type="channel"
    )
    return cache_manager


# ============================================================================
# is_processed() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_is_processed_returns_true_when_exists(manager_with_entity):
    """Returns True when message_id exists in entity data."""
    result = await manager_with_entity.is_processed(100, "chat_123")
    assert result is True


@pytest.mark.asyncio
async def test_is_processed_returns_false_when_not_exists(manager_with_entity):
    """Returns False when message_id doesn't exist."""
    result = await manager_with_entity.is_processed(999, "chat_123")
    assert result is False


@pytest.mark.asyncio
async def test_is_processed_returns_false_when_entity_not_exists(cache_manager):
    """Returns False when entity doesn't exist."""
    result = await cache_manager.is_processed(100, "nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_is_processed_constructs_correct_key(manager_with_entity):
    """Correctly constructs entity key format."""
    # Should use format: entity_{entity_id}
    result = await manager_with_entity.is_processed(100, "chat_123")
    assert result is True

    # Verify the key exists in cache
    assert "entity_chat_123" in manager_with_entity._cache


# ============================================================================
# add_processed_message_async() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_add_processed_message_adds_to_entity(cache_manager):
    """Adds message to entity's processed_messages."""
    await cache_manager.add_processed_message_async(
        message_id=101,
        entity_id="chat_456",
        text="Hello"
    )

    is_processed = await cache_manager.is_processed(101, "chat_456")
    assert is_processed is True


@pytest.mark.asyncio
async def test_add_processed_message_creates_entity_if_not_exists(cache_manager):
    """Creates entity data if it doesn't exist."""
    await cache_manager.add_processed_message_async(
        message_id=102,
        entity_id="new_chat"
    )

    # Entity should now exist
    entity_data = await cache_manager.get("entity_new_chat")
    assert entity_data is not None
    assert "processed_messages" in entity_data


@pytest.mark.asyncio
async def test_add_processed_message_updates_last_id(cache_manager):
    """Updates last_id to current message_id."""
    await cache_manager.add_processed_message_async(
        message_id=200,
        entity_id="chat_789"
    )

    entity_data = await cache_manager.get("entity_chat_789")
    assert entity_data["last_id"] == 200


@pytest.mark.asyncio
async def test_add_processed_message_includes_timestamp(cache_manager):
    """Includes timestamp in message data."""
    before_time = time.time()
    await cache_manager.add_processed_message_async(
        message_id=103,
        entity_id="chat_ts"
    )
    after_time = time.time()

    entity_data = await cache_manager.get("entity_chat_ts")
    msg_data = entity_data["processed_messages"]["103"]

    assert "timestamp" in msg_data
    assert before_time <= msg_data["timestamp"] <= after_time


@pytest.mark.asyncio
async def test_add_processed_message_stores_kwargs(cache_manager):
    """Accepts and stores additional kwargs."""
    await cache_manager.add_processed_message_async(
        message_id=104,
        entity_id="chat_extra",
        text="Message text",
        sender="user_123",
        reactions=5
    )

    entity_data = await cache_manager.get("entity_chat_extra")
    msg_data = entity_data["processed_messages"]["104"]

    assert msg_data["text"] == "Message text"
    assert msg_data["sender"] == "user_123"
    assert msg_data["reactions"] == 5


@pytest.mark.asyncio
async def test_add_processed_message_initializes_default_structure(cache_manager):
    """Initializes entity with default structure."""
    await cache_manager.add_processed_message_async(
        message_id=105,
        entity_id="chat_default"
    )

    entity_data = await cache_manager.get("entity_chat_default")

    # Should have default fields
    assert "processed_messages" in entity_data
    assert "last_id" in entity_data
    assert "title" in entity_data
    assert "type" in entity_data


# ============================================================================
# update_entity_info_async() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_update_entity_info_updates_existing(manager_with_entity):
    """Updates title and type for existing entity."""
    await manager_with_entity.update_entity_info_async(
        entity_id="chat_123",
        title="Updated Chat",
        entity_type="group"
    )

    entity_data = await manager_with_entity.get("entity_chat_123")
    assert entity_data["title"] == "Updated Chat"
    assert entity_data["type"] == "group"


@pytest.mark.asyncio
async def test_update_entity_info_creates_if_not_exists(cache_manager):
    """Creates entity if it doesn't exist."""
    await cache_manager.update_entity_info_async(
        entity_id="new_entity",
        title="New Chat",
        entity_type="private"
    )

    entity_data = await cache_manager.get("entity_new_entity")
    assert entity_data is not None
    assert entity_data["title"] == "New Chat"
    assert entity_data["type"] == "private"


@pytest.mark.asyncio
async def test_update_entity_info_preserves_processed_messages(manager_with_entity):
    """Preserves processed_messages and last_id."""
    # Get original data
    original_data = await manager_with_entity.get("entity_chat_123")
    original_messages = original_data["processed_messages"]
    original_last_id = original_data["last_id"]

    # Update info
    await manager_with_entity.update_entity_info_async(
        entity_id="chat_123",
        title="Changed Title",
        entity_type="megagroup"
    )

    # Check preserved data
    updated_data = await manager_with_entity.get("entity_chat_123")
    assert updated_data["processed_messages"] == original_messages
    assert updated_data["last_id"] == original_last_id


@pytest.mark.asyncio
async def test_update_entity_info_sets_dirty_flag(cache_manager):
    """Sets dirty flag (via set())."""
    cache_manager._dirty = False

    await cache_manager.update_entity_info_async(
        entity_id="chat_flag",
        title="Test",
        entity_type="channel"
    )

    assert cache_manager._dirty is True


# ============================================================================
# get_last_processed_message_id_async() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_last_message_id_returns_id(manager_with_entity):
    """Returns last_id when entity exists."""
    last_id = await manager_with_entity.get_last_processed_message_id_async("chat_123")
    assert last_id == 100


@pytest.mark.asyncio
async def test_get_last_message_id_returns_none_when_entity_not_exists(cache_manager):
    """Returns None when entity doesn't exist."""
    last_id = await cache_manager.get_last_processed_message_id_async("nonexistent")
    assert last_id is None


@pytest.mark.asyncio
async def test_get_last_message_id_returns_none_when_last_id_none(cache_manager):
    """Returns None when last_id is None."""
    # Create entity with no messages
    await cache_manager.set("entity_empty", {
        "processed_messages": {},
        "last_id": None,
        "title": "Empty",
        "type": "private"
    })

    last_id = await cache_manager.get_last_processed_message_id_async("empty")
    assert last_id is None


@pytest.mark.asyncio
async def test_get_last_message_id_converts_to_int(cache_manager):
    """Converts last_id to int if needed."""
    # Add multiple messages
    await cache_manager.add_processed_message_async(50, "chat_int")
    await cache_manager.add_processed_message_async(150, "chat_int")
    await cache_manager.add_processed_message_async(250, "chat_int")

    last_id = await cache_manager.get_last_processed_message_id_async("chat_int")

    assert isinstance(last_id, int)
    assert last_id == 250


# ============================================================================
# get_all_processed_messages_async() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_all_messages_returns_dict(manager_with_entity):
    """Returns all processed messages for entity."""
    messages = await manager_with_entity.get_all_processed_messages_async("chat_123")

    assert isinstance(messages, dict)
    assert "100" in messages


@pytest.mark.asyncio
async def test_get_all_messages_returns_empty_when_entity_not_exists(cache_manager):
    """Returns empty dict when entity doesn't exist."""
    messages = await cache_manager.get_all_processed_messages_async("nonexistent")

    assert messages == {}


@pytest.mark.asyncio
async def test_get_all_messages_returns_empty_when_no_messages(cache_manager):
    """Returns empty dict when processed_messages is missing."""
    await cache_manager.set("entity_nomsg", {
        "last_id": None,
        "title": "No Messages",
        "type": "private"
    })

    messages = await cache_manager.get_all_processed_messages_async("nomsg")
    assert messages == {}


@pytest.mark.asyncio
async def test_get_all_messages_returns_copy(manager_with_entity):
    """Returns dict copy (not reference)."""
    messages1 = await manager_with_entity.get_all_processed_messages_async("chat_123")
    messages2 = await manager_with_entity.get_all_processed_messages_async("chat_123")

    # Modify one
    messages1["999"] = {"new": "data"}

    # Other should not be affected
    assert "999" not in messages2


# ============================================================================
# cache property TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_cache_property_returns_correct_structure(manager_with_entity):
    """Returns dict with version and entities."""
    cache_data = manager_with_entity.cache

    assert isinstance(cache_data, dict)
    assert "version" in cache_data
    assert "entities" in cache_data
    assert cache_data["version"] == 2


@pytest.mark.asyncio
async def test_cache_property_includes_only_entity_keys(cache_manager):
    """Includes only entity_* keys in entities dict."""
    # Add both entity and non-entity keys
    await cache_manager.set("entity_test1", {"data": "entity1"})
    await cache_manager.set("entity_test2", {"data": "entity2"})
    await cache_manager.set("some_other_key", {"data": "other"})

    cache_data = cache_manager.cache
    entities = cache_data["entities"]

    # Should only have entity keys
    assert "test1" in entities
    assert "test2" in entities
    assert "some_other_key" not in entities


@pytest.mark.asyncio
async def test_cache_property_extracts_entity_id_correctly(manager_with_entity):
    """Extracts entity_id from key correctly."""
    cache_data = manager_with_entity.cache
    entities = cache_data["entities"]

    # entity_chat_123 should become chat_123
    assert "chat_123" in entities
    assert "entity_chat_123" not in entities


@pytest.mark.asyncio
async def test_cache_property_returns_entry_data(manager_with_entity):
    """Returns entry data for each entity."""
    cache_data = manager_with_entity.cache
    entity_data = cache_data["entities"]["chat_123"]

    # Should have entity fields
    assert "processed_messages" in entity_data
    assert "title" in entity_data
    assert entity_data["title"] == "Test Chat"


# ============================================================================
# flush_all_pending() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_flush_calls_save_cache(cache_manager, monkeypatch):
    """Calls _save_cache() directly."""
    save_called = False

    async def mock_save():
        nonlocal save_called
        save_called = True

    monkeypatch.setattr(cache_manager, "_save_cache", mock_save)

    await cache_manager.flush_all_pending()

    assert save_called


@pytest.mark.asyncio
async def test_flush_forces_save_regardless_of_dirty_flag(cache_manager, monkeypatch):
    """Forces save regardless of dirty flag."""
    # Set cache as clean
    cache_manager._dirty = False

    save_called = False

    async def mock_save():
        nonlocal save_called
        save_called = True

    monkeypatch.setattr(cache_manager, "_save_cache", mock_save)

    # flush should still call save
    await cache_manager.flush_all_pending()

    assert save_called
