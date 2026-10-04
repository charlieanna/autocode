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
