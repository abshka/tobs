# This file makes the 'handlers' directory a Python package.
# It can also be used to make submodules or symbols available at the package level.

# We anticipate having handler modules like common_handlers.py and export_handlers.py.
# By importing them here, they become part of the 'handlers' package namespace,
# allowing imports like `from bot.handlers import common_handlers`.

# We'll define what gets imported when `from .handlers import *` is used.
# These will be the actual handler modules.

from . import common_handlers
from . import export_handlers

__all__ = [
    'common_handlers',
    'export_handlers',
]