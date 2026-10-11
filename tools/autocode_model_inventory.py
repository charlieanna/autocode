"""Reuse one successful new-run model roster for its immediate route check.

This object belongs to one CLI invocation, never to run state or a provider
module. Validation consumes its observation. Failed and unsupported listings
are not reused, and any later check asks the provider again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class InvocationInventory:
    def __init__(self, provider: Any, workspace: Path):
        self.provider = provider
        self.workspace = Path(workspace).resolve()
        self._models: frozenset[str] | None = None

    def available_models(self, workspace):
        # A newer observation, including failure, supersedes any earlier one.
        self._models = None
        lister = getattr(self.provider, "available_models", None)
        if lister is None:
            return None
        models = lister(workspace)
        if Path(workspace).resolve() == self.workspace and models is not None:
            self._models = frozenset(models)
            return set(self._models)
        return models

    def check_models(self, roles):
        models, self._models = self._models, None
        if models is None:
            self.provider.check_models(roles, self.workspace)
        else:
            self.provider.check_models(roles, self.workspace, inventory=set(models))
