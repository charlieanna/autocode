"""A serial Builder's assignment boundary, measured over the whole assignment.

Checking only the latest attempt's own delta lets a retry launder what an earlier
attempt left behind: attempt one edits a file outside the assignment and is
rejected, the edit stays in the tree for inspection, and attempt two (with no new
edits, or with only in-scope ones) would pass. So the evidence is the retained
worktree against the assignment's starting snapshot, which is the before-snapshot
of the task's earliest Builder attempt, saved as that stage record's before_ref.

Pure over saved snapshot files and stage records. It imports nothing from the
runner (AGENTS.md rule 2); callers pass the stage history.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import json
import os
import subprocess
from pathlib import Path

try:
    from . import autocode_stray_writes as stray
except ImportError:
    import autocode_stray_writes as stray

BUILDER = "terra"


def contains(root, path):
    return path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/")


def _read(ref):
    try:
        value = json.loads(Path(ref).read_text())
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _ref(stages, attempt, key):
    """The first readable snapshot ref among ``attempt``'s row copies, for its files and head."""
    for row in (attempt, *stages):
        if _same_attempt(row, attempt) and _files(row.get(key)) is not None:
            return row.get(key)
    return None


def _files(ref):
    value = _read(ref)
    return value.get("files") if value is not None else None


def _snapshot(stages, attempt, key):
    """``attempt``'s saved snapshot, from whichever copy of its row still points at it.

    Archiving a rejected attempt moves its files and rewrites only the row it archives.
    """
    return _files(_ref(stages, attempt, key))


def _same_attempt(row, attempt):
    """One provider attempt can appear in several saved rows (a checkpoint copy, its
    archived row, a repaired report's original); they share the launch time."""
    return row is attempt or (
        bool(attempt.get("started_at"))
        and all(row.get(key) == attempt.get(key) for key in ("started_at", "stage", "iteration"))
    )


def starting_attempt(stages, record):
    """The task's earliest serial Builder attempt; ``record`` itself when it is the first."""
    task = record.get("task_id")
    return next(
        (
            row
            for row in stages
            if task
            and row.get("task_id") == task
            and (row.get("original_stage") or row.get("stage")) == BUILDER
            and not row.get("runner_owned")
            and not row.get("batch_id")
        ),
        record,
    )


def retained_changes(stages, record):
    """Every path changed or deleted since the assignment began, as ``record`` left the tree.

    None when an earlier attempt exists but the snapshots needed to compare against
    it cannot be read: that is missing evidence, never an empty delta.
    """
    first = starting_attempt(stages, record)
    before, after = _snapshot(stages, first, "before_ref"), _snapshot(stages, record, "after_ref")
    if before is None or after is None:
        # With no earlier attempt, this attempt's runner-measured delta is the whole delta.
        return sorted(record.get("changed_files") or []) if _same_attempt(first, record) else None
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def undo_created(paths, stages, record, workspace) -> str:
    """Remove or restore the out-of-scope files this assignment touched, then return the refusal naming what happened.

    A rejected attempt's out-of-scope edits stay in the tree, and the gate measures the whole assignment, so one
    file an attempt should not have written refuses every retry. Two live greenfield-todo-cli runs (Claude models,
    2026-09-30) stopped that way: the milestone-1 Builder also wrote README.md, which the plan gave to milestone 2.
    A file absent from the starting snapshot and unchanged since this attempt is deleted, as autocode_stray_writes
    does for read-only stages. A pre-existing file is restored from Git the same way when the snapshots prove it
    safe: unchanged since this attempt, and its starting content exactly what was committed at the recorded head.
    An edit of a file that already held uncommitted changes, or anything not provable, is kept for a person."""
    start = _read(_ref(stages, starting_attempt(stages, record), "before_ref")) or {}
    before, head = start.get("files"), start.get("head")
    after = _snapshot(stages, record, "after_ref")
    removed, restored, kept = [], [], []
    for name in paths:
        target = Path(workspace) / name if workspace else None
        if target is None or before is None or after is None or stray.current(target) != after.get(name, "deleted"):
            kept.append(name)  # changed since this attempt, or the snapshots cannot prove anything
        elif name not in before:
            target.unlink()
            removed.append(name)
        elif head and before[name] == stray.committed(Path(workspace), head, name):
            try:
                content = subprocess.run(
                    ["git", "-C", str(workspace), "show", f"{head}:{name}"], capture_output=True, check=True
                ).stdout
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                mode = os.stat(target).st_mode
                os.chmod(target, mode | 0o111 if before[name].startswith("executable:") else mode & ~0o111)
            except (OSError, subprocess.CalledProcessError) as problem:
                kept.append(f"{name} (restore failed: {problem})")
            else:
                restored.append(name)
        else:
            kept.append(name)  # held uncommitted changes before the assignment; restoring would lose them
    message = "Builder attempts for this task changed files outside the assigned paths"
    if removed:
        message += "; the runner removed the files they created, so a retry starts without them: " + ", ".join(removed)
    if restored:
        message += (
            "; the runner restored pre-existing files to their committed content, so a retry is not "
            "refused for them: " + ", ".join(restored)
        )
    if kept:
        message += "; edits retained for inspection: " + ", ".join(kept)
    return message


BUILD_OUTPUT_NOTE = prompts.get("fragments/assignment/build-output-note.md")

# Headers of native executables: ELF (Linux), Mach-O (macOS, both byte orders, 32/64-bit and fat), PE (Windows).
EXECUTABLE_HEADERS = (
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"MZ",
)


def build_output(workspace, name, stages, record):
    """A compiled program a build left behind, such as `go build .` writing ./policy: a file the assignment
    created (absent from its starting snapshot) whose first bytes are a native executable header. It is not
    source the Builder wrote, as __pycache__/*.pyc are not (autocode_support.snapshot skips those). Only new
    files qualify; changing or deleting an existing file, executable or not, always counts."""
    if workspace is None:
        return False
    before = _snapshot(stages, starting_attempt(stages, record), "before_ref")
    path = Path(workspace) / name
    if before is None or name in before or path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    return head.startswith(EXECUTABLE_HEADERS)


def outside(owned, stages, record, workspace=None):
    """Paths changed outside ``owned`` since the assignment began; None when unprovable.
    With ``workspace``, new compiled executables a build left behind are not counted (``build_output``)."""
    changed = retained_changes(stages, record)
    if changed is None:
        return None
    return [
        name
        for name in changed
        if not any(contains(root, name) for root in owned) and not build_output(workspace, name, stages, record)
    ]
