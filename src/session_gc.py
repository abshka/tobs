"""
Session Garbage Collection - automatically cleanup old Telegram session files.

Part of TIER A - Task 6: Session GC Implementation
"""
import os
import time
from pathlib import Path
from typing import List, Tuple

from src.utils import logger


class SessionGC:
    """
    Garbage collector for Telegram session files.
    
    Automatically removes old session files to prevent accumulation and
    security risks from stale credentials.
    
    Features:
    - Configurable retention policy (max age in days)
    - Keeps N most recent sessions as backup
    - Preserves active session
    - Safe cleanup with error handling
    """
    
    def __init__(
        self,
        session_dir: str = "sessions",
        max_age_days: int = 30,
        keep_last_n: int = 3,
        active_session_name: str = None
    ):
        """
        Initialize SessionGC.
        
        Args:
            session_dir: Directory containing session files
            max_age_days: Remove sessions older than this many days
            keep_last_n: Always keep N most recent sessions
            active_session_name: Name of active session to preserve
        """
        self.session_dir = Path(session_dir)
        self.max_age_days = max_age_days
        self.keep_last_n = keep_last_n
        self.active_session_name = active_session_name
        
    def scan_sessions(self) -> List[Tuple[Path, float]]:
        """
        Scan session directory and return list of (file_path, mtime) tuples.
        
        Returns:
            List of (Path, timestamp) sorted by modification time (newest first)
        """
        if not self.session_dir.exists():
            logger.warning(f"Session directory does not exist: {self.session_dir}")
            return []
            
        sessions = []
        
        # Find all .session files (excluding -journal files)
        for file_path in self.session_dir.glob("*.session"):
            if file_path.name.endswith("-journal"):
                continue
                
            try:
                mtime = file_path.stat().st_mtime
                sessions.append((file_path, mtime))
            except OSError as e:
                logger.warning(f"Failed to stat {file_path}: {e}")
                
        # Sort by modification time (newest first)
        sessions.sort(key=lambda x: x[1], reverse=True)
        
        return sessions
        
    def should_remove(self, file_path: Path, mtime: float, index: int) -> bool:
        """
        Determine if a session file should be removed.
        
        Args:
            file_path: Path to session file
            mtime: Modification timestamp
            index: Position in sorted list (0 = newest)
            
        Returns:
            True if file should be removed
        """
        # Never remove active session
        if self.active_session_name and file_path.stem == self.active_session_name:
            logger.debug(f"Preserving active session: {file_path.name}")
            return False
            
        # Always keep N most recent sessions
        if index < self.keep_last_n:
            logger.debug(f"Preserving recent session (#{index}): {file_path.name}")
            return False
            
        # Remove if older than max_age_days
        age_days = (time.time() - mtime) / 86400
        if age_days > self.max_age_days:
            logger.debug(f"Marking for removal (age: {age_days:.1f} days): {file_path.name}")
            return True
            
        return False
        
    def cleanup(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Execute garbage collection.
        
        Args:
            dry_run: If True, only report what would be deleted
            
        Returns:
            Tuple of (files_removed, errors)
        """
        sessions = self.scan_sessions()
        
        if not sessions:
            logger.info("No session files found for cleanup")
            return (0, 0)
            
        logger.info(f"Found {len(sessions)} session files")
        
        files_removed = 0
        errors = 0
        
        for index, (file_path, mtime) in enumerate(sessions):
            if not self.should_remove(file_path, mtime, index):
                continue
                
            age_days = (time.time() - mtime) / 86400
            
            if dry_run:
                logger.info(f"[DRY RUN] Would remove: {file_path.name} (age: {age_days:.1f} days)")
                files_removed += 1
            else:
                try:
                    # Also remove associated -journal file if exists
                    journal_file = file_path.with_suffix(".session-journal")
                    
                    logger.info(f"Removing old session: {file_path.name} (age: {age_days:.1f} days)")
                    file_path.unlink()
                    files_removed += 1
                    
                    if journal_file.exists():
                        journal_file.unlink()
                        logger.debug(f"Removed journal: {journal_file.name}")
                        
                except OSError as e:
                    logger.error(f"Failed to remove {file_path}: {e}")
                    errors += 1
                    
        if dry_run:
            logger.info(f"[DRY RUN] Would remove {files_removed} session files")
        else:
            logger.info(f"✅ Session GC complete: removed {files_removed} files, {errors} errors")
            
        return (files_removed, errors)


def run_session_gc(
    session_dir: str = "sessions",
    max_age_days: int = 30,
    keep_last_n: int = 3,
    active_session_name: str = None,
    dry_run: bool = False
) -> Tuple[int, int]:
    """
    Convenience function to run session garbage collection.
    
    Args:
        session_dir: Directory containing session files
        max_age_days: Remove sessions older than this many days
        keep_last_n: Always keep N most recent sessions
        active_session_name: Name of active session to preserve
        dry_run: If True, only report what would be deleted
        
    Returns:
        Tuple of (files_removed, errors)
        
    Example:
        # In main.py before connecting
        from src.session_gc import run_session_gc
        
        removed, errors = run_session_gc(
            session_dir="sessions",
            max_age_days=30,
            keep_last_n=3,
            active_session_name="tobs_session"
        )
    """
    gc = SessionGC(
        session_dir=session_dir,
        max_age_days=max_age_days,
        keep_last_n=keep_last_n,
        active_session_name=active_session_name
    )
    
    return gc.cleanup(dry_run=dry_run)
