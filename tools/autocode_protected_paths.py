"""Portable protected-file identities, including original internal file links.

A link captures its whole file-link chain and terminal file. A link that stands
for code (a test file, a module tests import) must lead to a file of the source
snapshot: a directory or a target the snapshot omits remains refused. Any other
link among the tests is data they read (a fixture directory, a link that a test
of link handling needs dangling, absolute or looping); where it leads to no
snapshot file it is captured as its text alone.
"""

import os
import shutil
from pathlib import Path, PurePosixPath

try:
    from . import autocode_project_kinds as project_kinds
    from . import autocode_util as util
except ImportError:
    import autocode_project_kinds as project_kinds
    import autocode_util as util


def relative_name(name):
    relative = PurePosixPath(name)
    if (
        not name
        or name == "."
        or relative.is_absolute()
        or ".." in relative.parts
        or ".git" in relative.parts
        or str(relative) != name
    ):
        raise ValueError("Protected test paths must be portable repository-relative paths")
    return relative


def path_in(root, name, *, allow_link=False):
    relative_name(name)
    root = Path(root)
    path = root / name
    current = root
    if current.is_symlink():
        raise ValueError(f"Protected test root must not be a symlink: {root}")
    for part in Path(name).parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Protected test must not follow a directory symlink: {name}")
    if path.is_symlink():
        if allow_link:
            return path
        raise ValueError(f"Protected test must not follow a symlink: {name}")
    if not path.is_file():
        raise ValueError(f"Protected test is missing: {name}")
    return path


def identity(path):
    if path.is_symlink():
        return {"symlink": os.readlink(path)}
    return {"sha256": util.file_hash(path), "size": path.stat().st_size, "mode": path.stat().st_mode & 0o777}


def resolve(name, target, is_directory=None):
    """The repository path a link's text names, by its names alone.

    ``is_directory`` is asked about each directory the text passes through, given
    its path parts; without it only the text is checked: relative, and inside
    the repository.
    """
    if not target or PurePosixPath(target).is_absolute():
        raise ValueError(f"Protected test link must stay repository-relative: {name}")
    parts = list(PurePosixPath(name).parent.parts)
    tokens = target.split("/")
    for index, part in enumerate(tokens):
        if part == "..":
            if not parts:
                raise ValueError(f"Protected test link escapes the repository: {name}")
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
        if index < len(tokens) - 1 and is_directory is not None and not is_directory(parts):
            raise ValueError(f"Protected test link traverses a non-directory or directory symlink: {name}")
    return str(relative_name("/".join(parts)))


def _directories(root):
    """Lexical parents are checked without following an intermediate directory link."""

    def is_directory(parts):
        parent = Path(root).joinpath(*parts)
        return parent.is_dir() and not parent.is_symlink()

    return is_directory


def data_links(files):
    """The names of the data links a protected binding captures as their text."""
    return sorted(name for name, value in files.items() if isinstance(value, dict) and "symlink" in value)


def holds_code(name) -> bool:
    """Whether a link by this name stands for code: a test file, or a module tests import."""
    return PurePosixPath(name).suffix in project_kinds.CODE_SUFFIXES


def follow(name, target, is_directory, known):
    """The captured file or link that the link ``name`` leads to, or None when it is data that leads to none.

    Code must lead to one: a test that is a link to anything else is refused, since its
    assertions would not be captured. A data link that leads to a directory, to a path the
    snapshot does not hold or out of the repository is all the repository holds of it, so
    its text is what is captured; the files of a linked directory are eligible under their
    own names.
    """
    try:
        found = resolve(name, target, is_directory)
        if found not in known:
            raise ValueError(f"Protected test link target is not in the source snapshot: {found}")
        return found
    except ValueError:
        if holds_code(name):
            raise
        return None


def collect(root, names, available):
    """Capture eligible files and their snapshot-bound link closure (see ``follow``)."""
    known = {name for name, value in available.items() if value != "deleted"}
    is_directory = _directories(root)
    files = {}
    for start in names:
        name, visited = start, set()
        while name is not None and name not in files:
            if name in visited:
                if any(holds_code(link) for link in visited):
                    raise ValueError(f"Protected test link cycle: {start}")
                break
            visited.add(name)
            if name not in known:
                raise ValueError(f"Protected test link target is not in the source snapshot: {name}")
            path = path_in(root, name, allow_link=True)
            value = identity(path)
            # Insert after the chain is checked, so a cycle cannot look complete.
            if "symlink" not in value:
                break
            name = follow(name, value["symlink"], is_directory, known)
        for name in visited:
            files[name] = identity(path_in(root, name, allow_link=True))
    return dict(sorted(files.items()))


def verify_links(root, files):
    captured = collect(root, files, files)
    if captured != files:
        raise ValueError("Original protected test bundle changed or has an incomplete link closure")


def copy_entry(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    else:
        shutil.copy2(source, target)
