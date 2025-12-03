import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from opentele.api import API
from opentele.td import TDesktop
from telethon import TelegramClient

logger = logging.getLogger(__name__)


class TDesktopManager:
    """
    Manages conversion of Telegram Desktop (tdata) sessions to Telethon sessions.
    """

    @staticmethod
    def convert_tdata(
        tdata_path: str, session_path: str, api_id: int, api_hash: str
    ) -> TelegramClient:
        """
        Converts a tdata folder to a Telethon session file.

        Args:
            tdata_path: Path to the 'tdata' folder (or the folder containing it)
            session_path: Path where the .session file should be saved
            api_id: Telegram API ID
            api_hash: Telegram API Hash

        Returns:
            A connected TelegramClient instance
        """
        tdata_path_obj = Path(tdata_path)

        # Handle case where user points to the parent folder of tdata
        if tdata_path_obj.name != "tdata" and (tdata_path_obj / "tdata").exists():
            tdata_path_obj = tdata_path_obj / "tdata"

        if not tdata_path_obj.exists():
            raise FileNotFoundError(f"tdata path not found: {tdata_path}")

        logger.info(f"🔄 Converting tdata from {tdata_path_obj}...")

        try:
            # Load TDesktop session
            tdesk = TDesktop(str(tdata_path_obj))

            # Check if session is loaded
            if not tdesk.isLoaded():
                raise ValueError(
                    "Failed to load TDesktop session. Is the path correct?"
                )

            # Create API object for opentele
            api = API.TelegramDesktop.Generate(
                api_id=api_id,
                api_hash=api_hash,
                system_lang_code="en-US",
                lang_code="en",
            )

            # Convert to Telethon client
            # We use the session_path provided. opentele expects the session name/path.
            # If session_path ends with .session, we strip it because Telethon adds it back usually,
            # but opentele might handle it differently. Let's be safe.

            # opentele's ToTelethon returns a client.
            # It saves the session to the path specified.

            client = tdesk.ToTelethon(
                session=session_path,
                flag=None,  # Use default flag
                api=api,
            )

            logger.info(f"✅ tdata converted successfully to {session_path}")
            return client

        except Exception as e:
            logger.error(f"❌ Failed to convert tdata: {e}")
            raise

    @staticmethod
    def is_tdata(path: str) -> bool:
        """Checks if the path looks like a valid tdata folder."""
        p = Path(path)
        if not p.exists():
            return False

        # Check for key_datas (typical tdata structure)
        # Or if it's a folder named tdata
        if p.name == "tdata":
            return True
        if (p / "tdata").exists():
            return True

        # Check for key files inside
        if (p / "key_datas").exists():
            return True

        return False
