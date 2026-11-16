"""Bind a qualified case's test strengthening to its accepted independent review.

The existing changed-code rework record owns the allowance. This module grants
neither a new round nor completion: it identifies the independent test input that
the already queued round needs. The review hash is read from its accepted evidence
pins (and checked here), so replay derives it from its own current review artifact;
no model supplies a repair identity or a path-derived token.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

FIELD = "evidence_repair"


def _intact(path, digest):
    try:
        return isinstance(path, str) and bool(path) and isinstance(digest, str) and util.file_hash(path) == digest
    except (OSError, ValueError, TypeError):
        return False


def pending(state: RunState, cases):
    """A current, pinned missing-test review whose existing round still needs a writer.

    Reading the canonical validation also recovers runs saved by older runtimes
    that queued the Builder before recognizing independent test ownership.
    """
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    proof, validation = state.get("regression_proof") or {}, state.get("validation") or {}
    assessment = proof.get("changed_code_evidence") or {}
    source, output = proof.get("source_revision"), validation.get("output")
    case_ids = [
        case["id"] for case in cases if not task.get("acceptance_criteria") or case["id"] in task["acceptance_criteria"]
    ]
    if (
        not case_ids
        or not task.get("id")
        or task.get("kind") != "implement"
        or contract.get("approval_status") != "approved"
        or proof.get("verdict") != "PASS"
        or not source
        or assessment.get("source_revision") != source
        or assessment.get("status") != "UNVERIFIED"
        or validation.get("source_revision") != source
        or validation.get("task_id") != task["id"]
        or validation.get("reviewer_role") not in ("tester", "task_planner")
        or any(validation.get("contract_" + key) != contract.get(key) for key in ("hash", "revision"))
        or not _intact(output, (validation.get("evidence_hashes") or {}).get(output))
    ):
        return None
    event = next(
        (
            row
            for row in reversed(state.get("regression_proofs") or [])
            if row.get("kind") == "changed_code_rework" and row.get("review_output") == output
        ),
        None,
    )
    if not event or (
        event.get("action") != "builder"
        or event.get("source_revision") != source
        or event.get("task_id") != task["id"]
        or any(event.get("contract_" + key) != contract.get(key) for key in ("hash", "revision"))
        or event.get("assessment") != assessment
    ):
        return None
    records = [row for row in state.get("stages") or [] if row.get("output") == output]
    if len(records) != 1:
        return None
    record = records[0]
    if (
        (record.get("original_stage") or record.get("stage"), record.get("role"))
        not in (("tester", "tester"), ("review_checkpoint", "task_planner"))
        or record.get("source_revision") != source
        or record.get("task_id") != task["id"]
        or record.get("exit_code") != 0
        or record.get("accounted") is not True
        or record.get("changed_files") != []
        or any(record.get(key) for key in ("rejected", "rolled_back", "interrupted", "abandoned", "timed_out"))
    ):
        return None
    gaps = {row["gap_id"]: row for row in assessment.get("obligations") or [] if row.get("gap_id")}
    rejected = [
        row
        for row in validation.get("changed_code_decisions") or []
        if row.get("accepted") is False and row.get("reason_kind") == "missing_test"
    ]
    if not rejected or any(
        row.get("source_revision") != source
        or row.get("proof_context") != assessment.get("proof_context")
        or row.get("reviewer", {}).get("output") != output
        or any(row.get(key) != gaps.get(row.get("gap_id"), {}).get(key) for key in ("path", "sha256", "line"))
        for row in rejected
    ):
        return None
    workspace = state.get("workspace")
    if workspace and any(not _intact(str(Path(workspace) / row["path"]), row["sha256"]) for row in rejected):
        return None
    binding = {
        "task_id": task["id"],
        "contract_hash": contract["hash"],
        "contract_revision": contract["revision"],
        "source_revision": source,
        "review_output": output,
        "review_sha256": validation["evidence_hashes"][output],
        "case_ids": case_ids,
    }
    if any(row.get(FIELD) == binding for row in (state.get("acceptance_tests") or {}).get("receipts", {}).values()):
        return None
    return {
        "binding": binding,
        "proof_path": proof.get("path"),
        "rejected": rejected,
        "open_line_count": sum(row.get("status") == "reason_required" for row in gaps.values()),
    }


def packet(state: RunState):
    """Only the repair bound when routing may enter a writer's handoff."""
    bound = (state.get("acceptance_tests") or {}).get("pending_evidence_repair")
    if not bound or not isinstance(bound.get("binding"), dict):
        return None
    current = pending(state, [{"id": cid} for cid in bound["binding"].get("case_ids", [])])
    return current if current and current["binding"] == bound["binding"] else None
