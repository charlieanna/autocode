"""Validate source citations using the caller's existing workspace reader.

The reader supplies inventory, path resolution and citation parsing. This module
owns the citation requirements and line bounds, without importing the controller
or planning unit.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import re
from pathlib import Path


def require(state: "RunState", refs, field="code_refs", *, planning_unit):
    if not state.get("workspace"):
        return
    root = Path(state.get("workspace") or "")
    if not root.is_dir():
        return
    files = planning_unit.workspace_inventory(root, state.get("task", ""))["files"]
    if not files:
        return
    if not refs:
        raise ValueError(f"Report must cite existing source in {field}")
    for ref in refs:
        raw, line_text = planning_unit.split_code_ref(root, str(ref))
        target = planning_unit.cited_file(root, raw, field, ref)
        if line_text:
            citation = re.fullmatch(r"(\d+)(?:-(\d+))?(?:\s+.*)?", line_text)
            if not citation:
                raise ValueError(f"{field} entry {ref} has no line number")
            line = int(citation[1])
            end = int(citation[2] or citation[1])
            count = len(target.read_text(errors="replace").splitlines())
            if line < 1 or end < line or end > max(count, 1):
                raise ValueError(f"{field} entry {ref} points past the end of the file ({count} lines)")
