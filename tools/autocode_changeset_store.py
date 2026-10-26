"""The change-set record: runs in different repositories linked for one request (#2066, step 4).

One run is bound to one workspace. When a request spans another repository, the person may link a
run there; the two runs then form a change set. This module keeps that record and nothing else:

- it lives in ``changesets.json`` next to the registry (``autocode_registry.storage_root()``), so
  the same per-user AutoCode directory that lists every registered project also lists their sets;
- one set per source run, with id ``changeset-<hash of the source run directory>``;
- each member names the ``target`` it covers (as the source run's ``view.scope`` names it), the
  registered workspace and its run directory. A member is reserved before its run starts and bound
  to the run directory once it exists, so two links for one target cannot both start a run;
- ``linked_for(run_dir)`` is what a run's status view shows under ``scope.linked``.

Starting runs and reading their status is the change-set layer's job (autocode_changeset). This
module only reads and writes the record; it imports only autocode_util and autocode_registry.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    from . import autocode_registry as registry
    from . import autocode_util as util
except ImportError:
    import autocode_registry as registry
    import autocode_util as util

VERSION = 1
LOCK_TIMEOUT_SECONDS = 1.0


class ChangesetError(Exception):
    """A change-set record that cannot be read or changed as asked."""


def path() -> Path:
    return registry.storage_root() / "changesets.json"


def changeset_id(source_run_dir) -> str:
    resolved = str(Path(source_run_dir).resolve())
    return f"changeset-{hashlib.sha256(resolved.encode()).hexdigest()[:12]}"


def _read() -> dict[str, Any]:
    if not path().exists():
        return {"version": VERSION, "changesets": {}}
    try:
        value = json.loads(path().read_text())
    except (OSError, ValueError) as error:
        raise ChangesetError(f"change sets cannot be read: {error}") from error
    if not isinstance(value, dict) or value.get("version") != VERSION or not isinstance(value.get("changesets"), dict):
        raise ChangesetError("change sets have an unsupported version or shape")
    return value


@contextlib.contextmanager
def _locked() -> Iterator[dict[str, Any]]:
    """The record, read under the change-set lock and saved when the block ends without error."""
    root = registry.storage_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "changesets.lock").open("a+") as handle:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                util.lock_file(handle)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ChangesetError("change sets are busy; retry after a short wait") from None
                time.sleep(0.05)
        try:
            document = _read()
            yield document
            util.atomic_json(path(), document)
        finally:
            util.unlock_file(handle)


def records() -> list[dict]:
    """Every change set, by id."""
    document = _read()
    return [document["changesets"][key] for key in sorted(document["changesets"])]


def find(run_dir) -> dict | None:
    """The change set ``run_dir`` belongs to, as its source or as a member; None when it is in none."""
    wanted = str(Path(run_dir).resolve())
    for record in records():
        if record["source"]["run_dir"] == wanted or any(row.get("run_dir") == wanted for row in record["members"]):
            return record
    return None


def reserve(*, source_workspace, source_run_dir, target: str, workspace, brief: str) -> dict:
    """Add an unstarted member for ``target``; refuse a target that already has one."""
    source_run_dir, workspace = str(Path(source_run_dir).resolve()), str(Path(workspace).resolve())
    key = changeset_id(source_run_dir)
    with _locked() as document:
        record = document["changesets"].setdefault(
            key,
            {
                "id": key,
                "created_at": util.now(),
                "source": {"workspace": str(Path(source_workspace).resolve()), "run_dir": source_run_dir},
                "members": [],
            },
        )
        if any(row["target"] == target for row in record["members"]):
            raise ChangesetError(f"{target} is already linked in {key}")
        member = {"target": target, "workspace": workspace, "run_dir": None, "brief": brief, "linked_at": util.now()}
        record["members"].append(member)
        return {**record, "members": list(record["members"])}


def bind(key: str, target: str, run_dir) -> None:
    """Record the run directory the member for ``target`` started."""
    with _locked() as document:
        for row in document["changesets"][key]["members"]:
            if row["target"] == target:
                row["run_dir"] = str(Path(run_dir).resolve())


def release(key: str, target: str) -> None:
    """Drop the member for ``target`` whose run never started, so the person can link it again."""
    with _locked() as document:
        record = document["changesets"].get(key)
        if record is None:
            return
        record["members"] = [row for row in record["members"] if row["target"] != target or row["run_dir"]]
        if not record["members"]:
            del document["changesets"][key]


def linked_for(run_dir) -> list[dict]:
    """The other runs of ``run_dir``'s change set, for the status view's ``scope.linked``.

    Each row: ``changeset``, ``target`` (the repository that run covers; the source workspace's name
    for the source), ``role`` (``source`` or ``member``), ``workspace`` and ``run_dir`` (None while a
    linked run is still starting). [] when the run is in no set or the record cannot be read."""
    try:
        record = find(run_dir)
    except (ChangesetError, OSError):
        return []
    if record is None:
        return []
    wanted = str(Path(run_dir).resolve())
    source = record["source"]
    rows = [
        {
            "changeset": record["id"],
            "target": Path(source["workspace"]).name,
            "role": "source",
            "workspace": source["workspace"],
            "run_dir": source["run_dir"],
        }
    ]
    rows += [
        {
            "changeset": record["id"],
            "target": row["target"],
            "role": "member",
            "workspace": row["workspace"],
            "run_dir": row.get("run_dir"),
        }
        for row in record["members"]
    ]
    return [row for row in rows if row["run_dir"] != wanted]


def set_fields(key: str, fields: dict) -> dict:
    """Replace the given top-level fields of change set ``key`` (verification and delivery, #2066 step 6)."""
    with _locked() as document:
        record = document["changesets"].get(key)
        if record is None:
            raise ChangesetError(f"no change set {key}")
        record.update(fields)
        return dict(record)
