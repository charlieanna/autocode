"""Classify Builder work retained across attempts and equivalent repair assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import json
from pathlib import Path

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_assignment as assignment
    from . import autocode_contract_identity as contracts
    from . import autocode_preservation_candidate as preservation
    from . import autocode_retained_receipts as retained_receipts
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_assignment as assignment
    import autocode_contract_identity as contracts
    import autocode_preservation_candidate as preservation
    import autocode_retained_receipts as retained_receipts
    import autocode_util as util
    import autocode_verification_plan as verification_plan


def _reapproved_scope(state: RunState, previous, task):
    """Same-turn retained source is only a candidate under fresh equivalent scope approval."""
    try:
        current = state.get("goal_contract") or {}
        if not contracts.approved(state) or set(previous.get("acceptance_criteria") or []) != set(
            task.get("acceptance_criteria") or []
        ):
            return False
        history = [
            row for row in state.get("contract_history") or [] if row.get("hash") == previous.get("contract_hash")
        ]
        if not history or any(row != history[0] for row in history[1:]):
            return False
        old = history[0]
        if old["task_id"] != current["task_id"] or any(
            old["body"].get(key) != current["body"].get(key) for key in ("permission_boundaries", "constraints")
        ):
            return False
        approval = contracts.historical_approval(state, old)
        events = state["user_events"]
        if approval is None or events.count(current["approval_event"]) != 1:
            return False
        boundary = -1
        if state.get("turns"):
            event_id = state["turns"][-1]["event_id"]
            indices = [i for i, event in enumerate(events) if event.get("id") == event_id]
            if len(indices) != 1:
                return False
            boundary = indices[0]
        return boundary < events.index(approval) < events.index(current["approval_event"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _earlier_assignment(state: RunState, record):
    """An unchanged repair may inherit work from the same approved milestone/scope.

    Resolver handoffs create a new task ID. That must not erase the snapshots
    of a Builder that left source before its report failed. Reapproval requires
    authenticated equivalent scope; milestones and ownership cannot change. This selects a baseline
    for fresh review only, never a previous validation verdict.
    """
    task = state.get("current_task") or {}
    contract = (state.get("goal_contract") or {}).get("hash")
    members = task.get("milestone_ids") or [task.get("milestone_id")]
    owned = {path.rstrip("/") for path in task.get("affected_paths") or []}
    if (
        not contract
        or task.get("contract_hash") != contract
        or not all(members)
        or record.get("task_id") != task.get("id")
    ):
        return None
    for previous in reversed(state.get("task_archive") or []):
        if (
            previous.get("rolled_back")
            or previous.get("kind") != "implement"
            or previous.get("contract_hash") != contract
            and not _reapproved_scope(state, previous, task)
            or set(previous.get("milestone_ids") or [previous.get("milestone_id")]) != set(members)
            or {path.rstrip("/") for path in previous.get("affected_paths") or []} != owned
        ):
            continue
        origin = dict(record, task_id=previous.get("id"))
        changes = assignment.retained_changes(state.get("stages", []), origin)
        if changes is None:
            return None
        if changes:
            return origin
    return None


def fresh_candidate(state: RunState, record, current_revision):
    """Return in-scope retained paths for fresh review, without claiming they pass.

    A no-diff retry may follow an interrupted or rejected Builder that left useful
    source changes. Compare the whole assignment with its earliest saved snapshot;
    an empty files dictionary is a valid baseline. The current tree and saved after
    snapshot must still describe the same source revision.
    """
    task = state.get("current_task") or {}
    owned = task.get("affected_paths") or []
    if (
        record.get("changed_files")
        or record.get("rolled_back")
        or task.get("kind") != "implement"
        or not owned
        or not state.get("goal_contract")
    ):
        return None
    stages, workspace = state.get("stages", []), state.get("workspace")
    origin = record
    if assignment.retained_changes(stages, record) == []:
        origin = _earlier_assignment(state, record) or record
    # A compiled program a build left behind is neither out of scope nor part of the
    # candidate: the Validator reviews source (autocode_assignment.build_output).
    paths = [
        path
        for path in assignment.retained_changes(stages, origin) or []
        if not assignment.build_output(workspace, path, stages, origin)
    ]
    archived = acceptance_tests.repaired_protected(state)
    if not paths or assignment.outside(owned, stages, origin, workspace=workspace, archived=archived) != []:
        return (
            (
                unchanged_recheck(state, record, current_revision)
                or preservation.candidate(state, record, current_revision)
            )
            if not paths
            else None
        )
    try:
        after = json.loads(Path(record["after_ref"]).read_text())
    except (KeyError, OSError, ValueError):
        return None
    revision = record.get("source_revision")
    if not isinstance(after, dict) or not revision or after.get("revision") != revision or current_revision != revision:
        return None
    return {
        "source_revision": revision,
        "retained_paths": paths,
        **({"origin_task_id": origin["task_id"]} if origin is not record else {}),
    }


def validated_candidate(state: RunState, value, record, workspace, current_revision):
    """Recognize a previously validated retained source for fresh review."""
    if record.get("rolled_back") or record.get("changed_files") or not isinstance(value.get("changed_files"), list):
        return None
    declared = set(value["changed_files"])
    if not declared:
        return _revalidated_candidate(state, record, current_revision)
    affected = set((state.get("current_task") or {}).get("affected_paths") or [])
    if (
        not declared
        or not affected
        or not declared <= affected
        or not value.get("commands_run")
        or not value.get("evidence_refs")
        or any(not (Path(workspace) / path).is_file() for path in declared)
        or assignment.outside(
            list(affected),
            state.get("stages", []),
            record,
            workspace=workspace,
            archived=acceptance_tests.repaired_protected(state),
        )
        != []
    ):
        return None
    revision = current_revision
    criteria = {row["id"] for row in (state.get("goal_contract") or {}).get("body", {}).get("acceptance_criteria", [])}
    if not criteria or (state.get("current_task") or {}).get("source_revision") != revision:
        return None
    for archived in reversed(state.get("validation_archive", [])):
        if archived.get("rolled_back") or archived.get("checked_earlier"):
            continue
        validation = archived.get("validation") or {}
        results = {row.get("id"): row.get("status") for row in validation.get("criterion_results", [])}
        if (
            validation.get("verdict") == "PASS"
            and validation.get("source_revision") == revision
            and criteria <= {cid for cid, status in results.items() if status == "PASS"}
        ):
            return {
                "source_revision": revision,
                "validation_output": validation.get("output"),
                "criteria": sorted(criteria),
                "declared_paths": sorted(declared),
            }
    return None


def _revalidated_candidate(state: RunState, record, revision):
    """A reapproved plan may recheck the same tested commit without manufacturing a source edit."""
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    if (
        not state.get("run_dir")
        or task.get("kind") != "implement"
        or task.get("contract_hash") != contract.get("hash")
        or task.get("source_revision") != revision
        or record.get("source_revision") != revision
        or record.get("task_id") != task.get("id")
    ):
        return None
    for previous in reversed(state.get("task_archive") or []):
        if (
            previous.get("rolled_back")
            or previous.get("kind") != "implement"
            or previous.get("contract_hash") == contract.get("hash")
            or {path.rstrip("/") for path in previous.get("affected_paths") or []}
            != {path.rstrip("/") for path in task.get("affected_paths") or []}
            or set(previous.get("milestone_ids") or [previous.get("milestone_id")])
            != set(task.get("milestone_ids") or [task.get("milestone_id")])
            or not _reapproved_scope(state, previous, task)
        ):
            continue
        for archived in reversed(state.get("validation_archive") or []):
            validation = archived.get("validation") or {}
            if any(row.get(key) for row in (archived, validation) for key in ("rolled_back", "checked_earlier")):
                continue
            pins = retained_receipts.checked_source(validation, state["run_dir"], revision, previous)
            if pins:
                return {
                    "source_revision": revision,
                    "reapproved_from": previous["contract_hash"],
                    "reapproved_to": contract["hash"],
                    "receipt_pins": pins,
                    "fresh_verification_required": True,
                    "checks_to_run": verification_plan.approved_commands(state),
                }
    return None


def _snapshot(ref):
    try:
        value = json.loads(Path(ref).read_text())
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("files"), dict) else None


def unchanged_recheck(state: RunState, record, current_revision):
    """Exact previously accepted source is a candidate for fresh review, never completion credit."""
    try:
        if not contracts.approved(state):
            return None
        task = cast(dict, state["current_task"])
        if (
            task.get("contract_hash") != cast(dict, state["goal_contract"])["hash"]
            or record.get("task_id") != task["id"]
            or record.get("changed_files")
            or task.get("kind") != "implement"
            or record.get("rolled_back")
            or task.get("rolled_back")
        ):
            return None
        receipt = util.read_object(Path(state["workspace"]) / ".autocode" / "task-workspace.json")["accepted_recheck"]
        accepted = receipt["snapshot"]
        approval, verification = receipt["approved_plan"], receipt["verification"]
        if (
            not approval.get("token")
            or not approval.get("run_dir")
            or verification.get("verdict") != "PASS"
            or verification.get("head") != accepted["head"]
            or current_revision != accepted["revision"]
            or receipt.get("revoked")
            or verification.get("rolled_back")
            or verification.get("checked_earlier")
        ):
            return None
        before, after = _snapshot(record.get("before_ref")), _snapshot(record.get("after_ref"))
        if not all(
            snapshot and all(snapshot.get(key) == accepted.get(key) for key in ("head", "files", "revision"))
            for snapshot in (before, after)
        ):
            return None
        return {
            "source_revision": current_revision,
            "retained_paths": [],
            "accepted_recheck": True,
            "prior_approved_plan": approval["token"],
        }
    except (KeyError, OSError, TypeError, ValueError, AttributeError, RuntimeError):
        return None


def own_recovery_source(stages, bound_revision, current) -> bool:
    """Whether every file of ``current`` holds its bound content or what this run's own Builder attempts left.

    One definition for both admissions that compare a bound source with the current one: a
    recovery packet's Builder dispatch (``autocode_resolver_recovery``) and the relaunch after a
    stage-level failure or timeout (``autocode_stale_handoff``). The chain starts at
    ``bound_revision``, the revision the packet or the preserved launch binding names. It does not
    ask which packet admitted an attempt: the runner's own bounded Builder rounds (changed-code,
    planned-check and acceptance-test rework, #2815) carry no packet, and a retry after one of them
    timed out must still see that work as AutoCode's own, not as a person's edit. An attempt joins the chain when its before-snapshot
    is at the bound revision or at the after-snapshot revision of an attempt already in it, so a
    relaunch that was itself set aside (a rejected report keeps its edits and the recovery note)
    still counts as the run's own work. Each file must hold either its bound content, from the
    first such attempt's before-snapshot, or what the latest such attempt left, from its
    after-snapshot; Git HEAD must be the one both snapshots recorded. An edit made while an attempt
    ran is in its after-snapshot and counts as that attempt's own, and a file the runner's own cleanup
    removed holds its bound content again. Anything else is external drift.
    """
    attempts, reached = [], {bound_revision}
    for row in stages:
        if (
            row.get("rolled_back")
            or (row.get("original_stage") or row.get("stage")) != assignment.BUILDER
            or row.get("report_only")
            or row.get("dry_run")
        ):
            continue
        start = _snapshot(row.get("before_ref"))
        if isinstance(start, dict) and start.get("revision") in reached:
            attempts.append(row)
            left = _snapshot(row.get("after_ref"))
            if left is not None and left.get("revision"):
                reached.add(left["revision"])
    if not attempts:
        return False
    start, left = _snapshot(attempts[0].get("before_ref")), _snapshot(attempts[-1].get("after_ref"))
    if (
        start is None
        or left is None
        or start.get("revision") != bound_revision
        or not current.get("head") == start.get("head") == left.get("head")
    ):
        return False
    files, before, after = current.get("files") or {}, start["files"], left["files"]
    return all(
        files.get(name) in (before.get(name), after.get(name)) for name in files.keys() | before.keys() | after.keys()
    )
