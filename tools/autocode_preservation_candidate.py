"""Recognize accepted independent test input for fresh review, never completion."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_contract_identity as contracts
    from . import autocode_coordination_context as coordination
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_contract_identity as contracts
    import autocode_coordination_context as coordination
    import autocode_test_cases as test_cases
    import autocode_util as util


def _passing_inputs(receipts, cases):
    observed = {cid for row in receipts for cid in row.get("case_ids", [])}
    known = {case["id"]: case for case in cases}
    if not observed or not observed <= known.keys():
        return False
    for cid in observed:
        values = {row.get("results", {}).get(cid) for row in receipts if cid in row.get("case_ids", [])}
        expected = (
            {"passes"} if known[cid].get("kind") == "preserve" else {acceptance.CANDIDATE_FAIL_FIRST, "already passes"}
        )
        if len(values) != 1 or not values <= expected:
            return False
    return True


def _writer_progress(state: RunState, record, before, receipts, cases, owned):
    """Trace this task's writer snapshots from assignment to the unchanged candidate.

    A red TDD input is not ready for review. The runner must already have observed
    these exact cases pass on the candidate and accepted any required original
    reference failure. That observation still grants no review PASS.
    """
    task = state["current_task"]
    contract = state["goal_contract"]
    revision = before["revision"]
    if (
        not task.get("source_revision")
        or record.get("contract_hash") != contract["hash"]
        or record.get("contract_revision") != contract["revision"]
        or record.get("accounted") is not True
        or type(record.get("exit_code")) is not int
        or record["exit_code"] != 0
        or any(
            record.get(key)
            for key in ("rejected", "rolled_back", "timed_out", "interrupted", "abandoned", "report_only", "dry_run")
        )
        or any(
            row.get("task_id") == task["id"]
            and row.get("source_revision") == revision
            and row.get("accepted_test_writer")
            for row in state.get("retained_candidate_handoffs") or []
        )
    ):
        return None
    reached = {task["source_revision"]: (None, None)}
    for writer in state.get("stages") or []:
        if (
            (writer.get("original_stage") or writer.get("stage")) != acceptance.STAGE
            or writer.get("task_id") != task["id"]
            or writer.get("contract_hash") != contract["hash"]
            or writer.get("contract_revision") != contract["revision"]
            or type(writer.get("exit_code")) is not int
            or writer["exit_code"] != 0
            or writer.get("accounted") is not True
            or any(
                writer.get(key)
                for key in (
                    "rejected",
                    "rolled_back",
                    "timed_out",
                    "interrupted",
                    "abandoned",
                    "report_only",
                    "dry_run",
                )
            )
        ):
            continue
        start = util.read_object(writer["before_ref"])
        end = util.read_object(writer["after_ref"])
        if (
            start.get("revision") not in reached
            or not isinstance(start.get("files"), dict)
            or not isinstance(end.get("files"), dict)
            or end.get("revision") != writer.get("source_revision")
            or start.get("head") != end.get("head")
            or end.get("head") != before.get("head")
        ):
            continue
        origin, previous = reached[start["revision"]]
        if previous is not None and previous != start:
            continue
        origin = origin or start
        changed = sorted(
            name
            for name in origin["files"].keys() | end["files"].keys()
            if origin["files"].get(name) != end["files"].get(name)
        )
        if not changed or any(
            not any(name == root.rstrip("/") or name.startswith(root.rstrip("/") + "/") for root in owned)
            for name in changed
        ):
            continue
        reached[end["revision"]] = (origin, end)
        matching = [row for row in receipts if row.get("output") == writer.get("output")]
        if end == before and _passing_inputs(matching, cases):
            return {"retained_paths": changed, "accepted_test_writer": writer["output"]}
    return None


def candidate(state: RunState, record, revision):
    """Accepted writer work or coordinated guards may reach fresh independent review.

    Both consume the same source/receipt integrity checks. Ordinary red TDD input,
    stale source and an already consumed writer handoff retain no-progress handling.
    """
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    cases = test_cases.proof_cases(state)
    if (
        not all(key in contract for key in ("task_id", "revision", "body"))
        or not contracts.approved(state)
        or not cases
        or task.get("contract_hash") != contract.get("hash")
        or task.get("kind") != "implement"
        or task.get("rolled_back")
        or not task.get("id")
        or record.get("task_id") != task["id"]
        or record.get("changed_files")
        or record.get("rolled_back")
        or record.get("source_revision") != revision
        or acceptance.is_blind(state)
    ):
        return None
    try:
        before = util.read_object(record["before_ref"])
        after = util.read_object(record["after_ref"])
        if before != after or after.get("revision") != revision or not isinstance(after.get("files"), dict):
            return None
        receipts = [
            row
            for row in acceptance.receipts(state).values()
            if row.get("contract_hash") == contract["hash"] and row.get("source_revision") == revision
        ]
        owned = task.get("affected_paths") or []
        writer = _writer_progress(state, record, before, receipts, cases, owned)
        preservation = coordination.context(state) and all(case.get("kind") == "preserve" for case in cases)
        if writer:
            receipts = [row for row in receipts if row.get("output") == writer["accepted_test_writer"]]
        elif not preservation or not {case["id"] for case in cases} <= {
            cid for row in receipts for cid in row.get("case_ids", [])
        }:
            return None
        files = {path: entry for row in receipts for path, entry in row.get("files", {}).items()}
        if not files or any(
            not any(path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/") for root in owned)
            for path in files
        ):
            return None
        workspace = Path(state["workspace"]).resolve()
        for path, entry in files.items():
            target = workspace / path
            if (
                not target.resolve().is_relative_to(workspace)
                or target.is_symlink()
                or not entry.get("sha256")
                or util.file_hash(entry["archive"]) != entry["sha256"]
                or util.file_hash(target) != entry["sha256"]
                or after["files"].get(path) != entry["sha256"]
            ):
                return None
        if writer:
            return {"source_revision": revision, **writer}
        if preservation:
            return {"source_revision": revision, "retained_paths": sorted(files), "acceptance_guards": True}
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return None
    return None
