"""Prepare complete, isolated source for a bug investigation before its model runs."""

import filecmp
import os
import shutil
import subprocess
import tempfile
import time
from functools import partial
from pathlib import Path

EXCLUDED = {".git", ".autocode", ".autocode-ui"}
# Beside each copy, holding the moment that copy finished.
STAMP_SUFFIX = ".stamp"
# Beside each copy, naming the run whose attempt it was made for; only that run may reuse it.
OWNER_SUFFIX = ".owner"
# How long a finished copy is left alone even when nothing was written into it: another run of the
# same project may be working in it before its first write.
RECLAIM_GRACE_SECONDS = 600


def scratch_root(workspace):
    """Locate scratch inside this workspace without traversing a runner-state symlink."""
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError("Investigation requires an existing workspace")
    root = workspace / ".autocode" / "investigation"
    for path in (root.parent, root):
        if path.is_symlink():
            raise ValueError("Investigation scratch must not use a symlinked runner directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def owning_runner_dir(path):
    """The workspace .autocode whose investigation scratch copy contains path, or None."""
    path = Path(path)
    for copy in (path, *path.parents):
        if (copy.parent.parent.name, copy.parent.name) == (".autocode", "investigation"):
            return copy.parent.parent
    return None


def ignored_entries(workspace, directory, names, *, reuse_virtualenvs=False, inventory=None):
    """Exclude metadata and reject links that could expose state or the original source."""
    workspace = Path(workspace).resolve()
    ignored = EXCLUDED.intersection(names)
    if inventory is not None:
        relative = Path(directory).relative_to(workspace)
        ignored.update(name for name in names if relative / name not in inventory)
    if reuse_virtualenvs and Path(directory) == workspace:
        # Virtualenvs contain external interpreter links (and, on Linux, lib64
        # directory links). Reuse their interpreter through the handoff instead
        # of weakening the source-copy boundary or copying a nonportable env.
        for name in {".venv", "venv"}.intersection(names):
            environment = workspace / name
            config = environment / "pyvenv.cfg"
            if not environment.is_symlink() and config.is_file() and not config.is_symlink():
                ignored.add(name)
    for name in set(names) - ignored:
        source = Path(directory) / name
        if not source.is_symlink():
            continue
        try:
            relative = source.resolve(strict=True).relative_to(workspace)
        except (OSError, ValueError) as error:
            raise ValueError(f"Investigation cannot copy an external or invalid symlink: {source}") from error
        if EXCLUDED.intersection(relative.parts) or source.is_dir():
            raise ValueError(f"Investigation cannot copy a state or directory symlink: {source}")
        if inventory is not None and relative not in inventory:
            raise ValueError(f"Investigation cannot copy a symlink to ignored or excluded source: {source}")
    return ignored


def source_inventory(workspace):
    """Eligible Git paths and their ancestors; non-Git callers retain their copy rule."""
    workspace = Path(workspace).resolve()
    root = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    if root.returncode or Path(root.stdout.strip()).resolve() != workspace:
        return None
    listed = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    if listed.returncode:
        raise ValueError("Investigation cannot enumerate the Git source inventory")
    included = set()
    for name in filter(None, listed.stdout.decode().split("\0")):
        relative = Path(name)
        if EXCLUDED.intersection(relative.parts) or "__pycache__" in relative.parts or name.endswith(".pyc"):
            continue
        source = workspace / relative
        if source.is_dir() and not source.is_symlink():
            nested = source_inventory(source) if (source / ".git").exists() else None
            if nested is None:
                continue
            included.update(relative / item for item in nested)
        else:
            included.add(relative)
        included.update(relative.parents)
    return included


def prepare(workspace, owner=None):
    """Return a complete copy of the workspace for one attempt; prior attempts and application files remain intact.

    See ``prepare_measured`` for what ``owner`` allows.
    """
    return prepare_measured(workspace, owner)[0]


def prepare_measured(workspace, owner=None):
    """The copy for one attempt and what making it cost: ``files``, ``bytes``, ``seconds`` and ``reused``.

    A copy is only kept when something was written into it: an earlier attempt that never wrote is
    reclaimed here rather than holding a whole second tree on disk for the rest of the run (#2606).
    A retry of the same run (``owner``) takes that run's own untouched copy again when its bytes still
    match the workspace, instead of duplicating the whole tree once more; a copy another run made, one
    an attempt wrote into, or one the workspace has moved on from is never reused. The cost is for the
    stop reason: a stage deadline includes it, and a person should see the copy, not blame the model.
    """
    started = time.monotonic()
    workspace = Path(workspace).resolve()
    inventory = source_inventory(workspace)
    root = scratch_root(workspace)
    target = _reusable(root, workspace, inventory, owner) if owner else None
    reused = target is not None
    if target is None:
        target = Path(tempfile.mkdtemp(prefix="bug-", dir=root))
        shutil.copytree(
            workspace,
            target,
            dirs_exist_ok=True,
            ignore=partial(ignored_entries, workspace, reuse_virtualenvs=True, inventory=inventory),
        )
    if owner:
        owner_path(target).write_text(str(owner))
    stamp(target)
    reclaim_unused(root, keep=[target])
    files = bytes_copied = 0
    for entry in target.rglob("*"):
        try:
            status = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not entry.is_dir() or entry.is_symlink():
            files += 1
            bytes_copied += status.st_size
    measured = {
        "files": files,
        "bytes": bytes_copied,
        "seconds": round(time.monotonic() - started, 3),
        "reused": reused,
    }
    return target, measured


def _reusable(root, workspace, inventory, owner):
    """This run's untouched copy whose bytes still match the workspace, or None."""
    for copy in sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink()):
        try:
            since = stamp_path(copy).stat().st_mtime
            if owner_path(copy).read_text() != str(owner):
                continue
        except OSError:
            continue
        if not _written_into(copy, since) and _matches(workspace, copy, inventory):
            return copy
    return None


def _matches(workspace, copy, inventory) -> bool:
    """Whether ``copy`` holds exactly the entries a fresh copy would, byte for byte."""
    expected: dict[Path, str] = {}
    for directory, dirs, files in os.walk(workspace):
        names = set(dirs) | set(files)
        ignored = ignored_entries(workspace, directory, names, reuse_virtualenvs=True, inventory=inventory)
        dirs[:] = sorted(set(dirs) - ignored)
        relative = Path(directory).relative_to(workspace)
        expected.update((relative / name, "dir") for name in dirs)
        expected.update((relative / name, "file") for name in set(files) - ignored)
    actual: dict[Path, str] = {}
    for directory, dirs, files in os.walk(copy):
        relative = Path(directory).relative_to(copy)
        actual.update((relative / name, "dir") for name in dirs)
        actual.update((relative / name, "file") for name in files)
    if expected != actual:
        return False
    try:
        return all(
            filecmp.cmp(workspace / path, copy / path, shallow=False)
            for path, kind in expected.items()
            if kind == "file"
        )
    except OSError:
        return False


def setup_note(measured) -> str:
    """One clause for a stop reason: what the attempt's copy cost before the model ran (#2606)."""
    seconds = measured.get("seconds", 0)
    if measured.get("reused"):
        return f"its investigation reused the previous attempt's copy of the workspace ({seconds:g} s)"
    megabytes = measured.get("bytes", 0) / 1_000_000
    return (
        f"{seconds:g} s of that copied the workspace for its investigation "
        f"({measured.get('files', 0):,} files, {megabytes:,.0f} MB)"
    )


def stamp(copy: Path) -> None:
    """Record when this copy finished; its contents keep the source timestamps, so later writes stand out."""
    stamp_path(copy).touch()


def stamp_path(copy: Path) -> Path:
    return copy.with_name(copy.name + STAMP_SUFFIX)


def owner_path(copy: Path) -> Path:
    return copy.with_name(copy.name + OWNER_SUFFIX)


def _written_into(copy: Path, since: float) -> bool:
    """True when anything in the copy is newer than the copy itself.

    ``shutil.copytree`` preserves the source timestamps, so a copy no attempt wrote into stays
    entirely older than its stamp. An entry that cannot be read counts as written: evidence is
    never discarded because of an I/O error.
    """
    for entry in (copy, *copy.rglob("*")):
        try:
            if entry.stat(follow_symlinks=False).st_mtime > since:
                return True
        except OSError:
            return True
    return False


def reclaim_unused(root, *, keep=()) -> list[str]:
    """Delete earlier copies that no attempt wrote into; an untouched copy carries no evidence (#2606).

    A copy finished within ``RECLAIM_GRACE_SECONDS`` is left alone, because another run of the same
    project may still be working in it before its first write.
    """
    root = Path(root)
    kept = {Path(item).resolve() for item in keep}
    deadline = time.time() - RECLAIM_GRACE_SECONDS
    removed = []
    for copy in sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink()):
        try:
            since = stamp_path(copy).stat().st_mtime
        except OSError:
            continue  # a copy made before stamps existed: leave it for a person to judge
        if copy.resolve() in kept or since > deadline or _written_into(copy, since):
            continue
        shutil.rmtree(copy, ignore_errors=True)
        if not copy.exists():
            stamp_path(copy).unlink(missing_ok=True)
            owner_path(copy).unlink(missing_ok=True)
            removed.append(str(copy))
    return removed
