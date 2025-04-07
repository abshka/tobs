from pathlib import Path
from typing import Optional
from src.config import Config
from src.cache_manager import CacheManager
from src.utils import logger, get_relative_path
import aiofiles
import asyncio

class ReplyLinker:
    def __init__(self, config: Config, cache_manager: CacheManager):
        self.config = config
        self.cache_manager = cache_manager

    async def link_replies(self):
        """Iterates through cached messages and adds reply links to parent notes."""
        logger.info("Starting reply linking process...")
        processed_messages = self.cache_manager.get_all_processed_messages()
        link_tasks = []

        # We iterate through messages that *have* a reply_to field
        for child_id_str, msg_data in processed_messages.items():
            parent_id = msg_data.get("reply_to")
            if parent_id:
                parent_id_str = str(parent_id)
                # Check if the parent message was also processed and exists in our vault
                if parent_id_str in processed_messages:
                    parent_filename = processed_messages[parent_id_str].get("filename")
                    child_filename = msg_data.get("filename")

                    if parent_filename and child_filename:
                        parent_note_path = self._find_note_path(parent_filename)
                        child_note_path = self._find_note_path(child_filename)

                        if parent_note_path and child_note_path and parent_note_path.exists():
                            # Schedule the linking task
                            link_tasks.append(
                                self._add_reply_link(child_note_path, parent_note_path)
                            )
                        else:
                            logger.warning(f"Could not find note files for reply link: Parent={parent_filename}, Child={child_filename}. Skipping link.")
                            # Optionally add "Reply to Unresolved" to the *child* note here if desired
                            await self._add_unresolved_reply_link(child_filename)

                    else:
                         logger.warning(f"Filename missing in cache for reply pair: Parent ID {parent_id_str}, Child ID {child_id_str}")
                         await self._add_unresolved_reply_link(child_filename) # Add unresolved to child
                else:
                    # Parent message wasn't processed (e.g., deleted, outside scope)
                    logger.warning(f"Parent message {parent_id} for reply {child_id_str} not found in processed cache. Marking as unresolved.")
                    child_filename = msg_data.get("filename")
                    await self._add_unresolved_reply_link(child_filename) # Add unresolved to child


        if link_tasks:
            logger.info(f"Attempting to add {len(link_tasks)} reply links...")
            await asyncio.gather(*link_tasks)
        else:
            logger.info("No reply links to process.")

        logger.info("Reply linking process finished.")

    def _find_note_path(self, filename: str) -> Optional[Path]:
        """Finds the full path of a note given its filename (including year)."""
        # Assumes filename format YYYY.MM.DD.Title.md
        try:
            year = filename.split('.')[0]
            if year.isdigit() and len(year) == 4:
                path = self.config.obsidian_path / year / filename
                # Check existence? The caller checks existence.
                return path
            else:
                logger.error(f"Could not extract year from filename: {filename}")
                return None
        except Exception as e:
            logger.error(f"Error parsing filename {filename} to find path: {e}")
            return None


    async def _add_reply_link(self, parent_note_path: Path, child_note_path: Path):
        """Adds a 'Reply to [[child]]' link to the beginning of the parent note."""
        try:
            # Calculate relative path for the link from parent's directory
            relative_child_path = get_relative_path(child_note_path, parent_note_path)
            # Obsidian link format: [[relative/path/to/note]] or [[note_name_without_ext]]
            # Using relative path is more robust if notes are moved within the vault structure.
            # Let's use the filename without extension for cleaner Obsidian links if they are in the same folder,
            # otherwise use relative path. For simplicity and robustness across folders, use relative path.
            link_target = relative_child_path.replace('.md', '') # Obsidian often resolves without .md
            reply_line = f"Reply to [[{link_target}]]\n"

            async with aiofiles.open(parent_note_path, mode='r+', encoding='utf-8') as f:
                content = await f.read()
                if not content.startswith("Reply to"): # Avoid adding duplicate links
                    logger.debug(f"Adding reply link to {parent_note_path.name} pointing to {child_note_path.name}")
                    await f.seek(0)
                    await f.write(reply_line + content)
                    await f.truncate() # Ensure file is truncated if new content is shorter (unlikely here)
                else:
                    logger.debug(f"Reply link already exists in {parent_note_path.name}. Skipping.")

        except Exception as e:
            logger.error(f"Failed to add reply link to {parent_note_path}: {e}", exc_info=self.config.verbose)


    async def _add_unresolved_reply_link(self, child_filename: Optional[str]):
        """Adds 'Reply to Unresolved' to the beginning of the child note if the parent is missing."""
        if not child_filename:
            logger.warning("Cannot add unresolved reply link: child filename is missing.")
            return

        child_note_path = self._find_note_path(child_filename)
        if not child_note_path or not child_note_path.exists():
            logger.warning(f"Cannot add unresolved reply link: child note path not found or doesn't exist ({child_filename}).")
            return

        try:
            reply_line = "Reply to Unresolved\n\n---\n" # Add separator
            async with aiofiles.open(child_note_path, mode='r+', encoding='utf-8') as f:
                content = await f.read()
                if not content.startswith("Reply to"): # Avoid adding duplicate links
                    logger.debug(f"Adding 'Reply to Unresolved' to {child_note_path.name}")
                    await f.seek(0)
                    await f.write(reply_line + content)
                    await f.truncate()
                else:
                     logger.debug(f"'Reply to' line already exists in {child_note_path.name}. Skipping unresolved.")

        except Exception as e:
            logger.error(f"Failed to add 'Reply to Unresolved' to {child_note_path}: {e}", exc_info=self.config.verbose)
