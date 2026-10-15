"""Pure consumption of freshly authenticated serial visual-checkpoint facts.

The runtime constructs VerifiedCheckpoint only after original native receipts,
capture inputs, source lineage and admission decisions have been authenticated.
This module neither authenticates those artifacts nor reads saved run files.
Facts are explicit call arguments, never a state-dict hint or a callback registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contract
    from . import autocode_util as util
    from . import autocode_visual_scope as visual_scope
except ImportError:
    import autocode_contract_identity as contract
    import autocode_util as util
    import autocode_visual_scope as visual_scope


@dataclass(frozen=True)
class VerifiedCheckpoint:
    """An in-memory runtime result, not a serializable acceptance authority.

    The scope is canonical JSON to prevent a caller mutating a nested dict after
    authentication. Historical and eligible owners remain separate: a stale image
    does not erase the approved CID authority needed for a read-only recheck.
    authorized_assignment and authorized_launch are exact task/decision digests
    authenticated by the runner, not retry, budget or provider permissions.
    """

    state_binding: str
    source_revision: str
    runtime_hash: str
    scope_json: str
    historical_milestone_ids: tuple[str, ...]
    eligible_milestone_ids: tuple[str, ...]
    current_scope_accepted: bool
    receipt_sha256: str | None
    authorized_assignment: str | None = None
    authorized_launch: str | None = None
    authorized_acceptance: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class VerifiedInitialAssignment:
    """Exact approved initial assignment; no image or owner acceptance claim."""

    state_binding: str
    source_revision: str
    runtime_hash: str
    authorized_assignment: str


def enabled(state: RunState):
    try:
        saved = state.get("goal_contract", {})
        # Before a first assignment, no contract is a normal planning state.
        # An assigned task without its contract remains a technical hold.
        if saved is None and state.get("current_task") is None:
            saved = {}
        body = cast(dict, saved).get("body", {})
        _require(isinstance(body.get("constraints", []), list), "Invalid visual checkpoint constraint list")
        return visual_scope.policy(body) is not None
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Invalid visual checkpoint policy: " + str(error)) from error


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _require(condition, reason):
    if not condition:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", reason)


def binding(state: RunState, current, *, initial=False):
    """Pure exact identity; the caller separately checks actual source/runtime."""
    try:
        return _binding(state, current, initial=initial)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Invalid visual checkpoint identity: " + str(error)) from error


def _binding(state: RunState, current, *, initial=False):
    _require(contract.approved(state), "Serial visual gates require the actual approved contract")
    _require(
        isinstance(current, dict)
        and isinstance(current.get("head"), str)
        and current["head"]
        and isinstance(current.get("files"), dict)
        and all(
            isinstance(name, str) and name and isinstance(pin, str) and pin for name, pin in current["files"].items()
        )
        and current.get("revision") == util.digest({key: current[key] for key in ("head", "files")}),
        "Invalid full checkpoint source snapshot",
    )
    task = state.get("current_task")
    saved = cast(dict, state["goal_contract"])
    if initial:
        _require(task is None, "Initial assignment cannot replace an existing task")
    else:
        _require(
            isinstance(task, dict) and isinstance(task.get("id"), str) and task["id"],
            "Serial visual gates need the current assigned task",
        )
        assert isinstance(task, dict)  # _require above refused anything else
        _require(
            task.get("contract_hash") == saved["hash"] and task.get("contract_revision") == saved["revision"],
            "Visual checkpoint task belongs to a different contract",
        )
    return util.digest(
        {"contract": saved, "task": task, "settings": state.get("settings", {}), "source_revision": current["revision"]}
    )


def assignment_identity(decision):
    """Bind the exact reviewed decision; do not invent or narrow a handoff here."""
    return util.digest(decision)


def launch_identity(task):
    return util.digest(task)


def check(state: RunState, current, proof):
    """Validate identity/shape only; missing runtime proof fails closed."""
    try:
        return _check(state, current, proof)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Invalid visual checkpoint context: " + str(error)) from error


def _check(state: RunState, current, proof):
    if not enabled(state):
        return None
    _require(
        state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled") is True,
        "Serial visual policy requires milestone checkpoints",
    )
    _require(type(proof) is VerifiedCheckpoint, "Missing freshly authenticated visual checkpoint context")
    _require(
        proof.state_binding == binding(state, current)
        and proof.source_revision == current["revision"]
        and _hash(proof.runtime_hash),
        "Visual checkpoint contract, task, settings, source or runtime identity changed",
    )
    _require(isinstance(proof.scope_json, str), "Missing immutable derived visual scope")
    try:
        scope = json.loads(proof.scope_json)
        _require(
            proof.scope_json == json.dumps(scope, sort_keys=True, separators=(",", ":")),
            "Visual checkpoint scope has a noncanonical identity",
        )
        saved = cast(dict, state["goal_contract"])
        manifest = state["settings"]["design_manifest"]["body"]
        admission = visual_scope.admit(saved["body"], manifest)
        _require(admission is not None, "Missing serial complete-case visual admission")
        _require(
            scope.get("case_owners") == admission["case_owners"],
            "Visual scope ownership differs from the exact approved admission",
        )
        visual_scope.check_bound_scope(
            scope,
            case_ids=admission["case_ids"],
            case_criteria=admission["case_criteria"],
            criterion_ids=[row["id"] for row in saved["body"]["acceptance_criteria"]],
        )
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Invalid authenticated checkpoint scope: " + str(error)) from error
    task = cast(dict, state["current_task"])
    criteria = task.get("acceptance_criteria", task.get("criterion_ids"))
    _require(
        not task.get("milestone_ids")
        and scope["milestone_ids"] == [task.get("milestone_id")]
        and scope["task_criterion_ids"] == criteria
        and scope["affected_paths"] == task.get("affected_paths"),
        "Authenticated visual scope does not match the exact assigned task",
    )
    owners = admission["milestone_ids"]
    for label, values in (("historical", proof.historical_milestone_ids), ("eligible", proof.eligible_milestone_ids)):
        _require(
            type(values) is tuple
            and all(isinstance(value, str) for value in values)
            and len(values) == len(set(values))
            and set(values) <= set(owners)
            and list(values) == [owner for owner in owners if owner in values],
            "Invalid authenticated " + label + " visual owner roster",
        )
    _require(
        set(proof.eligible_milestone_ids) <= set(proof.historical_milestone_ids),
        "Current visual eligibility has no authenticated original acceptance",
    )
    for owner in proof.eligible_milestone_ids:
        _require(
            set(owners[: owners.index(owner)]) <= set(proof.eligible_milestone_ids),
            "Visual scheduling eligibility is not transitively closed",
        )
    position = owners.index(task["milestone_id"])
    _require(
        set(proof.historical_milestone_ids) <= set(owners[: position + 1])
        and scope["accepted_milestone_ids"] == owners[:position]
        and set(scope["accepted_milestone_ids"]) <= set(proof.historical_milestone_ids),
        "Visual checkpoint borrowed future or unauthenticated prior owner acceptance",
    )
    _require(
        type(proof.current_scope_accepted) is bool
        and (proof.receipt_sha256 is None or _hash(proof.receipt_sha256))
        and (not proof.current_scope_accepted or _hash(proof.receipt_sha256)),
        "Current visual acceptance needs its authenticated original receipt identity",
    )
    for value in (proof.authorized_assignment, proof.authorized_launch, proof.authorized_acceptance):
        _require(value is None or _hash(value), "Invalid authenticated visual operation identity")
    _require(proof.reason is None or isinstance(proof.reason, str), "Invalid visual checkpoint reason")
    return scope


def current_ready(state: RunState, current, proof):
    if not enabled(state):
        return True
    scope = check(state, current, proof)
    # Readiness precedes actual Completion acceptance. The current owner must
    # not already be a committed historical owner to accept its first review.
    # Scheduling IDs remain exclusively committed, authenticated history.
    return proof.current_scope_accepted and set(scope["accepted_milestone_ids"]) <= set(proof.eligible_milestone_ids)


def require_case_acceptance(state: RunState, current, proof):
    if not enabled(state):
        return
    check(state, current, proof)
    _require(
        proof.current_scope_accepted,
        proof.reason or "Current milestone needs independent passing complete-case image evidence",
    )


def require_current_ready(state: RunState, current, proof):
    if not enabled(state):
        return
    require_case_acceptance(state, current, proof)
    _require(
        current_ready(state, current, proof),
        proof.reason or "Fresh complete-case prerequisites are required before checkpoint acceptance",
    )


def require_acceptance(state: RunState, current, proof):
    if not enabled(state):
        return
    require_current_ready(state, current, proof)
    _require(
        proof.authorized_acceptance == binding(state, current),
        "Checkpoint acceptance lacks the exact prepared native Completion transaction",
    )


def eligible_ids(state: RunState, current, proof):
    if not enabled(state):
        return None
    check(state, current, proof)
    return set(proof.eligible_milestone_ids)


def recheckable(state: RunState, current, proof):
    if not enabled(state):
        return None
    check(state, current, proof)
    rows = {row["id"]: row for row in cast(dict, state["goal_contract"])["body"]["milestones"]}
    return {cid for mid in proof.historical_milestone_ids for cid in rows[mid]["acceptance_criteria"]}


def require_assignment(state: RunState, current, proof, decision):
    if not enabled(state):
        return
    if state.get("current_task") is None:
        return require_initial_assignment(state, current, proof, decision)
    check(state, current, proof)
    _require(
        proof.authorized_assignment == assignment_identity(decision),
        "Assignment lacks fresh native prerequisite or causal bounded-rework authority",
    )


def exact_repair_paths(state: RunState, current, proof, decision):
    """A typed original handoff preserves its exact narrow repair grants.

    Ordinary untyped decisions retain ordinary milestone normalization. This
    pure check validates the exact fresh handoff before changing that policy.
    """
    if (
        not enabled(state)
        or type(proof) is not VerifiedCheckpoint
        or proof.authorized_assignment is None
        or decision.get("status") != "REWORK"
    ):
        return None
    require_assignment(state, current, proof, decision)
    _require(
        decision["next_task"]["kind"] == "implement" and bool(decision.get("affected_paths")),
        "Typed repair needs its concrete nonempty writer grants",
    )
    return list(decision["affected_paths"])


def require_initial_assignment(state: RunState, current, proof, decision):
    """The first task needs actual approval, not a prior image/Completion commit."""
    if not enabled(state):
        return
    try:
        _require(
            type(proof) is VerifiedInitialAssignment
            and proof.state_binding == binding(state, current, initial=True)
            and proof.source_revision == current["revision"]
            and _hash(proof.runtime_hash)
            and _hash(proof.authorized_assignment)
            and proof.authorized_assignment == assignment_identity(decision),
            "Initial assignment lacks fresh exact approved runtime authority",
        )
        _require(
            state["settings"]["milestone_checkpoints"]["enabled"] is True,
            "Serial visual policy requires milestone checkpoints",
        )
        body = cast(dict, state["goal_contract"])["body"]
        admission = visual_scope.admit(body, state["settings"]["design_manifest"]["body"])
        first = body["initial_task"]
        paths = (
            list(body["milestones"][0]["affected_paths"])
            if first["affected_paths"] or first["kind"] == "validate"
            else []
        )
        expected = {
            "status": "CONTINUE",
            "next_objective": first["objective"],
            "affected_paths": paths,
            "next_task": {key: value for key, value in first.items() if key not in ("objective", "affected_paths")},
        }
        _require(
            admission is not None
            and first["milestone_id"] == admission["milestone_ids"][0]
            and first["kind"] in ("implement", "validate")
            and decision == expected,
            "Initial assignment differs from the exact approved first task",
        )
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Invalid initial visual assignment: " + str(error)) from error


def require_launch(state: RunState, current, proof):
    if not enabled(state):
        return
    check(state, current, proof)
    _require(
        proof.authorized_launch == launch_identity(state["current_task"]),
        "Writer lacks fresh prerequisite or original causal admission authority",
    )
