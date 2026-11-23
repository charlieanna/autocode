"""Git source plus explicitly selected deliverables, including ignored files.

Callers supply literal paths from runner-validated authority. This utility never
loads run state or trusts a Builder's changed-files report. The selected paths
must travel with a saved source snapshot so a later freshness check uses the
same inventory policy. No process-global or workspace-global policy is changed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

try:
    from . import autocode_project_kinds as project_kinds
    from . import autocode_util as util
except ImportError:
    import autocode_project_kinds as project_kinds
    import autocode_util as util


_INTERNAL = {".git", ".autocode", ".autocode-ui"}


class UnsafeSourcePath(ValueError):
    """A source path would require following an unbound parent link."""


def symlinked_parent(root, relative, *, checked=None):
    """Whether traversing this source name would follow a parent link."""
    for parent in Path(relative).parents:
        if parent == Path(".") or (checked is not None and parent in checked):
            continue
        if (Path(root) / parent).is_symlink():
            return True
        if checked is not None:
            checked.add(parent)
    return False


def first_symlinked_parent(root, names):
    """Name the first inventoried source whose parent traversal follows a link."""
    checked: set[Path] = set()
    return next((name for name in names if symlinked_parent(root, name, checked=checked)), None)


def literal_paths(paths):
    """Validate and canonicalize a bounded, explicit source inventory policy."""
    if not isinstance(paths, (list, tuple)):
        raise ValueError("Source paths must be an explicit list")
    result = set()
    for name in paths:
        if not isinstance(name, str) or not name or name.startswith("/") or any(c in name for c in "*?[]\\\x00\n\r"):
            raise ValueError("Source paths must be literal repository-relative paths")
        name = name.rstrip("/")
        if any(p in ("", ".", "..") or p.casefold() in _INTERNAL for p in name.split("/")):
            raise ValueError("Source path crosses an internal or unbounded directory")
        result.add(name)
    return sorted(result)


def is_canonical_paths(paths):
    """Check saved JSON scope without normalizing or changing retained data."""
    if not isinstance(paths, list):
        return False
    try:
        return paths == literal_paths(paths)
    except ValueError:
        return False


def _identity(path, info):
    if stat.S_ISLNK(info.st_mode):
        return "symlink:" + os.readlink(path)
    if stat.S_ISREG(info.st_mode):
        return ("executable:" if info.st_mode & 0o111 else "") + util.file_hash(path)
    raise ValueError("Unsupported source file type: " + str(path))


def _include(root, relative, files):
    # Never dereference a parent symlink. Even an in-repository target can move
    # after this check, so the link itself is part of source, not its contents.
    if symlinked_parent(root, relative):
        raise UnsafeSourcePath("Source path has a symlinked parent: " + str(relative))
    # Preserve Git boundaries; nested selection is applied recursively below.
    if any(
        files.get(parent.as_posix(), "").startswith(("submodule:", "uninitialized-submodule"))
        for parent in relative.parents
    ):
        return
    path = root / relative
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        if (path / ".git").exists():
            files[relative.as_posix()] = "submodule:" + util.snapshot(path)["revision"]
            return
        for child in sorted(path.iterdir()):
            if (
                child.name.casefold() not in _INTERNAL
                and child.name not in project_kinds.BYTECODE_DIRS
                and not child.name.endswith(project_kinds.BYTECODE_SUFFIXES)
            ):
                _include(root, child.relative_to(root), files)
    else:
        files[relative.as_posix()] = _identity(path, info)


def snapshot(workspace, *, paths=(), base_snapshot=None):
    """Hash Git-visible source and selected outputs without altering Git state.

    Omitted paths preserve the legacy snapshot exactly. A nonempty policy is
    included separately for subsequent copy/freshness checks; source revision
    remains a digest of actual HEAD and content, not of selection spelling.
    Missing selected files are absent (creation/deletion changes the files map).
    """
    selected = literal_paths(paths)
    root = Path(workspace)
    original = (base_snapshot or util.snapshot)(root)
    if not selected:
        return original
    # A supplied reader may return a cached value. Do not mutate its inventory.
    result = {**original, "files": dict(original["files"])}
    for name in selected:
        _include(root, Path(name), result["files"])
    for name, identity in list(result["files"].items()):
        if identity.startswith("submodule:"):
            nested = nested_paths(root, selected, name)
            if nested:
                result["files"][name] = "submodule:" + snapshot(root / name, paths=nested)["revision"]
    if result["files"] != original["files"]:
        result["revision"] = util.digest({"head": result["head"], "files": result["files"]})
    result["source_paths"] = selected
    return result


def nested_paths(workspace, paths, name):
    """Translate outer literal scope into one nested Git repository's scope."""
    selected = []
    for path in literal_paths(paths):
        if path == name or name.startswith(path + "/"):
            selected.extend(
                child.name
                for child in (Path(workspace) / name).iterdir()
                if child.name.casefold() not in _INTERNAL
                and child.name != "__pycache__"
                and not child.name.endswith(".pyc")
            )
        elif path.startswith(name + "/"):
            selected.append(path[len(name) + 1 :])
    return literal_paths(selected)


def inventory(workspace, *, paths=()):
    """Flatten copyable source files while retaining nested Git scope in identity."""
    root = Path(workspace)
    result = {}
    for name, identity in snapshot(root, paths=paths)["files"].items():
        if identity.startswith("submodule:"):
            # Git spells an untracked repository as "directory/". Keep the
            # snapshot's historical identity, but emit canonical file paths.
            prefix = name.rstrip("/")
            nested = inventory(root / prefix, paths=nested_paths(root, paths, prefix))
            result.update({prefix + "/" + child: value for child, value in nested.items()})
        elif identity != "uninitialized-submodule":
            result[name] = identity
    return result


def bound_inventory(workspace, current, *, paths=()):
    """Flatten the exact captured snapshot, authenticating each nested Git snapshot against it."""
    root, result = Path(workspace), {}
    for name, identity in current["files"].items():
        if identity.startswith("submodule:"):
            prefix = name.rstrip("/")
            selected = nested_paths(root, paths, prefix)
            nested = snapshot(root / prefix, paths=selected)
            if "submodule:" + nested["revision"] != identity:
                raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Nested source changed while its copy was inventoried")
            children = bound_inventory(root / prefix, nested, paths=selected)
            result.update({prefix + "/" + child: value for child, value in children.items()})
        elif identity != "uninitialized-submodule":
            result[name] = identity
    return result


def copied_files(workspace, names):
    """Read known copyable file identities without depending on scratch Git metadata.

    Names come from inventory() of the authoritative source. A clean copy may
    flatten nested repositories; their copied bytes must still be checked.
    Missing files and parent symlinks never substitute another input.
    """
    root, files = Path(workspace), {}
    for name in names:
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise ValueError("Copied input must have an ordinary relative path")
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or any(part.casefold() in _INTERNAL for part in relative.parts)
        ):
            raise ValueError("Copied input crosses the source boundary")
        _include(root, relative, files)
    return files


def metadata(workspace, files):
    """Observable file metadata used alongside content in execution identity."""
    result = {}
    for name in files:
        path = Path(workspace) / name
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            result[name] = [info.st_mode, info.st_mtime_ns, info.st_uid, info.st_gid]
    return result


# POSIX requires at least eight link traversals; keep longer chains unbound.
_MAX_FILE_LINK_CHAIN = 8


def unbound_symlinks(workspace, files, tracked_paths, *, allow_untracked_terminal=False, bound_links=()):
    """Links not bound by this inventory and tracked, ordinary in-tree targets.

    Every link is tracked; its terminal regular file must be tracked unless
    an explicitly scoped copy permits an inventoried untracked terminal. All
    bytes still match this snapshot; escapes and unsafe parent links are held.
    ``bound_links`` names the data links a protected test binding captures as
    text: the copy carries each un-followed, so its own pinned text is bound
    and where it leads is the project's fixture, not source bytes.
    """
    if type(allow_untracked_terminal) is not bool:
        raise ValueError("Untracked terminal policy must be a boolean")
    if not isinstance(bound_links, (list, tuple)) or any(
        not isinstance(name, str)
        or not name
        or name.startswith("/")
        or ".." in name.split("/")
        or any(c in name for c in "\\\x00\n\r")
        for name in bound_links
    ):
        raise ValueError("Bound data links must be portable repository-relative paths")
    bound = set(bound_links)
    links = [name for name, value in files.items() if value.startswith("symlink:")]
    if not links:
        return []
    try:
        tracked = set(tracked_paths())
        root = Path(workspace).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return sorted(links)
    unbound = []
    for name in links:
        try:
            current = name
            visited = set()
            while True:
                if current in visited or len(visited) >= _MAX_FILE_LINK_CHAIN:
                    raise ValueError("symlink cycle or chain exceeds portable resolution bound")
                visited.add(current)
                link = root / current
                text = os.readlink(link)
                if current not in tracked or files.get(current) != "symlink:" + text:
                    raise ValueError("unbound link")
                if any((root / parent).is_symlink() for parent in Path(current).parents if parent != Path(".")):
                    raise ValueError("symlinked link parent")
                if name in bound:
                    # A data link the protected binding captures as text: the copy
                    # carries it un-followed, so its target need not be source bytes.
                    break
                if os.path.isabs(text) or Path(text).drive or "\\" in text or text.endswith("/"):
                    raise ValueError("unbound link")
                target = link.parent
                # Preserve literal dot components: ``file/.`` still traverses
                # the file as a directory, which Path(text).parts would erase.
                parts = text.split("/")
                for index, part in enumerate(parts):
                    if part == "..":
                        target = target.parent
                    elif part not in ("", "."):
                        target /= part
                    target.relative_to(root)
                    if index < len(parts) - 1 and (target.is_symlink() or not target.is_dir()):
                        raise ValueError("symlinked or invalid parent")
                relative = target.relative_to(root).as_posix()
                info = target.lstat()
                if files.get(relative) != _identity(target, info):
                    raise ValueError("unbound target")
                if stat.S_ISLNK(info.st_mode):
                    if relative not in tracked:
                        raise ValueError("untracked link")
                    current = relative
                    continue
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or (relative not in tracked and not allow_untracked_terminal)
                ):
                    raise ValueError("unbound target")
                break
        except (OSError, ValueError, RuntimeError):
            unbound.append(name)
    return sorted(unbound)


def preserve_after(path, current, *, write_json=util.atomic_json):
    """Keep a completed attempt's original snapshot bytes during reconciliation.

    A legacy snapshot may lack selection metadata. Matching source does not
    authorize rewriting that evidence with today's metadata or scope. A source
    mismatch must be reconciled explicitly, never rebound to the old report.
    """
    path = Path(path)
    if path.is_symlink():
        raise util.Paused("PAUSED_STALE_VALIDATION", "Saved after-source snapshot is a symlink")
    if path.exists():
        saved = util.read_object(path)
        if any(saved.get(key) != current.get(key) for key in ("head", "files", "revision")):
            raise util.Paused(
                "PAUSED_STALE_VALIDATION",
                "Saved after-source snapshot differs from current source; retain the stopped attempt for inspection",
            )
        return
    write_json(path, current)
