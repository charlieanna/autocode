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
    seen = set()
    for record in document["changesets"].values():
        for row in members(record):
            if row["run_dir"] and row["run_dir"] in seen:
                raise ChangesetError("overlapping legacy change sets require membership reconciliation before use")
            if row["run_dir"]:
                seen.add(row["run_dir"])
    return [document["changesets"][key] for key in sorted(document["changesets"])]


def members(record: dict) -> list[dict]:
    """Canonical public membership, shared by status, admission and delivery."""
    source = record["source"]
    return [{"role": "source", "target": Path(source["workspace"]).name, **source}] + [
        {
            "role": row.get("role", "member"),
            "target": row["target"],
            "workspace": row["workspace"],
            "run_dir": row.get("run_dir"),
        }
        for row in record["members"]
    ]


def _find(document: dict, run_dir) -> dict | None:
    wanted = str(Path(run_dir).resolve())
    matches = [r for r in document["changesets"].values() if any(m["run_dir"] == wanted for m in members(r))]
    if len(matches) > 1:
        raise ChangesetError("overlapping legacy change sets require membership reconciliation before use")
    return matches[0] if matches else None


def _touch(record: dict) -> None:
    record["revision"] = int(record.get("revision", 0)) + 1


def _membership_changed(record: dict) -> None:
    _archive_released_approval(record)
    record["approval"] = None
    _touch(record)


def _archive_released_approval(record: dict, observations=None) -> None:
    approval = record.get("approval") or {}
    if not approval.get("released"):
        return
    history = record.setdefault("approval_history", [])
    history.append(
        {
            **approval,
            "observations": json.loads(
                json.dumps(record.get("approval_observations") or {} if observations is None else observations)
            ),
        }
    )
    del history[:-16]


def find(run_dir) -> dict | None:
    """The change set ``run_dir`` belongs to, as its source or as a member; None when it is in none."""
    return _find(_read(), run_dir)


def memberships() -> dict[str, str]:
    """Each run directory of a change set, resolved, with that set's id; {} when the record cannot be read."""
    try:
        found = records()
    except (ChangesetError, OSError):
        return {}
    return {
        str(run_dir): record["id"]
        for record in found
        for run_dir in (record["source"]["run_dir"], *(row.get("run_dir") for row in record["members"]))
        if run_dir
    }


def reserve(*, source_workspace, source_run_dir, target: str, workspace, brief: str, role="member") -> dict:
    """Add an unstarted member for ``target``; refuse a target that already has one."""
    if role not in ("member", "joint"):
        raise ChangesetError("change-set participant role must be member or joint")
    source_run_dir, workspace = str(Path(source_run_dir).resolve()), str(Path(workspace).resolve())
    with _locked() as document:
        existing = _find(document, source_run_dir)
        key = existing["id"] if existing else changeset_id(source_run_dir)
        record = document["changesets"].setdefault(
            key,
            {
                "id": key,
                "created_at": util.now(),
                "source": {"workspace": str(Path(source_workspace).resolve()), "run_dir": source_run_dir},
                "members": [],
            },
        )
        if any(row["target"] == target for row in members(record)):
            raise ChangesetError(f"{target} is already linked in {key}")
        if any(row["workspace"] == workspace for row in members(record)):
            raise ChangesetError(f"{workspace} already belongs to {key}; do not start another member there")
        member = {
            "target": target,
            "workspace": workspace,
            "run_dir": None,
            "brief": brief,
            "linked_at": util.now(),
            **({"role": role} if role != "member" else {}),
        }
        record["members"].append(member)
        _membership_changed(record)
        return {**record, "members": list(record["members"])}


def bind(key: str, target: str, run_dir) -> None:
    """Record the run directory the member for ``target`` started."""
    with _locked() as document:
        owner = _find(document, run_dir)
        if owner is not None:
            raise ChangesetError(f"{run_dir} already belongs to {owner['id']}")
        record = document["changesets"][key]
        for row in record["members"]:
            if row["target"] == target:
                if row.get("run_dir"):
                    raise ChangesetError(f"{target} already has a run")
                row["run_dir"] = str(Path(run_dir).resolve())
                _membership_changed(record)
                return
        raise ChangesetError(f"{target} has no reserved member in {key}")


def release(key: str, target: str) -> None:
    """Drop the member for ``target`` whose run never started, so the person can link it again."""
    with _locked() as document:
        record = document["changesets"].get(key)
        if record is None:
            return
        record["members"] = [row for row in record["members"] if row["target"] != target or row["run_dir"]]
        _membership_changed(record)
        if not record["members"]:
            del document["changesets"][key]


def remove_joint(source_run_dir, joint_run_dir) -> bool:
    """Retire one completed internal joint participant and withdraw any old set approval."""
    source_run_dir = str(Path(source_run_dir).resolve())
    joint_run_dir = str(Path(joint_run_dir).resolve())
    with _locked() as document:
        record = _find(document, source_run_dir)
        if record is None:
            return False
        kept = [
            row
            for row in record["members"]
            if not (row.get("role") == "joint" and row.get("run_dir") == joint_run_dir)
        ]
        if len(kept) == len(record["members"]):
            return False
        record["members"] = kept
        _membership_changed(record)
        return True


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
            "role": row.get("role", "member"),
            "workspace": row["workspace"],
            "run_dir": row.get("run_dir"),
        }
        for row in record["members"]
    ]
    return [row for row in rows if row["run_dir"] != wanted and row["role"] in ("source", "member")]


def set_fields(key: str, fields: dict, *, expected_revision: int | None = None) -> dict:
    """Replace the given top-level fields of change set ``key`` (verification and delivery, #2066 step 6)."""
    with _locked() as document:
        record = document["changesets"].get(key)
        if record is None:
            raise ChangesetError(f"no change set {key}")
        if expected_revision is not None and int(record.get("revision", 0)) != expected_revision:
            raise ChangesetError("the change set changed during this action; inspect its current status")
        record.update(fields)
        _touch(record)
        return dict(record)


def begin_approval(run_dir, token: str, plans: dict[str, str], expected_members: list[dict]) -> None:
    """Hold the whole membership before any participant receives an approval."""
    with _locked() as document:
        record = _find(document, run_dir)
        if record is None or members(record) != expected_members:
            raise ChangesetError("change-set membership changed; inspect every plan again")
        if set(plans) != {row["run_dir"] for row in members(record)} or not all(plans.values()):
            raise ChangesetError("every change-set member needs a current plan")
        current = record.get("approval") or {}
        if current.get("released") and current.get("token") != token:
            _archive_released_approval(record)
        record["approval"] = {"token": token, "plans": dict(plans), "released": False}
        _touch(record)


def release_approval(run_dir, token: str) -> None:
    """Release only the exact cohort whose core checkpoints durably acknowledged approval."""
    with _locked() as document:
        record = _find(document, run_dir)
        approval = (record or {}).get("approval") or {}
        if (
            record is None
            or approval.get("token") != token
            or set(approval.get("plans", {})) != {row["run_dir"] for row in members(record)}
        ):
            raise ChangesetError("change-set membership or plans changed; approvals remain held")
        observations = record.get("approval_observations") or {}
        for member, plan in approval["plans"].items():
            observation = observations.get(member) or {}
            grant = observation.get("grant") or {}
            current_receipt = (
                observation.get("plan_token") == plan
                and observation.get("approved")
                and grant.get("set_token") == token
                and grant.get("plan_token") == plan
                and grant.get("task_digest") == observation.get("task_digest")
            )
            historical_receipt = any(
                prior.get("released")
                and prior.get("plans", {}).get(member) == plan
                and (prior.get("observations", {}).get(member) or {}).get("task_digest")
                == observation.get("task_digest")
                and (prior.get("observations", {}).get(member) or {}).get("plan_token") == plan
                and (prior.get("observations", {}).get(member) or {}).get("approved")
                and ((prior.get("observations", {}).get(member) or {}).get("grant") or {}).get("set_token")
                == prior.get("token")
                and ((prior.get("observations", {}).get(member) or {}).get("grant") or {}).get("plan_token")
                == plan
                and ((prior.get("observations", {}).get(member) or {}).get("grant") or {}).get("task_digest")
                == (prior.get("observations", {}).get(member) or {}).get("task_digest")
                for prior in record.get("approval_history", [])
            )
            if not (current_receipt or historical_receipt):
                raise ChangesetError("not every member durably accepted this set; approvals remain held")
        approval["released"] = True
        _touch(record)


@contextlib.contextmanager
def publishing_approval(run_dir, observation: dict) -> Iterator[None]:
    """Fence a core checkpoint using only that core's own public approval observation.

    Invalidation is durable *before* changed task/plan state is written. An interrupted
    write leaves a hold, never a speculative approval. Acknowledgement follows the
    successful core save while the membership lock still excludes set release.
    """
    if not path().exists():
        yield
        return
    with _locked() as document:
        record = _find(document, run_dir)
        if record is None:
            yield
            return
        key = str(Path(run_dir).resolve())
        observations = record.setdefault("approval_observations", {})
        if observations.get(key) == observation:
            yield
            return
        prior_observations = json.loads(json.dumps(observations))
        approval = record.get("approval")
        if approval and approval.get("released"):
            _archive_released_approval(record, observations=prior_observations)
        observations.pop(key, None)
        if approval:
            approval["released"] = False
        _touch(record)
        util.atomic_json(path(), document)
        yield
        observations[key] = observation
        _touch(record)
