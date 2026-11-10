"""The runner-written files a run recorded, and which of them still hold what it wrote.

Shared by the package-write check (autocode_package_writes, #711) and the atomic delivery scope
check (autocode_git_delivery): a recorded runner file whose content is unchanged is the runner's own
evidence — a review or design review report an earlier runtime saved, a design check's blockers
file — not a Builder edit or a workspace change to refuse. A recorded one whose content
changed (or that is gone) is not what the runner wrote and stays refused. Files an earlier, finished
turn recorded by path only are not handled here (autocode_package_writes's ``_by_path``): an edit to
one is not seen.

It imports nothing above it. State read: ``review``, ``design_review`` and ``design_check`` (the
recorded path/hash rows). An earlier runtime's bug-fix diagnosis note is not one of them: nothing
left can tell whether it still holds what that runtime wrote, so it counts as any other change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

# The runner-written files whose content the run recorded: (state key, path field, hash field). The
# design check's blockers joined with #711's hash; rows saved without one stay path-only (``_by_path``).
REPORTS = (
    ("review", "report_path", "report_sha256"),
    ("design_review", "report_path", "report_sha256"),
    ("design_check", "blockers", "blockers_sha256"),
)


def runner_files(state: RunState, workspace) -> tuple[set[str], set[str]]:
    """(the runner-written files whose content the run recorded, those of them that are unchanged)."""
    recorded, unchanged = set(), set()
    for key, path_field, hash_field in REPORTS:
        row = state.get(key)
        path, saved = (row.get(path_field), row.get(hash_field)) if isinstance(row, dict) else (None, None)
        if not (isinstance(path, str) and path.strip() and saved):
            continue
        recorded.add(path)
        target = Path(workspace, path)
        try:
            if not target.is_symlink() and target.is_file() and util.file_hash(target) == saved:
                unchanged.add(path)
        except OSError:
            pass
    return recorded, unchanged
