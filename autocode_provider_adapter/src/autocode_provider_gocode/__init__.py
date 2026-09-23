"""GoCode provider plug-in for Autocode's provider registry."""
from __future__ import annotations

from autocode_provider_adapter.compatibility import CompatibilityManifest
from autocode_provider_adapter.runtime import GoCodeFacade
from autocode_provider_adapter.transport import GoCodeTransport


def create_provider():
    """Return the facade consumed by ``tools.autocode_providers``."""
    return GoCodeFacade(GoCodeTransport(CompatibilityManifest.default()))
