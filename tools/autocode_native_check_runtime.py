"""Exact approved capture-runtime files reused by readonly native checks.

The original check owner supplies the authenticated contract body. This helper
grants no task authority, discovers no dependency paths, and starts no process.
"""

import json
import stat
from pathlib import Path

try:
    from . import autocode_native_capture_policy as capture_policy
    from . import autocode_util as util
    from . import autocode_visual_profile as profiles
except ImportError:
    import autocode_native_capture_policy as capture_policy
    import autocode_util as util
    import autocode_visual_profile as profiles


LOGICAL_NODE = "/opt/homebrew/bin/node"


def _require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native check runtime: " + reason)


def inputs(workspace, contract_body, *, setup=False):
    """Reauthenticate the existing exact-file input; Git setup gets no grant."""
    if setup:
        return None
    constraints = contract_body.get("constraints", [])
    _require(
        isinstance(constraints, list) and all(isinstance(row, str) for row in constraints),
        "invalid authenticated contract constraints",
    )
    rows = [row[len(profiles.MARKER) :] for row in constraints if row.startswith(profiles.MARKER)]
    if not rows:
        return None
    _require(len(rows) == 1, "ambiguous approved runtime profile")
    profile = profiles.profile_value(profiles._json(rows[0]))
    if "capture_runtime" not in profile:
        return None
    runtime, _pins = capture_policy.runtime_inputs(workspace, profile)
    logical = Path(LOGICAL_NODE)
    _require(
        logical.is_absolute() and logical.parent.resolve(strict=True) == logical.parent,
        "logical Node parent is missing or aliased",
    )
    before = logical.lstat()
    target = logical.resolve(strict=True)
    _require(stat.S_ISLNK(before.st_mode) or stat.S_ISREG(before.st_mode), "logical Node is not a file or exact alias")
    _require(str(target) == runtime["node"], "logical Node differs from the pinned runtime")
    after = logical.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    identity = {name: getattr(before, name) for name in fields}
    _require(
        identity == {name: getattr(after, name) for name in fields} and logical.resolve(strict=True) == target,
        "logical Node changed during authentication",
    )
    return {
        "manifest": dict(profile["capture_runtime"]),
        "node": runtime["node"],
        "files": dict(runtime["files"]),
        "logical_node": {"path": str(logical), "target": str(target), "identity": identity},
    }


def policy(value):
    """Only authenticated regular-file literals, never parent directory roots."""
    if value is None:
        return ""
    return (
        "(allow file-read* "
        + " ".join("(literal " + json.dumps(name, ensure_ascii=False) + ")" for name in sorted(value["files"]))
        + ")\n"
    )
