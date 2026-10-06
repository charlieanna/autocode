"""Classify Builder work retained across attempts and equivalent repair assignments."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_assignment as assignment
except ImportError:
    import autocode_assignment as assignment


def _earlier_assignment(state, record):
    """An unchanged repair may inherit work from the same approved milestone/scope.

    Resolver handoffs create a new task ID. That must not erase the snapshots
    of a Builder that left source before its report failed. Do not carry work
    across contracts, milestones or changed ownership. This selects a baseline
    for fresh review only, never a previous validation verdict.
    """
    task = state.get("current_task") or {}
    contract = (state.get("goal_contract") or {}).get("hash")
    members = task.get("milestone_ids") or [task.get("milestone_id")]
    owned = {path.rstrip("/") for path in task.get("affected_paths") or []}
    if not contract or task.get("contract_hash") != contract or not all(members) \
            or record.get("task_id") != task.get("id"):
        return None
    for previous in reversed(state.get("task_archive") or []):
        if (previous.get("kind") != "implement" or previous.get("contract_hash") != contract
                or set(previous.get("milestone_ids") or [previous.get("milestone_id")]) != set(members)
                or {path.rstrip("/") for path in previous.get("affected_paths") or []} != owned):
            continue
        origin = dict(record, task_id=previous.get("id"))
        changes = assignment.retained_changes(state.get("stages", []), origin)
        if changes is None:
            return None
        if changes:
            return origin
    return None


def fresh_candidate(state, record, current_revision):
    """Return in-scope retained paths for fresh review, without claiming they pass.

    A no-diff retry may follow an interrupted or rejected Builder that left useful
    source changes. Compare the whole assignment with its earliest saved snapshot;
    an empty files dictionary is a valid baseline. The current tree and saved after
    snapshot must still describe the same source revision.
    """
    task = state.get("current_task") or {}
    owned = task.get("affected_paths") or []
    if (record.get("changed_files") or task.get("kind") != "implement"
            or not owned or not state.get("goal_contract")):
        return None
    stages, workspace = state.get("stages", []), state.get("workspace")
    origin = record
    if assignment.retained_changes(stages, record) == []:
        origin = _earlier_assignment(state, record) or record
    # A compiled program a build left behind is neither out of scope nor part of the
    # candidate: the Validator reviews source (autocode_assignment.build_output).
    paths = [path for path in assignment.retained_changes(stages, origin) or []
             if not assignment.build_output(workspace, path, stages, origin)]
    if not paths or assignment.outside(owned, stages, origin, workspace=workspace) != []:
        return None
    try:
        after = json.loads(Path(record["after_ref"]).read_text())
    except (KeyError, OSError, ValueError):
        return None
    revision = record.get("source_revision")
    if (not isinstance(after, dict) or not revision or after.get("revision") != revision
            or current_revision != revision):
        return None
    return {"source_revision": revision, "retained_paths": paths,
            **({"origin_task_id": origin["task_id"]} if origin is not record else {})}


def validated_candidate(state, value, record, workspace, current_revision):
    """Recognize a previously validated retained source for fresh review."""
    if record.get('changed_files') or not isinstance(value.get('changed_files'), list):
        return None
    declared = set(value['changed_files'])
    affected = set((state.get('current_task') or {}).get('affected_paths') or [])
    if (not declared or not affected or not declared <= affected
            or not value.get('commands_run') or not value.get('evidence_refs')
            or any(not (Path(workspace) / path).is_file() for path in declared)
            or assignment.outside(list(affected), state.get('stages', []), record, workspace=workspace) != []):
        return None
    revision = current_revision
    criteria = {row['id'] for row in (state.get('goal_contract') or {}).get('body', {}).get('acceptance_criteria', [])}
    if not criteria or (state.get('current_task') or {}).get('source_revision') != revision:
        return None
    for archived in reversed(state.get('validation_archive', [])):
        validation = archived.get('validation') or {}
        results = {row.get('id'): row.get('status') for row in validation.get('criterion_results', [])}
        if (validation.get('verdict') == 'PASS' and validation.get('source_revision') == revision
                and criteria <= {cid for cid, status in results.items() if status == 'PASS'}):
            return {'source_revision': revision, 'validation_output': validation.get('output'),
                    'criteria': sorted(criteria), 'declared_paths': sorted(declared)}
    return None


def _snapshot(ref):
    try:
        value = json.loads(Path(ref).read_text())
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("files"), dict) else None


def own_repair_source(stages, packet, bound_revision, current) -> bool:
    """Whether ``current`` differs from an incident packet's bound source only by its own Builders' work.

    A recovery packet binds the source its incident was captured on, and each Builder attempt it
    admits carries the packet in its receipt (``recovery_novelty``). Such an attempt can stop without
    being accepted and leave its edits: the runner rejects one that also wrote outside its assignment,
    removes or restores those files (autocode_assignment.undo_created) and keeps the in-scope work for
    the retry, and a timed-out attempt leaves partial edits. The retry must not read that as somebody
    else's change (live feature-stock-refusals run, 2026-10-06). So every file must hold either its
    bound content, from the first such attempt's before-snapshot (taken at the bound revision), or
    what the latest such attempt left, from its after-snapshot, and Git HEAD must be the one both
    snapshots recorded. Any other content, or a snapshot that cannot be read, is a change AutoCode
    did not make.
    """
    attempts = [row for row in stages if packet and (row.get("recovery_novelty") or {}).get("packet") == packet
                and (row.get("original_stage") or row.get("stage")) == assignment.BUILDER
                and not row.get("report_only") and not row.get("dry_run")]
    if not attempts:
        return False
    start, left = _snapshot(attempts[0].get("before_ref")), _snapshot(attempts[-1].get("after_ref"))
    # A HEAD the attempt moved itself (a commit, reset or branch switch) is not admitted: fail closed.
    if (start is None or left is None or start.get("revision") != bound_revision
            or not current.get("head") == start.get("head") == left.get("head")):
        return False
    files, before, after = current.get("files") or {}, start["files"], left["files"]
    return all(files.get(name) in (before.get(name), after.get(name))
               for name in files.keys() | before.keys() | after.keys())
