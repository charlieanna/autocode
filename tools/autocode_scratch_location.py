"""Where proof execution copies live: outside the live workspace (#4057, phase 1).

A proof scratch tree built inside the workspace lets an ancestor-aware runner walk up
into the live candidate source: Go finds the candidate ``go.mod``, Node the candidate
``package.json``, Python the candidate project root or editable install. A purported
base observation then reads the candidate, so it is not an independent base failure.

Every run-owned proof copy is therefore mirrored to a sibling of the workspace,
``workspace.parent / "<workspace.name>.autocode-scratch" / <path relative to workspace>``.
No ancestor of the mirror is the workspace, so ancestor lookup cannot reach candidate
source. The mirror is deterministic from the run-owned path alone — the ``.autocode``
store location the registry binds every run to (``workspace/.autocode/runs/<run>``) — so
preparation, recovery and every proof gate re-derive the same location without a new
admission field or a question for the person. A path that is not under a ``.autocode``
store (for example a run directory already outside the workspace) is left where it is.

This is physical relocation only. The kernel read boundary, editable-install fallthrough
and dependency custody are later phases of #4057; absolute paths, symlinks and mutable
dependencies are not yet denied here.
"""

from pathlib import Path

SUFFIX = ".autocode-scratch"
STORE = ".autocode"


def workspace_of(path):
    """The workspace owning a run-owned path, read from its ``.autocode`` store component.

    The last ``.autocode`` component owns the path, so a workspace nested inside another
    store resolves to its own. Returns ``None`` when the path is not under a store.
    """
    parts = Path(path).parts
    for index in range(len(parts) - 1, 0, -1):
        if parts[index] == STORE:
            return Path(*parts[:index])
    return None


def external_root(workspace):
    """The scratch mirror root: a sibling of the workspace, never inside it."""
    workspace = Path(workspace)
    return workspace.parent / (workspace.name + SUFFIX)


def relocate(destination, workspace=None):
    """Mirror a proof copy outside its workspace; leave an already-external copy alone."""
    destination = Path(destination)
    resolved = destination.resolve()
    if workspace is None:
        workspace = workspace_of(resolved)
        if workspace is None:
            return destination
    workspace = Path(workspace).resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return destination
    return external_root(workspace) / relative


def scratch_root(base):
    """The external ``scratch`` root for a run-owned proof directory ``base``."""
    return relocate(Path(base) / "scratch")


def source_base(root):
    """The workspace-side proof directory an external scratch root mirrors, or ``None``.

    The inverse of :func:`scratch_root`, used by custody binding to check that a scratch
    root and a command output belong to the same proof without requiring the scratch to
    sit inside the workspace.
    """
    parts = Path(root).parts
    if not parts or parts[-1] != "scratch":
        return None
    for index in range(len(parts) - 2, 0, -1):
        name = parts[index]
        if name.endswith(SUFFIX) and len(name) > len(SUFFIX):
            external = Path(*parts[: index + 1])
            workspace = external.parent / name[: -len(SUFFIX)]
            return workspace / Path(*parts[index + 1 : -1])
    return None
