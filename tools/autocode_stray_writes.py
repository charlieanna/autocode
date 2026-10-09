"""A read-only stage that changed the repository: reject it, and put the files back.

Review, design review, design check, discussion and both Investigators may not change
the repository. When one does, its job raises StrayWrites. A report repair cannot undo a
file the attempt wrote, so the runner never spends one on it; it restores the files from
the attempt's before-snapshot and lets the normal retry path (the Investigator) run on
a clean workspace.

A live review (Claude models, 2026-09-29) ran ``... && cd $S && git init -q . ; git apply
pr-184.patch``: the chain failed and the patch was applied to the project itself. Four
report repairs and an Investigator retry were then all rejected the same way, because
nothing put the files back, and the run stopped for a person.

Only what the snapshots prove safe is restored. A file the attempt created is deleted,
and a file that matched the committed version before the attempt is restored from Git.
A file that changed again after the attempt, or held uncommitted edits before it, is
left as it is and named.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


class StrayWrites(ValueError):
    """A read-only stage changed these repository paths."""

    def __init__(self, message: str, paths):
        super().__init__(message)
        self.paths = sorted(paths)


def undo(state: dict, record: dict, error):
    """Restore the files a rejected read-only attempt wrote; return the error to record.

    Any other error is returned unchanged. For StrayWrites, the message says what was
    restored and what was left, so the Investigator and the person see it."""
    if not isinstance(error, StrayWrites):
        return error
    try:
        restored, kept = restore(state["workspace"], record, error.paths)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as problem:
        restored, kept = [], [f"{path} (restore failed: {problem})" for path in error.paths]
    note = ("; the runner restored " + ", ".join(restored) if restored else "") + (
        "; left as they are (changed since, or not clean before the attempt): " + ", ".join(kept) if kept else ""
    )
    return StrayWrites(str(error) + note, error.paths)


def restore(workspace, record: dict, paths) -> tuple[list[str], list[str]]:
    """Put ``paths`` back as the attempt's before-snapshot found them; return (restored, kept)."""
    root = Path(workspace)
    snapshot = util.read(record["before_ref"])
    before, head = snapshot["files"], snapshot["head"]
    after = util.read(record["after_ref"])["files"]
    restored, kept = [], []
    for path in paths:
        target = root / path
        if current(target) != after.get(path, "deleted"):
            kept.append(path)  # something else changed it after the attempt
        elif path not in before:
            target.unlink()
            restored.append(path)
        elif before[path] == committed(root, head, path):
            content = subprocess.run(
                ["git", "-C", str(root), "show", f"{head}:{path}"], capture_output=True, check=True
            ).stdout
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            mode = os.stat(target).st_mode
            os.chmod(target, mode | 0o111 if before[path].startswith("executable:") else mode & ~0o111)
            restored.append(path)
        else:
            kept.append(path)
    return restored, kept


def current(path: Path) -> str:
    """The snapshot entry the file has now (util.snapshot's form)."""
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if not path.is_file():
        return "deleted"
    return ("executable:" if path.stat().st_mode & 0o111 else "") + util.file_hash(path)


def committed(root: Path, head: str, path: str) -> str | None:
    """The snapshot entry of ``path`` as committed at ``head``, or None when it is not a plain committed file."""
    listed = subprocess.run(["git", "-C", str(root), "ls-tree", head, "--", path], capture_output=True, text=True)
    fields = listed.stdout.split()
    if listed.returncode or len(fields) < 3 or fields[1] != "blob" or fields[0] not in ("100644", "100755"):
        return None
    content = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", fields[2]], capture_output=True, check=True
    ).stdout
    return ("executable:" if fields[0] == "100755" else "") + hashlib.sha256(content).hexdigest()
