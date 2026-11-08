"""Bounded current-session UI contexts, keyed by public workspace/run identity.

These are display and unsent draft data, never a cached action authority. A
switch always reads current status before offering or taking a core decision.
"""

from collections import OrderedDict
from pathlib import Path

try:
    from . import autocode_scratch_projects as scratch_projects
except ImportError:
    import autocode_scratch_projects as scratch_projects

LIMIT = 32


def identity(workspace, run_dir):
    return str(workspace), str(run_dir) if run_dir is not None else None


def label(workspace, run_dir=None) -> str:
    """The chat's place as the header names it: the person's folder, never a private project's random id."""
    text = (scratch_projects.chosen(workspace) or Path(workspace)).name
    return f"{text} · {Path(run_dir).name}" if run_dir is not None else text


def draft_identity(view):
    """The core's opaque chat context binds a draft to the question it was typed for."""
    if not isinstance(view, dict):
        return None
    context = (view.get("chat") or {}).get("context")
    if not isinstance(context, str) or not context:
        return None
    return view.get("workspace"), view.get("run_dir"), context


class Contexts:
    def __init__(self, limit=LIMIT):
        self.limit = limit
        self.rows = OrderedDict()

    def keep(self, key, value):
        self.rows.pop(key, None)
        self.rows[key] = value
        while len(self.rows) > self.limit:
            self.rows.popitem(last=False)

    def get(self, key):
        value = self.rows.pop(key, None)
        if value is not None:
            self.rows[key] = value
        return value
