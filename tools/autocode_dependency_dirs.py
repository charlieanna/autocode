"""Installed dependency directories: third-party code a package manager writes, never the task's product code.

A project that does not ignore its node_modules shows one `npm install` as thousands of untracked changed
files. Counted as changed product lines, one install made 274,546 obligations, and the Tester's handoff
(68 MB) and report schema (32 MB) were too large to launch (#3164).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
from pathlib import Path, PurePosixPath

NAMES = ("node_modules", ".venv", "venv")
REASON = "Installed third-party code, not product code the task changed: no test or reason is required."
# Where a scratch tree keeps its views of installed packages: AutoCode's own directory there.
VIEWS = ".autocode/dependencies"


def installed(path: str) -> bool:
    """Whether ``path`` is a file inside an installed dependency directory, at any depth."""
    return any(part in NAMES for part in PurePosixPath(path).parts[:-1])


def summary(paths, *, listed: int = 20) -> dict:
    """The record of the installed files an inventory left out: their count and (the first ``listed``)
    dependency directories, never every path."""
    chosen = [path for path in paths if installed(path)]
    directories = set()
    for path in chosen:
        parts = PurePosixPath(path).parts
        depth = next(index for index, part in enumerate(parts[:-1]) if part in NAMES)
        directories.add("/".join(parts[: depth + 1]))
    return {"files": len(chosen), "directories": sorted(directories)[:listed], "reason": REASON}


def expose(original, target, view, *, live=(), tree=None) -> dict:
    """Let the scratch copy at ``target`` use what is installed at ``original``, the person's own directory.

    A link to ``node_modules`` itself hands a command run in the copy the person's installed packages:
    `npm ci` empties the directory behind the link before it installs, so one planned check deleted every
    package in the workspace, and each later check failed with "Cannot find module". The copy gets a
    directory of its own, ``view``, instead: a link to each installed package and a copy of each file the
    package manager keeps beside them (`npm install` rewrites `.package-lock.json`). Emptying, replacing or
    reinstalling the copy's ``node_modules`` then changes ``view`` and nothing the person installed.
    ``target`` is still one link, so the copy's files look as they did.

    ``live`` names the live source roots (the workspace and the checkouts its dependencies come from) and
    ``tree`` the copy they map to. With them the copy also keeps custody of what the installed directory
    points back at (#4152, phase 2): an installed directory is not source, but an editable install's `.pth`
    file, its finder, a script's `#!` line or a package link (`npm link`, a workspace package) leads from it
    into the live tree, so a test in the copy of the original code read the candidate's source through it.
    In ``view`` each of those is re-pointed at the copy's own path (``custody``). Returns what was re-pointed.
    Without ``live`` a virtualenv is linked whole, as before.
    """
    original, target, view = Path(original), Path(target), Path(view)
    record: dict[str, list] = {"relinked": [], "rewritten": []}
    if original.name != "node_modules":
        if not live or tree is None:
            target.symlink_to(original, target_is_directory=True)
            return record
        _venv_view(original, view, _pairs(live, tree, ((original, target),)), record)
    else:
        _node_view(original, view, _pairs(live, tree) if live and tree is not None else [], record, scoped=False)
    target.symlink_to(view, target_is_directory=True)
    return record


# Files of a virtualenv that name paths and are read at startup or when a script runs: what custody rewrites.
_SITE_FILES = (".pth", ".egg-link", ".py")
_TEXT_LIMIT = 1_000_000


def _forms(path) -> list[str]:
    path = Path(path).absolute()
    forms = {os.path.normpath(path)}
    with contextlib.suppress(OSError, RuntimeError):
        forms.add(str(path.resolve()))
    return sorted(forms)


def _pairs(live, tree, extra=()) -> list[tuple[str, str]]:
    """(live prefix, copy prefix) pairs, longest first: ``extra`` (a virtualenv and the copy's link to it),
    then each live root, which maps to ``tree``."""
    pairs: dict[str, str] = {}
    for source, copy in (*extra, *((root, tree) for root in live)):
        for form in _forms(source):
            pairs.setdefault(form, os.path.normpath(Path(copy).absolute()))
    return sorted(pairs.items(), key=lambda pair: -len(pair[0]))


def _mapped(path: str, pairs, *, source_only=False) -> str | None:
    """The copy's path for ``path`` inside a live root, else None. ``source_only`` leaves installed paths."""
    for live, copy in pairs:
        if path == live or path.startswith(live.rstrip(os.sep) + os.sep):
            relative = PurePosixPath(Path(path).relative_to(live).as_posix())
            if source_only and any(part in NAMES for part in relative.parts):
                return None
            return os.path.join(copy, *relative.parts) if relative.parts else copy
    return None


def _rewrite(data: bytes, pairs) -> bytes:
    if not pairs:
        return data
    pattern = re.compile(b"(" + b"|".join(re.escape(os.fsencode(live)) for live, _ in pairs) + b")(?![A-Za-z0-9._-])")
    copies = {os.fsencode(live): os.fsencode(copy) for live, copy in pairs}
    return pattern.sub(lambda match: copies[match.group(1)], data)


def _link_target(directory: str, link: str) -> str:
    return os.path.normpath(link if os.path.isabs(link) else os.path.join(directory, link))


def _custody_link(source: str, destination: Path, pairs, record, *, within=None, source_only=False) -> None:
    """Recreate the link ``source`` at ``destination``: kept as written when it stays inside ``within`` (a
    relative link inside the mirrored directory), re-pointed when it leads into a live root."""
    link = os.readlink(source)
    full = _link_target(os.path.dirname(source), link)
    if not os.path.isabs(link) and within is not None and (full == within or full.startswith(within + os.sep)):
        destination.symlink_to(link)
        return
    mapped = _mapped(full, pairs, source_only=source_only)
    if mapped is not None:
        record["relinked"].append({"path": source, "from": full, "to": mapped})
    destination.symlink_to(mapped if mapped is not None else (full if not os.path.isabs(link) else link))


def _copy_rewritten(source: str, destination: Path, pairs, record) -> None:
    data = Path(source).read_bytes()
    text = _rewrite(data, pairs)
    destination.write_bytes(text)
    shutil.copymode(source, destination)
    if text != data:
        record["rewritten"].append(source)


def _venv_view(original: Path, view: Path, pairs, record) -> None:
    """A virtualenv of the copy's own: its directories down to each ``site-packages`` and ``bin`` are real,
    every other entry a link to the person's; the files that name paths (`.pth`, editable finders and
    egg-links, top-level modules, scripts' `#!` lines, ``pyvenv.cfg``) are copies re-pointed at the copy.
    Python finds ``pyvenv.cfg`` beside the path it was started by, not the resolved one, so the copy's
    interpreter is this environment and reads this ``site-packages``."""
    descend = {PurePosixPath("."), PurePosixPath("bin")}
    for site in original.glob("lib*/python*/site-packages"):
        relative = PurePosixPath(site.relative_to(original).as_posix())
        if any((original / parent).is_symlink() for parent in (relative, *relative.parents)):
            continue
        descend.update((relative, *relative.parents))
    root = os.path.normpath(original.absolute())

    def mirror(relative: PurePosixPath) -> None:
        directory, destination = original / relative, view / relative
        destination.mkdir(parents=True, exist_ok=True)
        with os.scandir(directory) as entries:
            for entry in entries:
                inner, target = relative / entry.name, destination / entry.name
                if entry.is_symlink():
                    _custody_link(entry.path, target, pairs, record, within=root)
                elif entry.is_dir() and inner in descend:
                    mirror(inner)
                elif entry.is_file() and _venv_text(relative, entry):
                    _copy_rewritten(entry.path, target, pairs, record)
                else:
                    target.symlink_to(entry.path, target_is_directory=entry.is_dir())

    mirror(PurePosixPath("."))


def _venv_text(relative: PurePosixPath, entry) -> bool:
    try:
        if entry.stat().st_size > _TEXT_LIMIT:
            return False
        if relative == PurePosixPath("."):
            return entry.name == "pyvenv.cfg"
        if relative.name == "site-packages":
            return entry.name.endswith(_SITE_FILES)
        if relative == PurePosixPath("bin"):
            with open(entry.path, "rb") as handle:
                return handle.read(2) == b"#!" or entry.name.startswith("activate")
    except OSError:
        return False
    return False


def _node_view(original: Path, view: Path, pairs, record, *, scoped: bool) -> None:
    """``node_modules`` of the copy's own: a link to each installed package, a copy of each file beside them.
    With ``pairs``, a package that links into live source is linked to the copy's own path instead, and a
    scope (``@scope``) and ``.bin`` get directories of their own, so the same holds one level down."""
    view.mkdir(parents=True)
    with os.scandir(original) as entries:
        for entry in entries:
            target = view / entry.name
            if entry.is_file(follow_symlinks=False):
                shutil.copy2(entry.path, target)
            elif (
                pairs
                and entry.is_symlink()
                and _mapped(_link_target(str(original), os.readlink(entry.path)), pairs, source_only=True)
            ):
                _custody_link(entry.path, target, pairs, record, source_only=True)
            elif (
                pairs
                and not scoped
                and entry.is_dir(follow_symlinks=False)
                and (entry.name.startswith("@") or entry.name == ".bin")
            ):
                if entry.name == ".bin":
                    _bin_view(Path(entry.path), target, pairs, record)
                else:
                    _node_view(Path(entry.path), target, pairs, record, scoped=True)
            else:
                target.symlink_to(entry.path, target_is_directory=entry.is_dir())


def _bin_view(original: Path, view: Path, pairs, record) -> None:
    """``.bin`` links are relative to ``node_modules`` (``../dep/cli.js``): kept as written, they resolve
    through the copy's view, so a command reaches the package the copy has."""
    view.mkdir()
    with os.scandir(original) as entries:
        for entry in entries:
            target = view / entry.name
            if entry.is_symlink():
                _custody_link(entry.path, target, pairs, record, within=os.path.normpath(original.parent.absolute()))
            elif entry.is_file(follow_symlinks=False):
                shutil.copy2(entry.path, target)
            else:
                target.symlink_to(entry.path, target_is_directory=entry.is_dir())


def view_in(tree, package=".", name="node_modules") -> Path:
    """Where the scratch ``tree`` keeps its view of the ``node_modules`` of ``package`` (a directory in it).

    Under AutoCode's own directory in the tree, which Git is told to ignore there, so no listing of the
    tree's files shows the view, and removing the tree removes it."""
    views = Path(tree) / VIEWS
    views.mkdir(parents=True, exist_ok=True)
    ignore = views / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    return views / package / name
