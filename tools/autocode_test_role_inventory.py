"""Read Python role facts from one original tree, never from candidate metadata."""

from __future__ import annotations

import io
import os
import subprocess
import tokenize
from functools import cache
from pathlib import Path

try:
    from . import autocode_python_test_roles as policy
    from . import autocode_suite_definition_inputs as definition_inputs
    from . import autocode_util as util
except ImportError:
    import autocode_python_test_roles as policy
    import autocode_suite_definition_inputs as definition_inputs
    import autocode_util as util


def _text(path, data):
    encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0] if path.endswith(".py") else "utf-8"
    return data.decode(encoding)


def _git(workspace, *args):
    result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True)
    if result.returncode:
        raise ValueError("Cannot read the original role inventory: " + result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def blob(workspace, oid):
    return _git(workspace, "cat-file", "blob", oid)


def entries(workspace, base):
    result = {}
    for entry in _git(workspace, "ls-tree", "-r", "-z", base).split(b"\0"):
        if entry:
            header, name = entry.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            result[name.decode("utf-8")] = (mode, kind, oid)
    return result


def _candidate(workspace, changes):
    sources, links = {}, {}
    for name, status in (changes or {}).items():
        if status == "deleted":
            sources[name] = links[name] = None
            continue  # A deleted oracle retains its original role.
        path = Path(workspace) / name
        if path.is_symlink():
            sources[name] = links[name] = os.readlink(path)
        elif path.is_file():
            sources[name] = _text(name, path.read_bytes()) if name.endswith(".py") else ""
            links[name] = None
    return sources, links


def matches(roles, workspace, changes):
    sources, links = _candidate(workspace, changes)
    return sources == roles.candidate_sources and links == roles.candidate_links


def from_entries(workspace, original, read, hint, *, changes=None):
    """Freeze facts while the caller's pinned original reader is still open.

    Existing original-tree providers use mode as their first tuple member.
    Only changed Python source is read from the candidate; its manifests cannot
    replace the original manifest used to establish runtime ownership.
    """
    links = {name: read(name) for name, value in original.items() if value[0] == "120000"}
    candidates, candidate_links = _candidate(workspace, changes)
    return policy.build(original, read, hint, candidates=candidates, links=links, candidate_links=candidate_links)


def from_git(workspace, base, hint, *, changes=None):
    original = entries(workspace, base)

    blobs = {name: (value[0], value[2]) for name, value in original.items()}
    with definition_inputs.blob_reader(workspace, blobs, None) as read:
        return from_entries(workspace, original, read, hint, changes=changes)


def from_workspace(workspace, hint, *, snapshot=None):
    snapshot = snapshot or util.snapshot(workspace)["files"]
    names = {name: value for name, value in snapshot.items() if value != "deleted"}
    links = {name: os.readlink(Path(workspace) / name) for name in names if (Path(workspace) / name).is_symlink()}

    @cache
    def read(name):
        if name in links:
            return links[name]
        return _text(name, (Path(workspace) / name).read_bytes())

    return policy.build(names, read, hint, links=links)
