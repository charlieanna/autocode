"""Compatibility imports; planning lives in units.autoplanner."""

try:
    from .units.autoplanner import *  # noqa: F403 - compatibility re-export
except ImportError:
    from units.autoplanner import *  # noqa: F403 - compatibility re-export
