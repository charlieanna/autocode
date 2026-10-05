"""Re-attribute open findings when an approved revision moves the criteria they cite (#447).

A finding's scope is the milestone whose review raised it and the criteria that review covered,
for example ``{"milestone_id": "M2", "criteria": ["AC10", "AC15"]}``. An approved revision can
move a criterion to another milestone (AC15 to M5). The saved scope then no longer fits its
owner's approved criteria, so ``autocode_finding_scope.relevant_blockers`` fails closed on it and
the finding blocks every milestone, while ``autocode_findings._covers`` needs one report covering
criteria that now belong to different milestones, which no milestone's reviewer gives. Only
closing the finding, which drops the defect, got a run past that.

When the user approves a revision, the approval step (``autocode_goal_lifecycle._approve``, inside
its transaction) calls ``on_approval``: each open finding whose saved scope fitted the previously
approved contract and no longer fits the new one is attributed to the milestones that now own its
criteria. Nothing is closed, resolved, downgraded or dropped; status, severity, blocking, text,
evidence and the rest of the defect are kept.

A scope names one milestone; ``relevant_blockers`` and ``_covers`` accept nothing else. A finding
whose criteria now belong to two or more milestones is split: the row keeps its id for the part
its milestone still owns (or, when it owns none, for the first new owner in contract order), and
each other owner gets a copy with a new id and ``split_from`` naming the original finding. Each
part blocks the milestones its criteria belong to and the milestones depending on them, and closes
when a review covering its criteria resolves it, so every criterion the finding was raised against
keeps a reviewer. One row would either let one milestone's reviewer close the defect for criteria
it never reviewed, or let a milestone be accepted while the defect may concern its criterion.
Split parts are one defect over different criteria, not duplicates: resolving one part does not
close another (``autocode_finding_cause.split_family``).

A moved criterion can be listed by several milestones of the approved contract. It goes to one
that did not list it before (where the revision moved it), else to one not accepted under the
previous contract, else to the first in contract order; contract order breaks ties. A milestone
accepted before that receives a part is not carried forward as accepted
(``autocode_carryforward.carry`` revalidates a milestone with an open blocking finding recorded
against it), so its reviewer checks that criterion again and can close the part.

Left unchanged, so they still fail closed for a person (``--close-finding``, a permission answer):
a finding citing a criterion that no milestone of the approved contract lists (removed, or left
unassigned) or whose wording the revision changed (a different criterion under the same id), even
when its other criteria still exist; and a scope that did not fit the previously approved contract
(unscoped, a batch, or already stale before this revision): this revision did not move it. A
blocking one of these still blocks every milestone, as before #447. A moved finding fits the new
contract, so running this again (restart, resume) or a later approval that does not move its
criteria again changes nothing.

Each move is recorded once, on the row it started from: ``scope_history`` gains
``{"from", "to", "contract_token", "at"}``, ``to`` listing every resulting row's ``id``,
``milestone_id`` and ``criteria``. Read by ``autocode_run_view.evidence`` (``finding_scope_moves``)
and by people reading the ledger. Imports only cycle-free modules: the contract identity, the
workflow approval rule and the finding cause identity.
"""
from __future__ import annotations

import copy

try:
    from . import autocode_contract_identity as identity, autocode_workflows as workflows
    from . import autocode_finding_cause as cause
except ImportError:
    import autocode_contract_identity as identity
    import autocode_workflows as workflows
    import autocode_finding_cause as cause

# Fields a split copy does not take from the row: its identity, its scope and the row's own move
# history, and bookkeeping about the row rather than the defect (a resolution attempt over the
# row's whole scope, the tasks assigned to fix the row, how the row's scope was restored).
_NOT_COPIED = ("id", "scope", "scope_history", "split_from", "pending_resolution", "assigned_task",
               "assigned_history", "scope_restored_from")


def previous_approved(state):
    """The latest contract the user approved before the approval being made, or None.

    Call it before the new approval event is saved: the open findings were recorded under this
    contract. A contract counts only with a saved goal_approval event for its exact token."""
    events = [event for event in state.get("user_events") or []
              if isinstance(event, dict) and event.get("kind") == "goal_approval"]
    contracts = [*(state.get("contract_history") or []), state.get("goal_contract")]
    for contract in reversed(contracts):
        if not isinstance(contract, dict) or "revision" not in contract or "hash" not in contract:
            continue
        token = identity.token(contract)
        if any(event.get("token") == token and workflows.approval_actor_ok(contract.get("origin"), event)
               for event in events):
            return contract
    return None


def _owned(body):
    """Milestone id -> its criteria, in contract order, skipping malformed rows."""
    milestones = body.get("milestones") if isinstance(body, dict) else None
    owned = {}
    for row in milestones if isinstance(milestones, list) else []:
        criteria = row.get("acceptance_criteria") if isinstance(row, dict) else None
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(criteria, list):
            owned[row["id"]] = set(criteria)
    return owned


def _wording(body):
    """Criterion id -> its wording (the behavior it requires)."""
    rows = body.get("acceptance_criteria") if isinstance(body, dict) else None
    return {row["id"]: row.get("criterion") for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)}


def plan(rows, old_body, new_body, accepted=()) -> list[dict]:
    """The moves an approved revision makes, as ``{"id", "from", "to"}``; pure.

    ``old_body`` is the previously approved contract body, ``new_body`` the one being approved and
    ``accepted`` the milestones accepted under the previous contract. ``to`` holds one scope per
    milestone that now owns some of the row's criteria; the first is the one the row keeps. A row is
    moved only when its scope fitted ``old_body``, no longer fits ``new_body``, and every criterion
    it cites that left its milestone still has the same wording and an owner there."""
    old, new = _owned(old_body), _owned(new_body)
    old_wording, new_wording = _wording(old_body), _wording(new_body)
    order = {mid: index for index, mid in enumerate(new)}
    moves = []
    for row in rows or []:
        saved = row.get("scope") if isinstance(row, dict) else None
        if not isinstance(saved, dict) or row.get("status") != "open":
            continue
        owner, criteria = saved.get("milestone_id"), saved.get("criteria")
        if (not isinstance(row.get("id"), str) or not isinstance(owner, str)
                or not isinstance(criteria, list) or not criteria
                or any(not isinstance(cid, str) or not cid for cid in criteria)):
            continue
        cited = set(criteria)
        if owner not in old or not cited <= old[owner]:
            continue  # Not a scope this revision moved: unscoped, a batch, or already stale.
        if owner in new and cited <= new[owner]:
            continue
        parts = {}
        for cid in sorted(cited):
            if owner in new and cid in new[owner]:
                parts.setdefault(owner, []).append(cid)
                continue
            holders = [mid for mid in new if cid in new[mid]]
            if not holders or cid not in old_wording or old_wording[cid] != new_wording.get(cid):
                parts = None  # Removed, unassigned or redefined: nowhere to move it; a person decides.
                break
            # Where the revision moved it, else a milestone that still has to be reviewed.
            holder = min(holders, key=lambda mid: (cid in old.get(mid, ()), mid in accepted, order[mid]))
            parts.setdefault(holder, []).append(cid)
        if not parts:
            continue
        owners = ([owner] if owner in parts else []) + sorted((mid for mid in parts if mid != owner), key=order.get)
        moves.append({"id": row.get("id"), "from": copy.deepcopy(saved),
                      "to": [{"milestone_id": mid, "criteria": parts[mid]} for mid in owners]})
    return moves


def apply(rows, moves, *, contract_token, at, new_id) -> list[dict]:
    """Make the planned moves on the ledger rows in place and record each once; returns the records.

    A row whose scope is no longer the one planned from is left alone, so a move applies once."""
    by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
    records = []
    for move in moves:
        row = by_id.get(move["id"])
        if row is None or row.get("scope") != move["from"]:
            continue
        kept, *others = move["to"]
        results = [{"id": row["id"], **copy.deepcopy(kept)}]
        for scope in others:
            part = copy.deepcopy({key: value for key, value in row.items() if key not in _NOT_COPIED})
            part.update(id=new_id(), scope=copy.deepcopy(scope), split_from=cause.split_family(row),
                        assigned_task=None)
            rows.append(part)
            results.append({"id": part["id"], **copy.deepcopy(scope)})
        row["scope"] = copy.deepcopy(kept)
        pending = row.get("pending_resolution")
        if isinstance(pending, dict) and isinstance(pending.get("unverified_criteria"), list):
            # An earlier resolution attempt on this row; only the criteria the row still holds remain its own.
            pending["unverified_criteria"] = [cid for cid in pending["unverified_criteria"] if cid in kept["criteria"]]
        record = {"from": copy.deepcopy(move["from"]), "to": results, "contract_token": contract_token, "at": at}
        row.setdefault("scope_history", []).append(record)
        records.append({"finding": row["id"], **copy.deepcopy(record)})
    return records


def _accepted_under(state, contract_hash):
    """Milestones accepted under the contract with this hash (``milestone_progress``)."""
    progress = state.get("milestone_progress")
    return {mid for row in (progress.values() if isinstance(progress, dict) else [])
            if isinstance(row, dict) and row.get("accepted") and row.get("contract_hash") == contract_hash
            for mid in row.get("milestone_ids") or [row.get("id")]}


def on_approval(state, previous, *, contract_token, at, new_id) -> list[dict]:
    """Re-attribute the open findings the approved ``state["goal_contract"]`` moved.

    ``previous`` is ``previous_approved(state)`` taken before this approval was saved."""
    rows = state.get("findings_ledger")
    if not previous or not rows:
        return []
    moves = plan(rows, previous.get("body"), (state.get("goal_contract") or {}).get("body"),
                 _accepted_under(state, previous.get("hash")))
    return apply(rows, moves, contract_token=contract_token, at=at, new_id=new_id)


def history(rows) -> list[dict]:
    """Every recorded move, oldest first per finding: ``{"finding", "from", "to", "contract_token", "at"}``."""
    return [{"finding": row.get("id"), **{key: copy.deepcopy(entry.get(key))
                                         for key in ("from", "to", "contract_token", "at")}}
            for row in rows or [] if isinstance(row, dict)
            for entry in row.get("scope_history") or [] if isinstance(entry, dict)]
