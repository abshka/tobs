"""
Shutdown manager for graceful application termination.
Coordinates cleanup across all subsystems.

Part of TIER A - Task 3: Graceful Shutdown Implementation
"""
import asyncio
import signal
import time
from typing import Callable, List, Optional

from rich import print as rprint

from src.utils import logger


class ShutdownManager:
    """
    Manages graceful shutdown process with two-stage Ctrl+C handling.
    
    Features:
    - Two-stage Ctrl+C (graceful → force)
    - Cleanup hook registration (sync and async)
    - Async-safe shutdown coordination
    
    Usage:
        # In main.py
        from src.shutdown_manager import shutdown_manager
        
        # Register signal handler
        signal.signal(signal.SIGINT, shutdown_manager.handle_sigint)
        
        # Register cleanup hooks
        shutdown_manager.register_cleanup_hook(some_sync_function)
        shutdown_manager.register_async_cleanup_hook(some_async_function)
        
        # In application loop
        if shutdown_manager.shutdown_requested:
            break  # Exit gracefully
            
        # Before exit
        await shutdown_manager.run_graceful_cleanup()
    """
    
    def __init__(self, force_shutdown_timeout: float = 5.0):
        """
        Initialize ShutdownManager.
        
        Args:
            force_shutdown_timeout: Seconds to wait before allowing force shutdown
        """
        self.shutdown_requested = False
        self.force_shutdown = False
        self.first_sigint_time: Optional[float] = None
        self.force_shutdown_timeout = force_shutdown_timeout
        self._cleanup_hooks: List[Callable] = []
        self._async_cleanup_hooks: List[Callable] = []
        logger.debug("ShutdownManager initialized")
        
    def register_cleanup_hook(self, hook: Callable) -> None:
        """
        Register synchronous cleanup function.
        
        Hook will be called during graceful shutdown in registration order.
        
        Args:
            hook: Callable with no arguments (use lambda for parameters)
        """
        self._cleanup_hooks.append(hook)
        logger.debug(f"Registered cleanup hook: {hook.__name__}")
        
    def register_async_cleanup_hook(self, hook: Callable) -> None:
        """
        Register async cleanup coroutine.
        
        Hook will be awaited during graceful shutdown in registration order.
        
        Args:
            hook: Async callable with no arguments (use lambda for parameters)
        """
        self._async_cleanup_hooks.append(hook)
        logger.debug(f"Registered async cleanup hook: {hook.__name__}")
        
    def handle_sigint(self, signum: int, frame) -> None:
        """
        Handle SIGINT signal with two-stage shutdown logic.
        
        First Ctrl+C: Graceful shutdown (set flag, continue cleanup)
        Second Ctrl+C (within timeout): Force shutdown (immediate exit)
        
        Args:
            signum: Signal number
            frame: Current stack frame
        """
        if not self.shutdown_requested:
            # First Ctrl+C - initiate graceful shutdown
            self.shutdown_requested = True
            self.first_sigint_time = time.time()
            
            rprint("\n[bold yellow]⏸️  Graceful shutdown initiated...[/bold yellow]")
            rprint("[cyan]ℹ️  Finishing current operations and cleaning up...[/cyan]")
            rprint(f"[cyan]ℹ️  Press Ctrl+C again within {self.force_shutdown_timeout}s to force shutdown.[/cyan]")
            
            logger.info("🛑 Graceful shutdown requested (first SIGINT)")
            
        else:
            # Second Ctrl+C - check if within timeout window
            elapsed = time.time() - (self.first_sigint_time or 0)
            
            if elapsed < self.force_shutdown_timeout:
                # Force shutdown
                self.force_shutdown = True
                
                rprint("\n[bold red]⚠️  FORCE SHUTDOWN - immediate exit![/bold red]")
                logger.warning("💥 Force shutdown requested (second SIGINT within timeout)")
                
                # Minimal cleanup
                self._run_minimal_cleanup()
                
                # Immediate exit
                import sys
                sys.exit(1)
            else:
                # Timeout expired, treat as first Ctrl+C again
                rprint("[yellow]⏱️  Timeout expired, initiating new graceful shutdown...[/yellow]")
                logger.info("⏱️  Second SIGINT after timeout, restarting graceful shutdown")
                self.shutdown_requested = True
                self.first_sigint_time = time.time()
                
    def _run_minimal_cleanup(self) -> None:
        """
        Run minimal cleanup on force shutdown.
        
        Only executes critical cleanup operations that must not fail:
        - Flush log buffers
        - Close file handles (best effort)
        """
        try:
            # Flush logs immediately to preserve shutdown information
            from src.logging.global_batcher import global_batcher
            global_batcher.flush()
            logger.info("✅ Minimal cleanup: logs flushed")
        except Exception as e:
            # Can't log this, just print
            print(f"Warning: minimal cleanup error: {e}")
            
    async def run_graceful_cleanup(self) -> None:
        """
        Execute all registered cleanup hooks in order.
        
        Runs synchronous hooks first, then async hooks.
        Continues execution even if individual hooks fail.
        """
        if not self.shutdown_requested:
            logger.debug("Graceful cleanup called but shutdown not requested, skipping")
            return
            
        rprint("[cyan]🧹 Running graceful cleanup...[/cyan]")
        logger.info("🧹 Starting graceful cleanup sequence")
        
        # Run synchronous cleanup hooks
        for i, hook in enumerate(self._cleanup_hooks):
            try:
                hook_name = getattr(hook, '__name__', f'hook_{i}')
                logger.debug(f"Running sync cleanup hook: {hook_name}")
                hook()
                logger.debug(f"✅ Sync cleanup hook completed: {hook_name}")
            except Exception as e:
                logger.error(f"❌ Sync cleanup hook failed: {e}", exc_info=True)
                rprint(f"[yellow]⚠️  Cleanup warning: {hook_name} failed - {e}[/yellow]")
                
        # Run async cleanup hooks
        for i, hook in enumerate(self._async_cleanup_hooks):
            try:
                hook_name = getattr(hook, '__name__', f'async_hook_{i}')
                logger.debug(f"Running async cleanup hook: {hook_name}")
                await hook()
                logger.debug(f"✅ Async cleanup hook completed: {hook_name}")
            except Exception as e:
                logger.error(f"❌ Async cleanup hook failed: {e}", exc_info=True)
                rprint(f"[yellow]⚠️  Cleanup warning: {hook_name} failed - {e}[/yellow]")
                
        rprint("[green]✅ Graceful cleanup complete[/green]")
        logger.info("✅ Graceful cleanup sequence finished")


# Global singleton instance
shutdown_manager = ShutdownManager()
