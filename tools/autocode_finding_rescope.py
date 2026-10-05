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
evidence and the rest of the row are kept.

A scope names one milestone; ``relevant_blockers`` and ``_covers`` accept nothing else. A finding
whose criteria now belong to two or more milestones is split: the row keeps its id for the part
its milestone still owns (or, when it owns none, for the first owner in contract order), and each
other owner gets a copy with a new id and ``split_from`` naming the row. Each part blocks its own
milestone and the milestones depending on it, and only that milestone's reviewer can close it, so
every criterion the finding was raised against keeps a reviewer. One row would either let one
milestone's reviewer close the defect for criteria it never reviewed, or let a milestone be
accepted while the defect may concern its criterion. A criterion several milestones list goes to
the first of them in contract order; ``relevant_blockers`` holds the finding at the others too.

Left unchanged, so they still fail closed for a person (``--close-finding``, a permission answer):
a finding citing a criterion that no milestone of the approved contract lists (removed, or left
unassigned), and a scope that did not fit the previously approved contract (unscoped, a batch, or
already stale before this revision): this revision did not move it. A moved finding fits the new
contract, so running this again (restart, resume) or a later approval that does not move its
criteria again changes nothing.

Each move is recorded once, on the row it started from: ``scope_history`` gains
``{"from", "to", "contract_token", "at"}``, ``to`` listing every resulting row's ``id``,
``milestone_id`` and ``criteria``. Read by ``autocode_run_view.evidence`` (``finding_scope_moves``)
and by people reading the ledger. Imports only the cycle-free contract identity and the workflow
approval rule it uses.
"""
from __future__ import annotations

import copy

try:
    from . import autocode_contract_identity as identity, autocode_workflows as workflows
except ImportError:
    import autocode_contract_identity as identity
    import autocode_workflows as workflows

# Fields a split copy does not inherit: its identity, its scope and the row's own move history.
_OWN = ("id", "scope", "scope_history", "split_from")


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


def _owned(milestones):
    """Milestone id -> its criteria, in contract order, skipping malformed rows."""
    owned = {}
    for row in milestones if isinstance(milestones, list) else []:
        criteria = row.get("acceptance_criteria") if isinstance(row, dict) else None
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(criteria, list):
            owned[row["id"]] = set(criteria)
    return owned


def plan(rows, old_milestones, new_milestones) -> list[dict]:
    """The moves an approved revision makes, as ``{"id", "from", "to"}``; pure.

    ``to`` holds one scope per milestone that now owns some of the row's criteria; the first is
    the one the row keeps. A row is moved only when its scope fitted ``old_milestones``, no longer
    fits ``new_milestones``, and every criterion it cites has an owner there."""
    old, new = _owned(old_milestones), _owned(new_milestones)
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
            if not holders:
                parts = None  # Removed or unassigned: no milestone to give it to; a person decides.
                break
            parts.setdefault(holders[0], []).append(cid)
        if not parts:
            continue
        order = ([owner] if owner in parts else []) + [mid for mid in new if mid in parts and mid != owner]
        moves.append({"id": row.get("id"), "from": copy.deepcopy(saved),
                      "to": [{"milestone_id": mid, "criteria": parts[mid]} for mid in order]})
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
            part = copy.deepcopy({key: value for key, value in row.items() if key not in _OWN})
            part.update(id=new_id(), scope=copy.deepcopy(scope), split_from=row["id"])
            rows.append(part)
            results.append({"id": part["id"], **copy.deepcopy(scope)})
        row["scope"] = copy.deepcopy(kept)
        record = {"from": copy.deepcopy(move["from"]), "to": results, "contract_token": contract_token, "at": at}
        row.setdefault("scope_history", []).append(record)
        records.append({"finding": row["id"], **copy.deepcopy(record)})
    return records


def on_approval(state, previous, *, contract_token, at, new_id) -> list[dict]:
    """Re-attribute the open findings the approved ``state["goal_contract"]`` moved.

    ``previous`` is ``previous_approved(state)`` taken before this approval was saved."""
    rows = state.get("findings_ledger")
    if not previous or not rows:
        return []
    moves = plan(rows, (previous.get("body") or {}).get("milestones"),
                 ((state.get("goal_contract") or {}).get("body") or {}).get("milestones"))
    return apply(rows, moves, contract_token=contract_token, at=at, new_id=new_id)


def history(rows) -> list[dict]:
    """Every recorded move, oldest first per finding: ``{"finding", "from", "to", "contract_token", "at"}``."""
    return [{"finding": row.get("id"), **{key: copy.deepcopy(entry.get(key))
                                         for key in ("from", "to", "contract_token", "at")}}
            for row in rows or [] if isinstance(row, dict)
            for entry in row.get("scope_history") or [] if isinstance(entry, dict)]
