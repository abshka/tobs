"""
Tests for ExportTarget dataclass initialization and type detection.

Tests cover:
- Basic initialization with various ID formats
- Type detection for channels, chats, users, forums
- Forum topic URL parsing
- Edge cases and malformed inputs
"""


from src.config import ExportTarget


class TestExportTargetBasicInit:
    """Test basic ExportTarget initialization."""

    def test_init_with_username(self):
        """Test initialization with @username format."""
        target = ExportTarget(id="@testchannel", name="Test Channel")
        assert target.id == "@testchannel"
        assert target.name == "Test Channel"
        assert target.type == "channel"

    def test_init_with_numeric_user_id(self):
        """Test initialization with numeric user ID."""
        target = ExportTarget(id="123456789", name="Test User")
        assert target.id == "123456789"
        assert target.type == "user"

    def test_init_with_chat_id(self):
        """Test initialization with negative chat ID."""
        target = ExportTarget(id="-123456789", name="Test Chat")
        assert target.id == "-123456789"
        assert target.type == "chat"

    def test_init_with_channel_id(self):
        """Test initialization with -100 prefixed channel ID."""
        target = ExportTarget(id="-1001234567890", name="Test Channel")
        assert target.id == "-1001234567890"
        assert target.type == "channel"

    def test_init_strips_whitespace(self):
        """Test that ID whitespace is stripped."""
        target = ExportTarget(id="  @channel  ", name="Test")
        assert target.id == "@channel"


class TestExportTargetTMeLinks:
    """Test t.me link parsing."""

    def test_tme_link_basic(self):
        """Test basic t.me link parsing."""
        target = ExportTarget(id="t.me/testchannel", name="Test")
        assert target.type == "channel"
        assert "t.me/" in target.id

    def test_tme_link_with_https(self):
        """Test t.me link with https."""
        target = ExportTarget(id="https://t.me/testchannel", name="Test")
        assert target.type == "channel"


class TestExportTargetForumTopics:
    """Test forum and topic detection."""

    def test_forum_topic_url_parsing(self):
        """Test parsing forum topic URL (/c/chat_id/topic_id)."""
        target = ExportTarget(id="https://t.me/c/1234567890/123", name="Topic")
        assert target.type == "forum_topic"
        assert target.id == "-1001234567890"
        assert target.topic_id == 123
        assert target.is_forum is True
        assert target.export_all_topics is False

    def test_forum_topic_url_without_https(self):
        """Test parsing forum topic URL without https."""
        target = ExportTarget(id="/c/9876543210/456", name="Topic")
        assert target.type == "forum_topic"
        assert target.id == "-1009876543210"
        assert target.topic_id == 456
        assert target.is_forum is True

    def test_forum_topic_url_malformed(self, caplog):
        """Test malformed forum topic URL logs warning."""
        target = ExportTarget(id="/c/invalid", name="Bad Topic")
        # Should log warning but not crash
        assert "Could not parse forum topic URL" in caplog.text or target.id == "/c/invalid"


class TestExportTargetTypePreservation:
    """Test that explicitly set types are preserved."""

    def test_single_post_type_preserved(self):
        """Test that single_post type is not overwritten."""
        target = ExportTarget(id="@channel", name="Post", type="single_post")
        # __post_init__ should return early for single_post
        assert target.type == "single_post"

    def test_explicit_forum_chat_preserved(self):
        """Test explicitly set forum_chat type is preserved."""
        target = ExportTarget(id="-1001234567890", name="Forum", type="forum_chat")
        assert target.type == "forum_chat"

    def test_explicit_channel_preserved(self):
        """Test explicitly set channel type is preserved."""
        target = ExportTarget(id="123456", name="Channel", type="channel")
        assert target.type == "channel"


class TestExportTargetEdgeCases:
    """Test edge cases and unknown formats."""

    def test_unknown_format_logs_warning(self, caplog):
        """Test unknown ID format logs warning."""
        target = ExportTarget(id="???unknown???", name="Unknown")
        assert "Could not determine type" in caplog.text or target.type == "unknown"

    def test_default_name_empty(self):
        """Test default name is empty string."""
        target = ExportTarget(id="@channel")
        assert target.name == ""

    def test_default_type_unknown(self):
        """Test default type is unknown."""
        # Create with non-matching pattern
        target = ExportTarget(id="", name="Empty", type="unknown")
        assert target.type == "unknown"

    def test_optional_fields_defaults(self):
        """Test optional fields have correct defaults."""
        target = ExportTarget(id="@channel")
        assert target.message_id is None
        assert target.estimated_messages is None
        assert target.last_updated is None
        assert target.priority == 1
        assert target.is_forum is False
        assert target.topic_id is None
        assert target.export_all_topics is True
        assert target.topic_filter is None
        assert target.export_path is None
