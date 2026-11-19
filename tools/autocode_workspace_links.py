"""Links in a workspace that lead outside it, and the tool-permission patterns that name them.

A tool that checks a path as it is written, rather than where it leads, writes outside the project
through such a link. In a live run (2026-11-17) a Kilo Builder's ``edit`` changed a file in a sibling
directory through a tracked link at ``src/pricing/discount.py``, while Kilo's ``read`` of the same
path was refused as an external directory. The kernel boundaries (Seatbelt, bubblewrap) and Claude
Code's edit hook (``providers/claude_edit_scope``) resolve a path before judging it; a provider
whose file tools run in its own process and judge the written path needs the links named.

``outside`` observes them from the workspace each time it is asked: the entries git lists (tracked,
and untracked but not ignored), or every entry when the workspace is not a git checkout.
``read_targets`` gives what such links lead to, so a kernel-contained stage may read, never write,
the files the project itself depends on (owner decision 2026-11-17: under the macOS sandbox a
project whose code is a link out of it could not load its own tests). ``linked_file`` is the same
answer for a path as a stage writes it: a file the project reaches through one of those links is a
workspace file a stage may read and cite by that path (2026-11-19: Requirements cited the task's own
``config/rates.json``, a tracked link to a shared file, and the citation check refused it as "not a
file in the workspace", costing a repair and an Investigator call). Imports only the secret-name
rule from AutoCode.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

try:
    from .autocode_source_privacy import secret_path
except ImportError:
    from autocode_source_privacy import secret_path

GIT_SECONDS = 30


def _listed(root: Path) -> list[str] | None:
    """The workspace's entries as git lists them, relative to ``root``; None when git cannot list them."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
            timeout=GIT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [name for name in os.fsdecode(listed.stdout).split("\0") if name]


def _walked(root: Path) -> list[str]:
    """Every entry below ``root`` outside ``.git``, without descending through a linked directory."""
    names = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name != ".git"]
        base = Path(directory).relative_to(root)
        names += [(base / name).as_posix() for name in (*subdirectories, *files)]
    return names


def outside(workspace) -> list[tuple[str, bool]]:
    """``(relative path, leads to a directory)`` for each link in ``workspace`` that resolves outside it.

    A dangling link counts by where it would lead, since writing through it creates the file there.
    """
    root = Path(workspace).resolve()
    names = _listed(root)
    found = []
    for name in sorted(set(_walked(root) if names is None else names)):
        path = root / name
        if not path.is_symlink():
            continue
        try:
            target = path.resolve()
        except (OSError, RuntimeError):  # a loop leads nowhere a tool can write
            continue
        if not target.is_relative_to(root):
            found.append((name, target.is_dir()))
    return found


def read_targets(workspace) -> list[Path]:
    """The existing files and directories the workspace's outside links lead to, for read-only access.

    A target that is ``/``, the home directory or above it, or a secret by name (``.env``, ``id_rsa``) is
    left out: it is not a dependency a link grants, and reading it stays refused.
    """
    root, home = Path(workspace).resolve(), Path.home().resolve()
    found = []
    for name, _directory in outside(root):
        try:
            target = (root / name).resolve()
        except (OSError, RuntimeError):
            continue
        if not target.exists() or home.is_relative_to(target) or secret_path(target.as_posix()):
            continue
        found.append(target)
    return sorted(set(found))


def linked_file(workspace):
    """A test for a path as written: its workspace-relative POSIX name when it reaches a readable file
    through one of the workspace's own links out of it (``outside``), else None.

    ``name`` is relative to the workspace or absolute below it. The link may be the file itself or a
    directory above it. The file it reaches must be one ``read_targets`` grants, so a secret or the home
    directory stays refused. The workspace's links are observed once, at the first call.
    """
    root = Path(workspace).resolve()
    links: list[tuple[frozenset[str], list[Path]]] = []

    def known():
        if not links:
            links.append((frozenset(name for name, _directory in outside(root)), read_targets(root)))
        return links[0]

    def check(name) -> str | None:
        relative = _below(root, name)
        if relative is None:
            return None
        names, targets = known()
        parts = relative.parts
        if not any("/".join(parts[:end]) in names for end in range(1, len(parts) + 1)):
            return None
        try:
            target = (root / relative).resolve()
        except (OSError, RuntimeError):
            return None
        if not target.is_file() or secret_path(target.as_posix()):
            return None
        if not any(target == granted or target.is_relative_to(granted) for granted in targets):
            return None
        return relative.as_posix()

    return check


def _below(root: Path, name) -> Path | None:
    """``name`` relative to ``root`` as written, without following links; None outside it."""
    if not isinstance(name, (str, Path)) or not str(name).strip() or "\0" in str(name):
        return None
    path = Path(str(name).strip())
    if path.is_absolute():
        parts = path.parts
        for end in range(len(parts), 0, -1):  # /tmp/p spells /private/tmp/p on macOS
            try:
                if Path(*parts[:end]).resolve() == root:
                    path = Path(*parts[end:]) if end < len(parts) else Path()
                    break
            except (OSError, RuntimeError):
                return None
        else:
            return None
    if not path.parts or ".." in path.parts:
        return None
    return path


def patterns(workspace, name: str, directory: bool) -> list[str]:
    """The permission patterns a tool may be asked about for ``name``, below ``workspace``.

    OpenCode-family tools ask with the path relative to their worktree (the git top level, or ``/``
    outside git), so the deny names the path relative to the workspace, to the top level and to ``/``,
    and as an absolute path.
    A linked directory also denies everything below it.
    """
    root = Path(workspace).resolve()
    absolute = root / name
    spellings = [name, absolute.as_posix().lstrip("/"), absolute.as_posix()]
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_SECONDS,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    if top and absolute.is_relative_to(Path(top).resolve()):
        spellings.append(absolute.relative_to(Path(top).resolve()).as_posix())
    unique = list(dict.fromkeys(spellings))
    return [*unique, *(spelling + "/*" for spelling in unique)] if directory else unique
