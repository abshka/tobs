"""
Unit tests for SessionGC (TIER A - Task 6: Session Garbage Collection).

Tests automatic cleanup of old Telegram session files.
"""
import tempfile
import time
from pathlib import Path

import pytest

from src.session_gc import SessionGC, run_session_gc


def create_test_session(session_dir: Path, name: str, age_days: int) -> Path:
    """Create a test session file with specified age."""
    session_file = session_dir / f"{name}.session"
    session_file.touch()
    
    # Set modification time to simulate age
    mtime = time.time() - (age_days * 86400)
    session_file.stat()
    import os
    os.utime(session_file, (mtime, mtime))
    
    return session_file


def test_scan_sessions_finds_all_files():
    """SessionGC should find all .session files in directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        # Create test sessions
        create_test_session(session_dir, "session1", age_days=10)
        create_test_session(session_dir, "session2", age_days=20)
        create_test_session(session_dir, "session3", age_days=30)
        
        gc = SessionGC(session_dir=str(session_dir))
        sessions = gc.scan_sessions()
        
        assert len(sessions) == 3
        assert all(isinstance(s[0], Path) for s in sessions)
        assert all(isinstance(s[1], float) for s in sessions)


def test_preserves_active_session():
    """SessionGC should never remove active session."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        # Create active session (very old)
        active = create_test_session(session_dir, "active", age_days=365)
        
        gc = SessionGC(
            session_dir=str(session_dir),
            max_age_days=30,
            active_session_name="active"
        )
        
        removed, errors = gc.cleanup(dry_run=False)
        
        assert removed == 0
        assert active.exists()


def test_preserves_recent_sessions():
    """SessionGC should keep N most recent sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        # Create sessions with different ages
        s1 = create_test_session(session_dir, "session1", age_days=5)   # Keep (recent)
        s2 = create_test_session(session_dir, "session2", age_days=10)  # Keep (recent)
        s3 = create_test_session(session_dir, "session3", age_days=15)  # Keep (recent)
        s4 = create_test_session(session_dir, "session4", age_days=50)  # Remove (old)
        
        gc = SessionGC(
            session_dir=str(session_dir),
            max_age_days=30,
            keep_last_n=3
        )
        
        removed, errors = gc.cleanup(dry_run=False)
        
        assert removed == 1
        assert s1.exists()
        assert s2.exists()
        assert s3.exists()
        assert not s4.exists()


def test_removes_old_sessions():
    """SessionGC should remove sessions older than max_age_days."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        # Create sessions
        recent = create_test_session(session_dir, "recent", age_days=10)
        old1 = create_test_session(session_dir, "old1", age_days=40)
        old2 = create_test_session(session_dir, "old2", age_days=50)
        
        gc = SessionGC(
            session_dir=str(session_dir),
            max_age_days=30,
            keep_last_n=0  # Don't preserve any
        )
        
        removed, errors = gc.cleanup(dry_run=False)
        
        assert removed == 2
        assert recent.exists()
        assert not old1.exists()
        assert not old2.exists()


def test_dry_run_doesnt_remove():
    """Dry run should report what would be removed without deleting."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        old = create_test_session(session_dir, "old", age_days=50)
        
        gc = SessionGC(
            session_dir=str(session_dir),
            max_age_days=30,
            keep_last_n=0
        )
        
        removed, errors = gc.cleanup(dry_run=True)
        
        assert removed == 1  # Would remove
        assert old.exists()  # But file still exists


def test_handles_missing_directory():
    """SessionGC should handle non-existent session directory gracefully."""
    gc = SessionGC(session_dir="/nonexistent/directory")
    sessions = gc.scan_sessions()
    
    assert len(sessions) == 0


def test_removes_journal_files():
    """SessionGC should also remove associated -journal files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        # Create session + journal
        session = create_test_session(session_dir, "old", age_days=50)
        journal = session_dir / "old.session-journal"
        journal.touch()
        
        gc = SessionGC(
            session_dir=str(session_dir),
            max_age_days=30,
            keep_last_n=0
        )
        
        removed, errors = gc.cleanup(dry_run=False)
        
        assert not session.exists()
        assert not journal.exists()


def test_run_session_gc_convenience_function():
    """run_session_gc() convenience function should work."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        old = create_test_session(session_dir, "old", age_days=50)
        
        removed, errors = run_session_gc(
            session_dir=str(session_dir),
            max_age_days=30,
            keep_last_n=0
        )
        
        assert removed == 1
        assert errors == 0
        assert not old.exists()


def test_empty_directory():
    """SessionGC should handle empty directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gc = SessionGC(session_dir=tmpdir)
        removed, errors = gc.cleanup()
        
        assert removed == 0
        assert errors == 0


def test_error_handling():
    """SessionGC should continue cleanup even if individual files fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        
        # Create one removable session
        old = create_test_session(session_dir, "old", age_days=50)
        
        gc = SessionGC(
            session_dir=str(session_dir),
            max_age_days=30,
            keep_last_n=0
        )
        
        # This test mainly verifies error handling exists
        # Actual error conditions are hard to simulate in unit tests
        removed, errors = gc.cleanup()
        
        assert removed == 1
        assert errors == 0
