"""Retained inputs for a base-definition proof, independent of the verifier.

Read links from the effective Git tree, never from candidate-controlled paths.
Keep reviewed source changes visible and allocate proof trees outside the project
so ancestor package-manager configuration cannot change what the proof executes.
"""

from __future__ import annotations

import contextlib
import os
import posixpath
import tempfile
from pathlib import Path, PurePosixPath


def linked_inputs(entries, read, pinned):
    """Retain link chains, directory targets and absent targets of definition files.

    ``entries`` maps Git paths to (mode, object). Reject external and cyclic links:
    a proof cannot bind those inputs to the base revision.
    """
    retained = set(pinned)
    queue = [(path, frozenset()) for path in sorted(pinned)]
    while queue:
        path, seen = queue.pop()
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts) + 1):
            prefix = posixpath.join(*parts[:index])
            if entries.get(prefix, (None,))[0] != "120000":
                continue
            if prefix in seen:
                raise ValueError(f"suite definition contains a cyclic link at {prefix}")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(prefix), read(prefix)))
            if target in ("", ".", "..") or target.startswith(("../", "/")):
                raise ValueError(f"suite definition link {prefix} does not bind an in-tree input")
            target = posixpath.join(target, *parts[index:]) if parts[index:] else target
            retained.update((prefix, target))
            descendants = [name for name in entries if name.startswith(target + "/")]
            reached = [target, *descendants]
            retained.update(reached)
            queue.extend((name, seen | {prefix}) for name in reached)
            break
    return retained


def reviewed_changes(workspace, changes, patched):
    """Overlay the candidate even when it reverted a file changed by the review."""
    measured = dict(changes)
    for path in patched - changes.keys():
        measured[path] = "modified" if os.path.lexists(Path(workspace, path)) else "deleted"
    return measured


def check_parents(overlay, pinned):
    """A candidate file/link cannot replace a directory holding retained inputs."""
    for path, status in overlay.items():
        if status != "deleted" and any(item.startswith(path + "/") for item in pinned):
            raise ValueError(f"candidate path {path} replaces a directory holding the base suite definition")


@contextlib.contextmanager
def isolated_folder(workspace):
    """Keep the proof outside the candidate, including when TMPDIR is overridden."""
    with tempfile.TemporaryDirectory(prefix="autocode-suite-definition-") as temporary:
        folder = Path(temporary).resolve()
        if folder.is_relative_to(Path(workspace).resolve()):
            raise ValueError("temporary proof folder is inside the candidate workspace")
        yield folder
