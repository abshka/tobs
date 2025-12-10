"""Lazy Media Loader for TOBS.

Provides lazy loading functionality for media files, allowing metadata storage
and on-demand downloading instead of immediate downloads during export.
"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loguru import logger
from telethon.tl.types import Message

from .manager import MediaProcessor


@dataclass
class LazyMediaMetadata:
    """Metadata for lazy-loaded media."""

    message_id: int
    entity_id: Union[str, int]
    media_type: str
    file_name: str
    file_size: int
    mime_type: Optional[str]
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    telegram_file_id: Optional[str] = None
    download_url: Optional[str] = None
    created_at: Optional[float] = None
    lazy_load_token: Optional[str] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()
        if self.lazy_load_token is None:
            self.lazy_load_token = (
                f"{self.entity_id}_{self.message_id}_{int(self.created_at)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LazyMediaMetadata":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class LazyLoadPlaceholder:
    """Placeholder information for lazy-loaded media in markdown."""

    token: str
    media_type: str
    file_name: str
    file_size: int
    description: str

    def to_markdown(self) -> str:
        """Convert to markdown placeholder."""
        size_mb = self.file_size / (1024 * 1024)
        return f"![[Lazy Load: {self.description} ({size_mb:.1f}MB) - Token: {self.token}]]"


class LazyMediaLoader:
    """
    Handles lazy loading of media files.

    Instead of downloading media immediately during export, this class:
    1. Stores media metadata and download information
    2. Creates placeholders in exported markdown
    3. Provides on-demand downloading functionality
    """

    def __init__(
        self, media_processor: MediaProcessor, metadata_dir: Path, cache_manager=None
    ):
        """
        Initialize lazy media loader.

        Args:
            media_processor: The main media processor
            metadata_dir: Directory to store metadata files
            cache_manager: Optional cache manager for downloaded files
        """
        self.media_processor = media_processor
        self.metadata_dir = metadata_dir
        self.cache_manager = cache_manager
        self.metadata_dir.mkdir(exist_ok=True)

        # In-memory cache of metadata
        self._metadata_cache: Dict[str, LazyMediaMetadata] = {}
        self._placeholders_cache: Dict[str, LazyLoadPlaceholder] = {}

        logger.info(f"LazyMediaLoader initialized with metadata dir: {metadata_dir}")

    async def store_media_metadata(
        self, message: Message, entity_id: Union[str, int], media_type: str
    ) -> Optional[LazyMediaMetadata]:
        """
        Store media metadata for lazy loading instead of downloading.

        Args:
            message: Telegram message with media
            entity_id: Entity ID (chat/channel)
            media_type: Type of media (photo, video, etc.)

        Returns:
            LazyMediaMetadata if successful, None otherwise
        """
        try:
            if not hasattr(message, "media") or not message.media:
                return None

            # Extract basic metadata
            metadata = await self._extract_metadata(message, entity_id, media_type)
            if not metadata:
                return None

            # Store in memory cache
            assert metadata.lazy_load_token is not None
            self._metadata_cache[metadata.lazy_load_token] = metadata

            # Save to disk
            await self._save_metadata(metadata)

            logger.debug(
                f"Stored lazy metadata for message {message.id}: {metadata.file_name}"
            )
            return metadata

        except Exception as e:
            logger.error(
                f"Failed to store media metadata for message {message.id}: {e}"
            )
            return None

    async def create_placeholder(
        self, metadata: LazyMediaMetadata
    ) -> LazyLoadPlaceholder:
        """
        Create a placeholder for the lazy-loaded media.

        Args:
            metadata: Media metadata

        Returns:
            Placeholder for markdown
        """
        description = self._create_description(metadata)
        assert metadata.lazy_load_token is not None
        placeholder = LazyLoadPlaceholder(
            token=metadata.lazy_load_token,
            media_type=metadata.media_type,
            file_name=metadata.file_name,
            file_size=metadata.file_size,
            description=description,
        )

        # Cache placeholder
        self._placeholders_cache[metadata.lazy_load_token] = placeholder

        return placeholder

    async def download_lazy_media(
        self, token: str, output_dir: Path, progress_callback=None
    ) -> Optional[Path]:
        """
        Download media that was previously stored as lazy metadata.

        Args:
            token: Lazy load token
            output_dir: Directory to save the downloaded file
            progress_callback: Optional progress callback

        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            # Get metadata
            metadata = await self.get_metadata(token)
            if not metadata:
                logger.error(f"No metadata found for token: {token}")
                return None

            # Check if already downloaded and cached
            if self.cache_manager:
                cached_path = await self._check_download_cache(metadata, output_dir)
                if cached_path:
                    return cached_path

            # Create output path
            output_path = output_dir / metadata.file_name
            output_path.parent.mkdir(exist_ok=True)

            # For now, we'll need to reconstruct the message or use stored info
            # This is a simplified implementation - in practice, we'd need more context
            logger.warning(f"Lazy download not fully implemented for token: {token}")
            logger.info(
                f"Would download {metadata.file_name} ({metadata.file_size} bytes)"
            )

            # TODO: Implement actual download using stored telegram_file_id or message reconstruction
            # This would require storing more information or having access to the original message

            return None

        except Exception as e:
            logger.error(f"Failed to download lazy media for token {token}: {e}")
            return None

    async def get_metadata(self, token: str) -> Optional[LazyMediaMetadata]:
        """
        Get metadata for a lazy load token.

        Args:
            token: Lazy load token

        Returns:
            Metadata if found, None otherwise
        """
        # Check memory cache first
        if token in self._metadata_cache:
            return self._metadata_cache[token]

        # Try to load from disk
        metadata = await self._load_metadata(token)
        if metadata:
            self._metadata_cache[token] = metadata
            return metadata

        return None

    async def get_placeholder(self, token: str) -> Optional[LazyLoadPlaceholder]:
        """
        Get placeholder for a lazy load token.

        Args:
            token: Lazy load token

        Returns:
            Placeholder if found, None otherwise
        """
        if token in self._placeholders_cache:
            return self._placeholders_cache[token]

        # Get metadata and create placeholder
        metadata = await self.get_metadata(token)
        if metadata:
            placeholder = await self.create_placeholder(metadata)
            return placeholder

        return None

    async def list_pending_downloads(
        self,
        entity_id: Optional[Union[str, int]] = None,
        media_type: Optional[str] = None,
    ) -> List[LazyMediaMetadata]:
        """
        List all pending lazy downloads, optionally filtered.

        Args:
            entity_id: Filter by entity ID
            media_type: Filter by media type

        Returns:
            List of metadata for pending downloads
        """
        all_metadata = []

        # Load all metadata files
        for metadata_file in self.metadata_dir.glob("*.json"):
            try:
                import aiofiles

                async with aiofiles.open(metadata_file, "r", encoding="utf-8") as f:
                    data = json.loads(await f.read())
                    metadata = LazyMediaMetadata.from_dict(data)
                    all_metadata.append(metadata)
            except Exception as e:
                logger.warning(f"Failed to load metadata from {metadata_file}: {e}")

        # Apply filters
        filtered = all_metadata
        if entity_id is not None:
            filtered = [m for m in filtered if m.entity_id == entity_id]
        if media_type is not None:
            filtered = [m for m in filtered if m.media_type == media_type]

        return filtered

    async def cleanup_old_metadata(self, days_old: int = 30) -> int:
        """
        Clean up old metadata files.

        Args:
            days_old: Remove metadata older than this many days

        Returns:
            Number of files removed
        """
        cutoff_time = time.time() - (days_old * 24 * 60 * 60)
        removed_count = 0

        for metadata_file in self.metadata_dir.glob("*.json"):
            try:
                # Check file modification time
                stat = metadata_file.stat()
                if stat.st_mtime < cutoff_time:
                    metadata_file.unlink()
                    removed_count += 1

                    # Remove from cache if loaded
                    token = metadata_file.stem
                    self._metadata_cache.pop(token, None)
                    self._placeholders_cache.pop(token, None)

            except Exception as e:
                logger.warning(f"Failed to cleanup {metadata_file}: {e}")

        logger.info(f"Cleaned up {removed_count} old metadata files")
        return removed_count

    async def _extract_metadata(
        self, message: Message, entity_id: Union[str, int], media_type: str
    ) -> Optional[LazyMediaMetadata]:
        """Extract metadata from message."""
        try:
            if not hasattr(message, "file") or not message.file:
                return None

            file = message.file

            # Basic metadata
            metadata = LazyMediaMetadata(
                message_id=message.id,
                entity_id=entity_id,
                media_type=media_type,
                file_name=getattr(file, "name", f"msg_{message.id}"),
                file_size=getattr(file, "size", 0),
                mime_type=getattr(file, "mime_type", None),
                telegram_file_id=getattr(file, "id", None),
            )

            # Extract additional attributes based on media type
            if hasattr(file, "attributes"):
                for attr in file.attributes:
                    if hasattr(attr, "w") and hasattr(attr, "h"):
                        metadata.width = attr.w
                        metadata.height = attr.h
                    if hasattr(attr, "duration"):
                        metadata.duration = attr.duration

            return metadata

        except Exception as e:
            logger.error(f"Failed to extract metadata: {e}")
            return None

    def _create_description(self, metadata: LazyMediaMetadata) -> str:
        """Create human-readable description for placeholder."""
        desc_parts = []

        if metadata.media_type:
            desc_parts.append(metadata.media_type.title())

        if metadata.width and metadata.height:
            desc_parts.append(f"{metadata.width}x{metadata.height}")

        if metadata.duration:
            minutes = int(metadata.duration // 60)
            seconds = int(metadata.duration % 60)
            desc_parts.append(f"{minutes}:{seconds:02d}")

        desc_parts.append(metadata.file_name)

        return " ".join(desc_parts)

    async def _save_metadata(self, metadata: LazyMediaMetadata):
        """Save metadata to disk."""
        try:
            import aiofiles

            metadata_file = self.metadata_dir / f"{metadata.lazy_load_token}.json"
            data = metadata.to_dict()

            async with aiofiles.open(metadata_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))

        except Exception as e:
            logger.error(
                f"Failed to save metadata for token {metadata.lazy_load_token}: {e}"
            )

    async def _load_metadata(self, token: str) -> Optional[LazyMediaMetadata]:
        """Load metadata from disk."""
        try:
            import aiofiles

            metadata_file = self.metadata_dir / f"{token}.json"
            if not metadata_file.exists():
                return None

            async with aiofiles.open(metadata_file, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
                return LazyMediaMetadata.from_dict(data)

        except Exception as e:
            logger.error(f"Failed to load metadata for token {token}: {e}")
            return None

    async def _check_download_cache(
        self, metadata: LazyMediaMetadata, output_dir: Path
    ) -> Optional[Path]:
        """Check if media is already downloaded in cache."""
        if not self.cache_manager:
            return None

        # This would need to be implemented based on cache manager interface
        # For now, just check if file exists in output dir
        output_path = output_dir / metadata.file_name
        if output_path.exists():
            return output_path

        return None
