"""Compatibility import for integrations that still import OpenCode directly.

New Autocode code resolves transports through :mod:`tools.autocode_providers`.
"""
try:
    from .providers.opencode import *  # noqa: F401,F403
except ImportError:  # Script-style execution from tools/ remains supported.
    from providers.opencode import *  # noqa: F401,F403
