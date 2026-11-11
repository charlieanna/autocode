"""The Git checkouts a folder that is not one itself holds, nearest first (#2066).

A person who works on several repositories often keeps them side by side in one parent folder and
opens that folder. The parent is not a Git checkout, so a chat started there works in a new, empty
private project (``autocode_workspaces.bootstrap``) and changes none of them. The places a new chat
may work (``autocode_worktree_choice.options``) name these checkouts in their warning, so the
person sees that before the chat starts; the terminal chat adds such a folder as a source folder
for discovery (``autocode_chat_coordination.include_launch_folder``).

Reads only directory entries: no Git command, no symlink followed, and a bounded number of
directories looked at. Lower layer: imports nothing from AutoCode.
"""

from __future__ import annotations

from pathlib import Path

DEPTH = 2  # the folder's children and grandchildren: a folder of checkouts, or of groups of them
BUDGET = 2000  # directories looked at before giving up: discovery does the real, deeper search
SKIPPED = frozenset({".git", ".autocode", ".autocode-ui", "node_modules", "vendor", ".venv", "venv"})


def found(folder, *, depth: int = DEPTH, budget: int = BUDGET) -> list[Path]:
    """The Git checkouts in the first ``depth`` levels below ``folder``, nearest first.

    A checkout is a directory with a ``.git`` entry (a repository or a linked worktree); the search
    does not look inside one. At most ``budget`` entries are looked at."""
    result, level, seen = [], [Path(folder)], 0
    for _ in range(depth):
        below = []
        for directory in level:
            try:
                children = sorted(directory.iterdir())
            except OSError:
                continue
            for child in children:
                seen += 1
                if seen > budget:
                    return result
                if child.name in SKIPPED or child.is_symlink() or not child.is_dir():
                    continue
                (result if (child / ".git").exists() else below).append(child)
        level = below
    return result


def names(checkouts, shown: int = 3) -> str:
    """``api, history and 2 more``: the first ``shown`` checkouts' folder names, for a sentence."""
    listed = [Path(path).name for path in checkouts]
    head = ", ".join(listed[:shown])
    return head + (f" and {len(listed) - shown} more" if len(listed) > shown else "")
