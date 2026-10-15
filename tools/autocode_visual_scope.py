"""Pure complete-case visual scope rules over approved facts supplied by callers.

No files, private run state, processes or visual verdicts. Historical acceptance
IDs and capture input paths must already be authenticated by the runner. A valid
scope does not authenticate those inputs or grant image acceptance authority.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

try:
    from . import autocode_design_inventory as inventory
    from . import autocode_progressive_plan as progressive
    from . import autocode_util as util
except ImportError:
    import autocode_design_inventory as inventory
    import autocode_progressive_plan as progressive
    import autocode_util as util


MARKER = "VISUAL_SLICE_GATES="
POLICY = "complete-cases-v1"
CASE_MARKER = "VISUAL_CASE_CRITERIA="
_SCOPE_KEYS = frozenset(
    (
        "policy",
        "mode",
        "milestone_ids",
        "required_case_ids",
        "current_case_ids",
        "recheck_case_ids",
        "out_of_scope_case_ids",
        "case_criteria",
        "case_owners",
        "task_criterion_ids",
        "affected_paths",
        "changed_paths",
        "accepted_milestone_ids",
        "case_input_paths",
    )
)


def policy(body):
    rows = [
        line[len(MARKER) :] for line in body.get("constraints", []) if isinstance(line, str) and line.startswith(MARKER)
    ]
    if not rows:
        return None
    if rows != [POLICY]:
        raise ValueError("Visual slice gates need one exact supported policy declaration")
    return POLICY


def _ids(values, label, *, empty=False):
    if (
        not isinstance(values, list)
        or (not values and not empty)
        or any(not isinstance(value, str) or not value.strip() for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError(label + " needs unique declared IDs")
    return list(values)


def _paths(values, label, *, empty=False):
    paths = _ids(values, label, empty=empty)
    for value in paths:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or "\x00" in value
            or value == "."
            or str(path) != value.rstrip("/")
        ):
            raise ValueError(label + " needs portable relative paths")
    return paths


def _covers(path, owned):
    if not inventory.path_intersects(path, owned):
        return False
    # Existing glob semantics come from inventory; literal scopes must contain
    # the full case/input path rather than merely intersect a broader directory.
    return (
        any(char in owned for char in "*?[")
        or path.rstrip("/") == owned.rstrip("/")
        or path.rstrip("/").startswith(owned.rstrip("/") + "/")
    )


def _declaration(body, case_ids):
    rows = [
        line[len(CASE_MARKER) :]
        for line in body.get("constraints", [])
        if isinstance(line, str) and line.startswith(CASE_MARKER)
    ]
    if len(rows) != 1:
        raise ValueError("Visual slice gates need one complete visual case declaration")

    def unique(pairs):
        result = {}
        for identity, mapped in pairs:
            if identity in result:
                raise ValueError("Visual case declaration repeats a case: " + identity)
            result[identity] = mapped
        return result

    declared = json.loads(rows[0], object_pairs_hook=unique)
    if not isinstance(declared, dict) or set(declared) != set(case_ids):
        raise ValueError("Visual slice declaration must retain every case exactly once")
    return {identity: _ids(declared[identity], "Visual case " + identity) for identity in case_ids}


def admit(body, manifest_body):
    """Return complete ownership facts, or None for an unchanged opt-out plan."""
    if policy(body) is None:
        return None
    if not isinstance(manifest_body, dict) or manifest_body.get("version") not in (1, 2):
        raise ValueError("Visual slice gates require a complete version 1 or 2 design inventory")
    prefixes = (progressive.DISCLOSURE_DELEGATION, progressive.DISCLOSURE_SLICE, progressive.DISCLOSURE_OUTSTANDING)
    if any(
        isinstance(line, str) and line.startswith(prefixes)
        for field in ("constraints", "technical_approach")
        for line in body.get(field) or []
    ):
        raise ValueError("Complete-case visual slice gates do not admit progressive disclosure")
    cases = manifest_body.get("cases") or []
    case_ids = _ids([case["id"] for case in cases], "Visual inventory")
    criteria = _ids([row["id"] for row in body.get("acceptance_criteria", [])], "Product criteria")
    milestones = body.get("milestones") or []
    milestone_ids = _ids([row["id"] for row in milestones], "Visual slice milestones")
    rows = {row["id"]: row for row in milestones}
    if {cid for row in milestones for cid in row.get("acceptance_criteria", [])} != set(criteria):
        raise ValueError("Visual slice milestones must retain every approved product criterion")
    for index, milestone in enumerate(milestones):
        owned = _ids(milestone.get("acceptance_criteria"), "Milestone criteria")
        if not set(owned) <= set(criteria):
            raise ValueError("Visual slice milestone names unknown product criteria")
        _paths(milestone.get("affected_paths"), "Milestone ownership")
        dependencies = _ids(milestone.get("depends_on"), "Milestone dependencies", empty=True)
        if not set(dependencies) <= set(milestone_ids[:index]) or (
            index and milestone_ids[index - 1] not in dependencies
        ):
            raise ValueError("Visual slice gates require an explicit serial milestone dependency chain")
    initial = body.get("initial_task")
    if initial and initial.get("milestone_id") != milestone_ids[0]:
        raise ValueError("Visual slice initial task must start the serial dependency chain")
    mapping = _declaration(body, case_ids)
    coverage = body.get("design_coverage")
    if manifest_body["version"] == 2:
        if not isinstance(coverage, dict) or coverage.get("manifest_hash") != util.digest(manifest_body):
            raise ValueError("Visual slice ownership needs the full current structured design coverage")
        covered = coverage.get("cases") or []
        identities = _ids([row["id"] for row in covered], "Structured visual coverage")
        if set(identities) != set(case_ids):
            raise ValueError("Structured visual ownership must retain every case")
        coverage_rows = {row["id"]: row for row in covered}
    else:
        coverage_rows = None
    owners, paths = {}, {}
    criterion_owners = {
        cid: {mid for mid, row in rows.items() if cid in row["acceptance_criteria"]} for cid in criteria
    }
    for case in cases:
        identity, mapped = case["id"], mapping[case["id"]]
        if not set(mapped) <= set(criteria):
            raise ValueError("Visual case names unknown product criteria: " + identity)
        if any(len(criterion_owners[cid]) != 1 for cid in mapped):
            raise ValueError("Visual criterion must have one milestone owner: " + identity)
        paths[identity] = _paths(case.get("implementation_paths"), "Case implementation paths")
        candidates = [
            mid
            for mid, row in rows.items()
            if set(mapped) <= set(row["acceptance_criteria"])
            and all(any(_covers(path, owned) for owned in row["affected_paths"]) for path in paths[identity])
        ]
        if coverage_rows is not None:
            row = coverage_rows[identity]
            declared = _ids(row.get("milestone_ids"), "Structured visual owners")
            declared_criteria = _ids(row.get("criterion_ids"), "Structured visual criteria")
            if len(declared) != 1 or declared_criteria != mapped or declared != candidates:
                raise ValueError("Complete visual case needs one exact owner and criterion declaration: " + identity)
        if len(candidates) != 1:
            raise ValueError("Complete visual case has missing or ambiguous milestone ownership: " + identity)
        owners[identity] = candidates[0]
    if set(owners.values()) != set(milestone_ids):
        raise ValueError("Every visual-slice milestone needs a complete owned case")
    return {
        "policy": POLICY,
        "case_ids": case_ids,
        "milestone_ids": milestone_ids,
        "case_criteria": mapping,
        "case_owners": owners,
        "implementation_paths": paths,
    }


def check_bound_scope(scope, *, case_ids, case_criteria, criterion_ids):
    """Check binding structure/identity, not owner, history or image authority."""
    if not isinstance(scope, dict) or set(scope) != _SCOPE_KEYS or scope["policy"] != POLICY:
        raise ValueError("Visual scope has an unsupported shape or policy")
    case_ids = _ids(list(case_ids), "Bound cases")
    criterion_ids = _ids(list(criterion_ids), "Bound product criteria")
    if (
        not isinstance(case_criteria, dict)
        or scope["case_criteria"] != case_criteria
        or set(case_criteria) != set(case_ids)
    ):
        raise ValueError("Visual scope changed the full approved case mapping")
    for identity in case_ids:
        if not set(_ids(case_criteria[identity], "Bound case criteria")) <= set(criterion_ids):
            raise ValueError("Visual scope contains unknown product criteria")
    owners = scope["case_owners"]
    if (
        not isinstance(owners, dict)
        or set(owners) != set(case_ids)
        or any(not isinstance(owner, str) or not owner.strip() for owner in owners.values())
    ):
        raise ValueError("Visual scope needs a full owner roster")
    milestones = _ids(scope["milestone_ids"], "Current scope owner")
    if len(milestones) != 1:
        raise ValueError("Visual scope admits only one serial owner")
    if milestones[0] not in set(owners.values()):
        raise ValueError("Visual scope current owner must own a complete case")
    accepted = _ids(scope["accepted_milestone_ids"], "Prior scope owners", empty=True)
    if milestones[0] in accepted or not set(accepted) <= set(owners.values()):
        raise ValueError("Visual scope has invalid prior owners")
    partitions = {}
    for key in ("required_case_ids", "current_case_ids", "recheck_case_ids", "out_of_scope_case_ids"):
        values = _ids(scope[key], key, empty=key not in ("required_case_ids", "current_case_ids"))
        if not set(values) <= set(case_ids) or values != [cid for cid in case_ids if cid in values]:
            raise ValueError("Visual scope case partitions must retain full inventory order")
        partitions[key] = set(values)
    required, current, recheck, outside = (
        partitions[key]
        for key in ("required_case_ids", "current_case_ids", "recheck_case_ids", "out_of_scope_case_ids")
    )
    if (
        current & recheck
        or required != current | recheck
        or required & outside
        or required | outside != set(case_ids)
        or current != {cid for cid in case_ids if owners[cid] == milestones[0]}
        or any(owners[cid] not in accepted for cid in recheck)
    ):
        raise ValueError("Visual scope case partitions or owner claims disagree")
    mapped_owners = {}
    for identity in case_ids:
        for cid in case_criteria[identity]:
            if mapped_owners.setdefault(cid, owners[identity]) != owners[identity]:
                raise ValueError("Visual scope shares a criterion across owner milestones")
    task_criteria = _ids(scope["task_criterion_ids"], "Task criteria")
    if not set(task_criteria) <= set(criterion_ids) or any(
        not set(case_criteria[cid]) <= set(task_criteria) for cid in required
    ):
        raise ValueError("Required visual cases need their explicit approved task criteria")
    if scope["mode"] not in ("slice", "integration") or (
        scope["mode"] == "integration"
        and (outside or required != set(case_ids) or set(task_criteria) != set(criterion_ids))
    ):
        raise ValueError("Visual integration scope requires every case and product criterion")
    affected = _paths(scope["affected_paths"], "Task affected paths")
    changed = _paths(scope["changed_paths"], "Measured changed paths", empty=True)
    if any(not any(_covers(path, owned) for owned in affected) for path in changed):
        raise ValueError("Visual scope has an unaccounted source change")
    inputs = scope["case_input_paths"]
    expected_inputs = {cid for cid in case_ids if owners[cid] in accepted}
    if not isinstance(inputs, dict) or set(inputs) != expected_inputs:
        raise ValueError("Visual scope needs authenticated prior case input footprints")
    for identity, paths in inputs.items():
        _paths(paths, "Prior case inputs " + identity)


def derive(body, manifest_body, task, *, accepted_milestones=(), accepted_case_input_paths=None, changed_paths=()):
    """Derive required complete cases; caller authenticates all history inputs.

    An implement task reviews only its own complete cases and cannot claim
    prior CIDs. Touched prior cases remain outside/NOT_VERIFIED; the runtime
    holds later scheduling until an explicit validate task rechecks them.
    A validate task must retain the explicit existing touched-case criteria.
    Final integration requires one all-case review, never a historical union.
    """
    admission = admit(body, manifest_body)
    if admission is None:
        return None
    if task.get("milestone_ids") or task.get("kind") not in ("implement", "validate"):
        raise ValueError("Visual slice gates require a serial implement or validate task")
    mid = task.get("milestone_id")
    rows = {row["id"]: row for row in body["milestones"]}
    if mid not in rows:
        raise ValueError("Visual task names an unknown milestone")
    if not isinstance(accepted_milestones, (list, tuple, set, frozenset)):
        raise ValueError("Authenticated accepted milestones need explicit identities")
    accepted_values = list(accepted_milestones)
    _ids(accepted_values, "Authenticated accepted milestones", empty=True)
    if not set(accepted_values) <= set(rows):
        raise ValueError("Authenticated history names an unknown milestone")
    position = admission["milestone_ids"].index(mid)
    prior = [identity for identity in admission["milestone_ids"][:position] if identity in accepted_values]
    if any(identity not in admission["milestone_ids"][: position + 1] for identity in accepted_values):
        raise ValueError("Visual scope cannot borrow future milestone acceptance")
    if set(prior) != set(admission["milestone_ids"][:position]):
        raise ValueError("Visual scope needs authenticated earlier owner acceptance")
    task_criteria = _ids(task.get("acceptance_criteria"), "Visual task criteria")
    current_criteria = set(rows[mid]["acceptance_criteria"])
    allowed = current_criteria | (
        {cid for identity in prior for cid in rows[identity]["acceptance_criteria"]}
        if task["kind"] == "validate"
        else set()
    )
    if not set(task_criteria) <= allowed or not current_criteria <= set(task_criteria):
        raise ValueError("Visual task must retain its full approved criteria and explicit validate rechecks")
    affected = _paths(task.get("affected_paths"), "Visual task affected paths")
    if any(not any(_covers(path, owned) for owned in rows[mid]["affected_paths"]) for path in affected):
        raise ValueError("Visual task affected paths exceed its approved owner")
    if not isinstance(changed_paths, (list, tuple, set, frozenset)):
        raise ValueError("Measured source changes need explicit paths")
    changed = sorted(_paths(list(changed_paths), "Measured source changes", empty=True))
    ids, owners, mapping = (admission[key] for key in ("case_ids", "case_owners", "case_criteria"))
    prior_cases = [identity for identity in ids if owners[identity] in prior]
    inputs = {} if accepted_case_input_paths is None else accepted_case_input_paths
    if not isinstance(inputs, dict) or set(inputs) != set(prior_cases):
        raise ValueError("Visual scope needs authenticated prior case input footprints")
    inputs = {identity: _paths(inputs[identity], "Prior case inputs " + identity) for identity in prior_cases}
    all_criteria = [row["id"] for row in body["acceptance_criteria"]]
    integration = task["kind"] == "validate" and set(task_criteria) == set(all_criteria)
    if integration and set(prior) | {mid} != set(admission["milestone_ids"]):
        raise ValueError("Integration scope cannot replace acceptance of future milestone work")
    current = [identity for identity in ids if owners[identity] == mid]
    recheck = [
        identity
        for identity in prior_cases
        if integration
        or set(mapping[identity]) <= set(task_criteria)
        or any(
            inventory.path_intersects(path, changed_path)
            for path in (*admission["implementation_paths"][identity], *inputs[identity])
            for changed_path in (*affected, *changed)
        )
    ]
    if task["kind"] == "implement":
        # Ownership and CID boundaries stay fixed. Passing this current-only
        # review cannot restore stale prior images or release a later owner.
        # The runtime must authenticate transitive eligibility separately.
        recheck = []
    required = [identity for identity in ids if identity in current or identity in recheck]
    scope = {
        "policy": POLICY,
        "mode": "integration" if integration else "slice",
        "milestone_ids": [mid],
        "required_case_ids": required,
        "current_case_ids": current,
        "recheck_case_ids": recheck,
        "out_of_scope_case_ids": [identity for identity in ids if identity not in required],
        "case_criteria": {identity: list(mapping[identity]) for identity in ids},
        "case_owners": dict(owners),
        "task_criterion_ids": task_criteria,
        "affected_paths": affected,
        "changed_paths": changed,
        "accepted_milestone_ids": prior,
        "case_input_paths": inputs,
    }
    check_bound_scope(scope, case_ids=ids, case_criteria=mapping, criterion_ids=all_criteria)
    return scope


def eligible_milestones(body, manifest_body, *, authenticated_milestone_ids, current_case_ids):
    """Pure scheduling closure over independently authenticated caller facts.

    The runtime must verify each original functional/native owner acceptance
    and each case's current unchanged capture-input footprint or fresh native
    recheck. Saved accepted flags, IDs, checksums and historical unions are not
    authentication. This helper grants neither writer nor final completion.
    """
    admission = admit(body, manifest_body)
    if admission is None:
        return None
    history = _ids(list(authenticated_milestone_ids), "Authenticated milestone history", empty=True)
    current = _ids(list(current_case_ids), "Authenticated current cases", empty=True)
    owners, cases = admission["milestone_ids"], admission["case_ids"]
    if not set(history) <= set(owners) or not set(current) <= set(cases):
        raise ValueError("Visual scheduling has unknown authenticated owners or cases")
    rows = {row["id"]: row for row in body["milestones"]}
    eligible = []
    for owner in owners:
        required = {identity for identity in cases if admission["case_owners"][identity] == owner}
        if owner in history and required <= set(current) and set(rows[owner]["depends_on"]) <= set(eligible):
            eligible.append(owner)
    return eligible
