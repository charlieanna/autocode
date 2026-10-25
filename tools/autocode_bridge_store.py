"""One relay record per bridged issue, kept outside every task workspace.

The records bind a GitHub issue to the durable run the bridge started for it,
the gate state it last relayed to the issue (question ids, or the plan token a
person was shown), and the comments it has already consumed. Control tokens are
never written here: every decision is taken against a fresh status view, whose
needs carry the current token. Storage mirrors the worker and attention roots
(owner-private directory, atomic JSON), so a restarted bridge resumes its relays
without holding tokens or a second controller.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

VERSION = 1
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
# Comments accumulate; keep the record bounded by remembering ids only, newest last.
REMEMBERED_COMMENTS = 200


def root(create: bool = True) -> Path:
    """The bridge's private storage root; each host has one bridge per root."""
    selected = (
        os.environ.get("AUTOCODE_BRIDGE_HOME")
        or (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "autocode" / "bridge")
    )
    selected = Path(selected)
    if create:
        selected.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = selected.stat()
    if selected.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Bridge storage must be an owned directory")
    if hasattr(os, "getuid") and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
        raise ValueError("Bridge storage must be private to its owner")
    return selected


def slug(owner: str, repo: str, number: int) -> str:
    value = f"{owner}-{repo}-{number}".lower()
    if not SLUG.fullmatch(value):
        raise ValueError(f"invalid issue identity {owner}/{repo}#{number}")
    return value


def path(owner: str, repo: str, number: int) -> Path:
    return root(create=False) / f"{slug(owner, repo, number)}.json"


def load(owner: str, repo: str, number: int) -> dict | None:
    """The saved relay record, or None when the bridge never took this issue."""
    selected = path(owner, repo, number)
    if not selected.is_file():
        return None
    try:
        record = util.read_object(selected)
    except (OSError, ValueError):
        raise ValueError(f"the bridge record for {owner}/{repo}#{number} is unreadable; inspect {selected}") from None
    if not isinstance(record, dict) or record.get("version") != VERSION:
        raise ValueError(f"the bridge record for {owner}/{repo}#{number} has an unsupported version")
    return record


def save(record: dict) -> None:
    required = ("owner", "repo", "number", "project", "gate")
    if not record.get("failed"):
        required = (*required, "worktree", "branch", "run_dir")
    for key in required:
        if not record.get(key):
            raise ValueError(f"a bridge record requires {key}")
    record["version"] = VERSION
    util.atomic_json(path(record["owner"], record["repo"], record["number"]), record)


def records() -> list[dict]:
    """Every relay record, by issue number order."""
    found = []
    for selected in sorted(root(create=False).glob("*.json")):
        try:
            value = util.read_object(selected)
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("version") == VERSION:
            found.append(value)
    found.sort(key=lambda row: (row.get("repo") or "", row.get("number") or 0))
    return found


def seen_comments(record: dict, ids: list[int]) -> dict:
    """The record's comment ledger extended with newly consumed ids, bounded."""
    remembered = [value for value in record.get("comments_seen", []) if isinstance(value, int)]
    return {"comments_seen": (remembered + [value for value in ids if value not in remembered])[-REMEMBERED_COMMENTS:]}
