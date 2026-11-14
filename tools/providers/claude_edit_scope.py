"""Tell a Claude Code stage why an edit outside its directory is refused, and where its own copy is.

A stage that may write is launched with Edit and Write accepted inside its own directory only
(``providers/claude.WRITE_ALLOWED``). Claude Code's own refusal reads "Claude requested permissions to write
to PATH, but you haven't granted it yet". A parallel Builder (Haiku) that had edited the project by absolute
path instead of its worktree took that for a permission to ask for: it stopped, reported "Grant file edit
permissions", and the person was asked about it (a real run, 2026-11-14). The mistake is a path, and the
runner knows the right one.

Claude Code runs this script before every Edit and Write of such a stage (a ``PreToolUse`` hook that
``providers/claude.launch`` passes with ``--settings``), with the call as JSON on standard input and the
stage's directory as its only argument:

    python -I providers/claude_edit_scope.py STAGE_DIRECTORY

For a path inside the directory it prints nothing, and Claude Code decides as it would without it. For a
path outside it prints a refusal whose reason the model reads: the path is outside the stage's directory,
no permission changes that, and the stage's own file at the same place is at the named path, when there is
one.
It grants nothing: an edit it lets through is still judged by Claude Code, and when this script fails
Claude Code's own refusal stands.

Standard library only; it runs isolated (``-I``) and imports nothing from AutoCode.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MATCHER = "Edit|Write"


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def own_copy(path: Path, root: Path) -> Path | None:
    """The path under ``root`` with the longest ending of ``path`` that is there, or None.

    ``PROJECT/packages/web/src/format.js`` asked of the worktree ``PROJECT/.autocode/builders/B/2`` is
    that worktree's ``packages/web/src/format.js``. A file that does not exist yet (a Write) is placed by
    its directory, which must be below the root: ``/etc/hosts`` is nobody's ``hosts``.
    """
    parts = path.parts[1:]
    for start in range(len(parts)):
        candidate = root.joinpath(*parts[start:])
        new_file = start < len(parts) - 1 and candidate.parent.is_dir()
        if (candidate.exists() or new_file) and inside(candidate, root):
            return candidate
    return None


def refusal(path: str, root: Path, current: Path | None = None) -> str | None:
    """Why an Edit or Write of ``path`` is refused for the stage working in ``root``; None when it is inside.

    A relative path is read from ``current``, the directory the session is in now (a command may have
    moved it below the root), as Claude Code reads it."""
    target = Path(os.path.normpath(path if os.path.isabs(path) else (current or root) / path))
    if inside(target, root):
        return None
    reason = (
        f"{path} is outside the directory this stage works in, {root}. Edit and Write work only inside that "
        "directory. This is not a permission anyone can grant, so do not ask for it and do not stop. "
    )
    own = own_copy(target, root)
    if own is not None:
        return reason + f"If you meant this stage's file, it is {own}: read and change that path instead, and carry on."
    return reason + f"Use a path inside {root} instead, and carry on."


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        return 0
    try:
        call = json.load(sys.stdin)
        root = Path(argv[0]).resolve()
    except (OSError, ValueError):
        return 0
    arguments = call.get("tool_input") if isinstance(call, dict) else None
    path = arguments.get("file_path") if isinstance(arguments, dict) else None
    if not isinstance(path, str) or not path:
        return 0
    current = call.get("cwd")
    reason = refusal(path, root, Path(current) if isinstance(current, str) and current else Path.cwd())
    if reason is not None:
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
        print(json.dumps({"hookSpecificOutput": decision}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
