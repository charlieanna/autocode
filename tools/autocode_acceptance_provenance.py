"""Authenticate acceptance cases delivered under an earlier approved plan.

These receipts permit a pre-Builder test to pass on today's code. They never
approve a new plan, relabel its cases, or satisfy its fresh regression proof.
Reapproval in the same turn retains only an intact earlier failure; completed
turns additionally require their sealed conversation-boundary provenance.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_contract_identity as identity
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_contract_identity as identity
    import autocode_test_cases as test_cases
    import autocode_util as util


def prior_contract(state: RunState) -> dict | None:
    """The latest completed turn's exact, sealed and authenticated approved plan.

    A historical approval alone is insufficient. The runner's completed-turn
    receipt and the same feedback event in both source ledgers must identify it.
    Missing or ambiguous legacy provenance supplies no inherited authority.
    """
    try:
        turns, stages = state.get("turns"), state.get("stages")
        events, feedback = state.get("user_events"), state.get("brief_feedback")
        history = state.get("contract_history")
        if (
            not isinstance(turns, list)
            or not turns
            or not isinstance(stages, list)
            or not isinstance(events, list)
            or not isinstance(feedback, list)
            or not isinstance(history, list)
        ):
            return None
        turn = turns[-1]
        if not isinstance(turn, dict) or (turn.get("previous") or {}).get("status") != "TASK_COMPLETE":
            return None
        boundary = turn.get("stage_index")
        if type(boundary) is not int or not 0 < boundary <= len(stages):
            return None
        matches = [row for row in events if isinstance(row, dict) and row.get("id") == turn.get("event_id")]
        sources = [row for row in feedback if isinstance(row, dict) and row.get("id") == turn.get("event_id")]
        if (
            len(matches) != 1
            or sources != matches
            or matches[0].get("kind") != "brief_feedback"
            or matches[0].get("actor") != "user_cli"
            or matches[0].get("text") != turn.get("say")
            or not isinstance(turn.get("event_id"), str)
            or not turn["event_id"]
        ):
            return None
        token = matches[0].get("contract_token")
        contracts = [
            row
            for row in [*history, state.get("goal_contract")]
            if isinstance(row, dict) and "revision" in row and "hash" in row and identity.token(row) == token
        ]
        # Exact restoration keeps the discarded history, including an identical
        # historical copy of the restored contract. Such copies have one
        # identity; conflicting copies never establish completed-turn authority.
        if not contracts or any(row != contracts[0] for row in contracts[1:]):
            return None
        contract = contracts[0]
        if not identity.approved({**state, "goal_contract": contract}):
            return None
        if events.index(contract["approval_event"]) >= events.index(matches[0]):
            return None
        return contract
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _archives(state: RunState, contract, row, *, name=None):
    receipts = (state.get("acceptance_tests") or {}).get("receipts") or {}
    if not isinstance(receipts, dict):
        return
    cid = row["id"]
    name = name or test_cases.declared_test_name(
        str(row["verification_method"]).strip()[len(test_cases.MARK) :].strip()
    )
    if not name:
        return
    for receipt in receipts.values():
        if (
            not isinstance(receipt, dict)
            or receipt.get("contract_hash") != contract["hash"]
            or cid not in (receipt.get("case_ids") or [])
            or (verdict := (receipt.get("results") or {}).get(cid)) not in ("fails", "already passes")
            or not receipt.get("source_revision")
        ):
            continue
        for path, entry in (receipt.get("files") or {}).items():
            archive = Path(entry["archive"])
            if not entry.get("sha256") or util.file_hash(archive) != entry["sha256"]:
                continue
            archived = archive.read_bytes()
            if test_cases.named_in(name, archived.decode(errors="replace")):
                yield path, archived, verdict


def _has_fail_first_origin(state: RunState, row, path, archived) -> bool:
    """An already-passing receipt traces to an earlier completed turn's red test."""
    turns = state["turns"]
    for end in range(1, len(turns)):
        earlier = copy.copy(state)
        earlier["turns"] = turns[:end]
        contract = prior_contract(earlier)
        if contract is None or turns[end - 1]["stage_index"] >= turns[-1]["stage_index"]:
            continue
        rows = [old for old in contract["body"]["acceptance_criteria"] if old.get("id") == row["id"]]
        if rows != [row]:
            continue
        if any(
            verdict == "fails" and old_path == path and acceptance.kept(original, archived)
            for old_path, original, verdict in _archives(state, contract, row)
        ):
            return True
    return False


def _reapproved_cases(state: RunState, reports, workspace) -> set[str]:
    """An unchanged same-turn test may already pass after the Builder implemented it.

    Only a sealed earlier approval and an intact runner-recorded failure admit
    reuse. Citation additions and an exact binding of an old case-id selector
    do not change its behavior. Nothing here approves a plan or proves the fix.
    """
    try:
        if not identity.approved(state):
            return set()
        events = state["user_events"]
        current = state.get("goal_contract") or {}
        if events.count(current["approval_event"]) != 1:
            return set()
        boundary = -1
        if state.get("turns"):
            event_id = state["turns"][-1]["event_id"]
            indices = [i for i, event in enumerate(events) if event.get("id") == event_id]
            if len(indices) != 1:
                return set()
            boundary = indices[0]
        history = state.get("contract_history") or []
        reproduced = {case["id"] for case in test_cases.diagnosis_cases(state)}
        result = set()
        for contract in history:
            approval = identity.historical_approval(state, contract)
            if (
                approval is None
                or not boundary < events.index(approval) < events.index(current["approval_event"])
                or any(other != contract for other in history if identity.token(other) == identity.token(contract))
            ):
                continue
            old = {row["id"]: row for row in contract["body"]["acceptance_criteria"]}
            for row in current["body"]["acceptance_criteria"]:
                cid, method = row["id"], row.get("verification_method")
                previous = old.get(cid)
                if cid in reproduced or previous is None or test_cases.mark(method) != test_cases.MARK:
                    continue
                semantic = lambda value: {k: v for k, v in value.items() if k not in ("sources", "verification_method")}
                if (
                    semantic(previous) != semantic(row)
                    or test_cases.mark(previous.get("verification_method")) != test_cases.MARK
                ):
                    continue
                name = test_cases.declared_test_name(str(method).strip()[len(test_cases.MARK) :].strip())
                old_name = test_cases.declared_test_name(
                    str(previous["verification_method"]).strip()[len(test_cases.MARK) :].strip()
                )
                if not name or (
                    old_name != name if old_name else not test_cases.match_cases([{"id": cid}], [name])[cid]
                ):
                    continue
                reported = [entry for entry in reports or [] if entry.get("case_id") == cid]
                if reports is not None and (len(reported) != 1 or reported[0].get("test_name") != name):
                    continue
                for path, archived, verdict in _archives(state, contract, previous, name=name):
                    if verdict != "fails" or reports is not None and reported[0].get("file") != path:
                        continue
                    if workspace is not None and not acceptance.kept(archived, (Path(workspace) / path).read_bytes()):
                        continue
                    result.add(cid)
        return result
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return set()


def inherited_cases(state: RunState, *, reports: list[dict] | None = None, workspace=None) -> set[str]:
    """Exact unchanged restore cases with intact, prior fail-first generation receipts.

    When applying a report, its file and selector must also match the archived
    test, whose lines must remain intact in the current file. A current diagnosis
    reproduction never qualifies. Later turns retain an already-passing receipt
    only with its intact completed-turn fail-first origin. No result is relabelled
    and no historical final proof is reused. The caller runs today's tests.
    """
    reapproved = _reapproved_cases(state, reports, workspace)
    prior = prior_contract(state)
    if prior is None:
        return reapproved
    try:
        contract = state.get("goal_contract") or {}
        if not identity.approved(state):
            return set()
        old = {row["id"]: row for row in prior["body"]["acceptance_criteria"]}
        current = contract["body"]["acceptance_criteria"]
        reproduction = {
            case["id"] for case in test_cases.diagnosis_cases(state) if case.get("kind", "restore") != "preserve"
        }
        result = set(reapproved)
        for row in current:
            cid, method = row["id"], row.get("verification_method")
            if cid in reproduction or old.get(cid) != row or test_cases.mark(method) != test_cases.MARK:
                continue
            name = test_cases.declared_test_name(str(method).strip()[len(test_cases.MARK) :].strip())
            if not name:
                continue
            reported = [entry for entry in reports or [] if entry.get("case_id") == cid]
            if reports is not None and (len(reported) != 1 or reported[0].get("test_name") != name):
                continue
            for path, archived, verdict in _archives(state, prior, row):
                if reports is not None and reported[0].get("file") != path:
                    continue
                if verdict == "already passes" and not _has_fail_first_origin(state, row, path, archived):
                    continue
                if workspace is not None and not acceptance.kept(archived, (Path(workspace) / path).read_bytes()):
                    continue
                result.add(cid)
        return result
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return reapproved
