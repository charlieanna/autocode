"""Bounded, read-only workspace diffs for paths named by public review evidence.

The diff is a review aid, not authenticated execution proof. It uses immutable
base blobs and ordinary current workspace files, never private run state.
"""

from __future__ import annotations

import difflib
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath

MAX_FILES = 20
MAX_FILE_BYTES = 128 * 1024
MAX_DIFF_BYTES = 1024 * 1024
GIT_TIMEOUT = 5


def _git(root, *args):
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "--literal-pathspecs",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )
    if result.returncode:
        raise ValueError("The run's base blob could not be read from this repository.")
    return result.stdout


def _path(value):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("The coverage path is not an ordinary relative file path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part.casefold() in ("", ".", "..", ".git", ".autocode") for part in value.split("/")):
        raise ValueError("The coverage path leaves the product-file review scope.")
    return path


def _current(root, relative):
    """Open beneath a directory descriptor, refusing symlinks at every component."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("The current path is not an ordinary file.")
            if info.st_size > MAX_FILE_BYTES:
                raise ValueError(f"The current file exceeds the {MAX_FILE_BYTES}-byte review limit.")
            data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"The current file exceeds the {MAX_FILE_BYTES}-byte review limit.")
            after = os.fstat(stream.fileno())
            identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            if identity(after) != identity(info):
                raise ValueError("The current file changed while its diff was being read.")
            return data
    except FileNotFoundError:
        return None  # A deletion is meaningful if the base contains this path.
    finally:
        os.close(directory)


def _base(root, base, relative):
    # ls-tree distinguishes an added path from a missing/invalid base object.
    output = _git(root, "ls-tree", "-z", base, "--", str(relative))
    if not output:
        return None
    entries = output.rstrip(b"\0").split(b"\0")
    if len(entries) != 1:
        raise ValueError("The base path does not name one ordinary file.")
    metadata, _ = entries[0].split(b"\t", 1)
    mode, kind, oid = metadata.split(b" ")
    if kind != b"blob" or mode not in (b"100644", b"100755"):
        raise ValueError("The base path is not an ordinary file.")
    size = int(_git(root, "cat-file", "-s", oid.decode()).strip())
    if size > MAX_FILE_BYTES:
        raise ValueError(f"The base file exceeds the {MAX_FILE_BYTES}-byte review limit.")
    data = _git(root, "cat-file", "blob", oid.decode())
    if len(data) != size or len(data) > MAX_FILE_BYTES:
        raise ValueError("The base blob size changed while its diff was being read.")
    return data


def _diff(path, old, new):
    if old is None and new is None:
        raise ValueError("The path is absent from both the run's base and the current checkout.")
    if b"\0" in (old or b"") or b"\0" in (new or b""):
        raise ValueError("Binary file contents are not shown as a text diff.")
    try:
        before, after = (old or b"").decode("utf-8"), (new or b"").decode("utf-8")
    except UnicodeError as error:
        raise ValueError("The file is not UTF-8 text; its text diff is unavailable.") from error
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="/dev/null" if old is None else "a/" + path,
        tofile="/dev/null" if new is None else "b/" + path,
    )
    # Preserve missing-final-newline differences as explicit unified-diff markers.
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def collect(workspace, view):
    """Read only public coverage paths; return unavailable reasons without guessing."""
    evidence = view.get("evidence") if isinstance(view, dict) else None
    evidence = evidence if isinstance(evidence, dict) else {}
    coverage = evidence.get("diff_coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    base = evidence.get("base_commit")
    result = {
        "base_commit": base,
        "availability": "unavailable",
        "meaning": "Run-level diff of the current checkout against the run's base; not criterion-specific or proof that these bytes were tested.",
        "files": [],
    }
    paths = [row.get("path") for row in coverage.get("files") or [] if isinstance(row, dict)]
    paths.extend(coverage.get("unmeasured") or [])
    paths = list(dict.fromkeys(path for path in paths if isinstance(path, str)))
    if not paths:
        result["reason"] = "No file paths were supplied by the public diff-coverage evidence."
        return result
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", base) is None:
        result["reason"] = "The status view does not name a full base commit identity."
        return result
    root = Path(workspace)
    total = 0
    for name in paths[:MAX_FILES]:
        row = {"path": name, "availability": "unavailable", "text": None}
        try:
            relative = _path(name)
            old, new = _base(root, base, relative), _current(root, relative)
            text = _diff(name, old, new)
            size = len(text.encode("utf-8"))
            if total + size > MAX_DIFF_BYTES:
                raise ValueError(f"The combined diff exceeds the {MAX_DIFF_BYTES}-byte review limit.")
            total += size
            row.update(
                availability="available", text=text, reason=None if text else "No content difference from the base."
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            row["reason"] = str(error)
        result["files"].append(row)
    if len(paths) > MAX_FILES:
        result["reason"] = f"Only the first {MAX_FILES} of {len(paths)} coverage paths are shown."
    available = sum(row["availability"] == "available" for row in result["files"])
    if available:
        result["availability"] = "available" if available == len(paths) else "partial"
    return result
