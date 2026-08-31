"""Compatibility module alias for the Open Montage integration."""

import sys

from app.integrations.providers import open_montage_bridge as _implementation

sys.modules[__name__] = _implementation
