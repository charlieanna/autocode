"""Identify linear Builder commits from runner-observed source captures.

No source or ref writes and no model claims: every edge from the launch base to HEAD
must occur inside a successfully recorded Builder attempt for this plan.
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


def builder_commits(state: RunState, workspace, current, git):
    """Return witnessed commits, refusing dirty trees, merges and external moves."""
    base, head = state.get("base_commit"), current["head"]
    if base == head:
        return []
    reason = "Moved HEAD lacks a clean, runner-witnessed Builder commit chain; preserve Git state for reconciliation"
    if not base or git(workspace, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError(reason)
    if (
        state.get("task_branch")
        and git(workspace, "symbolic-ref", "--short", "HEAD").decode().strip() != state["task_branch"]
    ):
        raise ValueError(reason)
    rows = [
        line.split()
        for line in git(workspace, "rev-list", "--reverse", "--parents", base + ".." + head).decode().splitlines()
    ]
    chain = [base]
    for row in rows:
        if row[1:] != [chain[-1]]:
            raise ValueError(reason)
        chain.append(row[0])
    if chain[-1] != head:
        raise ValueError(reason)
    witnessed = set()
    root = Path(state["run_dir"]).resolve()
    for record in state.get("stages", []):
        if (
            record.get("stage") != "terra"
            or record.get("exit_code") != 0
            or any(record.get(key) for key in ("report_only", "rejected", "abandoned", "timed_out"))
            or record.get("contract_hash") != state["goal_contract"]["hash"]
        ):
            continue
        try:
            captures = []
            for key in ("before_ref", "after_ref"):
                path = Path(record[key])
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError(reason)
                snapshot = util.read(path)
                if snapshot["revision"] != util.digest({"head": snapshot["head"], "files": snapshot["files"]}):
                    raise ValueError(reason)
                captures.append(snapshot)
            before, after = captures
            if record.get("source_revision") != after["revision"]:
                continue
            start, stop = chain.index(before["head"]), chain.index(after["head"])
            witnessed.update(range(start + 1, stop + 1))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if witnessed != set(range(1, len(chain))):
        raise ValueError(reason)
    return chain[1:]
