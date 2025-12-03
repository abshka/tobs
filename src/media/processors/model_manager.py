"""
Minimal Whisper model manager for the project (single model: large-v3).

This module implements a lightweight model manager used by the UI and tests.
The project uses Systran/faster-whisper-large-v3 (Whisper Large V3). The manager
exposes the minimal API necessary for the UI: cache paths, availability checks,
info entries, deletion, and a simulated download.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

# Single supported model in this project
WHISPER_MODELS: Dict[str, Dict[str, Any]] = {
    "large-v3": {
        "size_mb": 2048,  # nominal/representative size
        "description": "Faster-Whisper Large V3 (Systran/faster-whisper-large-v3)",
    }
}


class WhisperModelManager:
    """
    Minimal Whisper model manager.

    The only intentionally supported model is 'large-v3'. The cache directory
    naming convention is `whisper-{model_name}` (e.g., whisper-large-v3).
    This manager is intentionally lightweight and synchronous (async download
    method is minimal and test-friendly).
    """

    def __init__(self, custom_cache_dir: Optional[Path] = None):
        # If a custom cache dir is provided, it takes precedence.
        self.custom_cache_dir = Path(custom_cache_dir) if custom_cache_dir else None
        self.hf_cache_dir = self._get_hf_cache_dir()

    def get_model_cache_path(self, model_name: str) -> Path:
        """
        Return the cache path for the given model name.

        If `custom_cache_dir` was provided at init time, the path will be:
            custom_cache_dir / f"whisper-{model_name}"

        Otherwise the path is:
            hf_cache_dir / f"whisper-{model_name}"
        """
        subdir = f"whisper-{model_name}"
        if self.custom_cache_dir:
            return self.custom_cache_dir / subdir
        return self.hf_cache_dir / subdir

    def is_model_available(self, model_name: str) -> bool:
        """
        Return True if the model is a known model and a `model.bin` file exists
        in the cache directory for that model.
        """
        if model_name not in WHISPER_MODELS:
            return False
        cache_path = self.get_model_cache_path(model_name)
        model_file = cache_path / "model.bin"
        return model_file.exists()

    def get_available_models(self) -> List[str]:
        """Return the list of recognized models that are available locally."""
        return [m for m in WHISPER_MODELS.keys() if self.is_model_available(m)]

    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        Return a small info dict for the model:

        {
            "is_available": bool,
            "size_mb": float,  # directory's size if it exists, otherwise 0
            "description": str,
            "cache_path": str
        }

        Returns None for unknown model names.
        """
        if model_name not in WHISPER_MODELS:
            return None
        cache_path = self.get_model_cache_path(model_name)
        is_available = self.is_model_available(model_name)
        size_mb = self._get_directory_size_mb(cache_path) if is_available else 0
        return {
            "is_available": is_available,
            "size_mb": size_mb,
            "description": WHISPER_MODELS[model_name]["description"],
            "cache_path": str(cache_path),
        }

    def get_models_info(self) -> Dict[str, Dict[str, Any]]:
        """Return a dictionary mapping each recognized model to its info dict."""
        return {name: self.get_model_info(name) or {} for name in WHISPER_MODELS.keys()}

    @staticmethod
    def _get_directory_size_mb(path: Path) -> float:
        """
        Recursively compute directory size in megabytes.

        If the path doesn't exist, returns 0.
        """
        if not path.exists():
            return 0.0
        total = 0
        for root, _, files in os.walk(path):
            for fname in files:
                try:
                    fp = Path(root) / fname
                    total += fp.stat().st_size
                except OSError:
                    # Skip files that can't be stat'ed for whatever reason
                    continue
        return total / (1024 * 1024)

    def get_total_cache_size_mb(self) -> float:
        """Return the sum of cache sizes for all recognized models (in MB)."""
        total = 0.0
        for name in WHISPER_MODELS.keys():
            total += self._get_directory_size_mb(self.get_model_cache_path(name))
        return total

    def delete_model(self, model_name: str) -> bool:
        """
        Delete the cached model directory (if present).

        Returns:
            False if model_name is invalid (not recognized).
            True if deletion succeeded or the model wasn't present.
        """
        if model_name not in WHISPER_MODELS:
            return False
        path = self.get_model_cache_path(model_name)
        try:
            if path.exists():
                shutil.rmtree(path)
            return True
        except OSError:
            return False

    async def download_model(self, model_name: str) -> bool:
        """
        Simulate download for the model.

        In tests, this creates the `model.bin` placeholder file in the cache
        directory if it does not already exist.

        Returns:
            False if model_name invalid.
            True on success.
        """
        import asyncio

        if model_name not in WHISPER_MODELS:
            return False
        path = self.get_model_cache_path(model_name)
        try:
            path.mkdir(parents=True, exist_ok=True)
            model_file = path / "model.bin"
            if not model_file.exists():
                # Create a small placeholder file to simulate a real model artifact.
                model_file.write_bytes(b"faster-whisper-large-v3-placeholder")
            # Keep the coroutine async so tests can `await` it
            await asyncio.sleep(0)
            return True
        except OSError:
            return False

    def cleanup_old_versions(self, model_name: str) -> int:
        """
        Cleanup older model versions in an HF cache path.

        Minimal behavior for tests: return 0 (no old versions removed).
        """
        if model_name not in WHISPER_MODELS:
            return 0
        return 0

    @staticmethod
    def _get_hf_cache_dir() -> Path:
        """
        Return a best-effort HuggingFace cache directory:

        Order of preference:
            - HF_HOME environment variable
            - XDG_CACHE_HOME/huggingface/hub
            - ~/.cache/huggingface/hub
        """
        hf_home = os.getenv("HF_HOME")
        if hf_home:
            return Path(hf_home)
        xdg = os.getenv("XDG_CACHE_HOME")
        if xdg:
            return Path(xdg) / "huggingface" / "hub"
        return Path.home() / ".cache" / "huggingface" / "hub"


__all__ = ["WHISPER_MODELS", "WhisperModelManager"]
