"""Compatibility module alias for the Ark protocol integration."""

import sys

from app.integrations.providers import ark_protocol_bridge as _implementation

sys.modules[__name__] = _implementation
