"""A regression proof that does not pass becomes a runner-owned blocking finding (#2475).

A Tester can honestly report PASS while the runner's own regression proof refuses the change: a
named test missing, existing behavior unproven, a test that passes on base failing now. Before this
module a Completion Reviewer that still asked to complete stopped the run at the completion gate,
and resuming only repeated the same review, so the person was handed a stop about AutoCode's own
machinery. Instead, before a judge reviews a source, ``reconcile`` opens (or refreshes) one blocking
finding with source ``runner`` naming the proof's reasons, and closes it once the proof passes on a
source it has not failed on. The ordinary route then carries it: the reviewers and the Resolver see
it in ``open_findings``, and a correction task assigns it to the Builder (``actionable_findings``).

A reviewer that asks to complete while the finding is open is sent back once for that source with
SEND_BACK_NOTE (``send_back``); a second request for the same source pauses PAUSED_COMPLETION_REVIEW,
which the Investigator takes (autocode_stuck_job.RETRYABLE), never a person question. The same holds
when a test the original code passes failed on a source whose proof then passed (``intermittent``,
#2942): the finding stays open, and the request is sent back with INTERMITTENT_NOTE instead of stopping
at the completion gate.

Only this module writes or closes ``runner`` findings marked ``regression_proof``. Imports nothing
above the findings ledger; the caller passes whether the proof is owed and proven.
State: findings_ledger rows with source ``runner``, ``regression_proof`` and ``failed_revisions``;
``unresolved_findings``; ``completion_sent_back`` (shared with units.autoreview.completion_review:
one send-back per artifact).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_findings as findings_ledger
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_findings as findings_ledger
    import autocode_run_status as run_status
    import autocode_util as util

SOURCE = "runner"
MARK = "regression_proof"
SEND_BACK_NOTE = (
    "The runner refused completion: its regression proof does not pass for this source, and its "
    "blocking finding stays open until the proof passes on new source. Return REWORK so the Builder "
    "can correct the change or its tests."
)
# The suite judgment's words for a test that passed on the original code and failed with the change
# (autocode_verify_judgment.judge_suite): a proof that failed only for this on a source and then passed on the
# same source saw that test fail intermittently with the change (#2942).
REGRESSED = "Tests that pass on base fail on the candidate"
INTERMITTENT_NOTE = (
    "The runner refused completion: its regression proof of this source failed because a test that passes on "
    "the original code failed with the change, and the same source then passed. A test that fails some of the "
    "time with the change counts as flaky only when it also fails on the original code, which the runner did not "
    "see, so its blocking finding stays open until the proof passes on new source. Return REWORK so the Builder "
    "can make the change, or the test, reliable."
)
STOP = (
    "The Completion Reviewer asked again to complete while the runner's regression proof does not pass "
    "for this source; the Investigator decides how the work continues"
)


def open_rows(state: RunState):
    return [row for row in findings_ledger.open_entries(state, SOURCE) if row.get(MARK)]


def reconcile(
    state: RunState, revision, *, required, proved, reason="", settle_same_revision_ids=(), settle_evidence=None
):
    """Open, refresh or close the proof finding for the source a judge is about to review; returns the open row.

    ``required`` and ``proved`` are autocode_regression.required/complete for final completion, and
    ``reason`` its rejection. Only a proof of ``revision`` opens one; a proved source the finding has
    not failed on closes it, as does a contract that no longer owes a proof. ``settle_evidence`` maps a
    settled id to its resolution evidence when the caller's exception names more than the new receipt.
    """
    at = util.now()
    rows = open_rows(state)
    proof = state.get("regression_proof") or {}
    if proved or not required:
        for row in rows:
            if (
                not required
                or revision not in (row.get("failed_revisions") or [])
                or row.get("id") in settle_same_revision_ids
            ):
                # Closed where the other runner findings are: by the receipt that settled it.
                findings_ledger.settle(
                    row,
                    "resolved",
                    resolved_at=at,
                    resolved_in=proof.get("path")
                    if required
                    else "The approved contract no longer owes a regression proof",
                    resolution_evidence=(settle_evidence or {}).get(row.get("id"))
                    or f"Runner regression proof passes on source {str(revision)[:12]}"
                    if required
                    else "The approved contract no longer owes a regression proof",
                )
        return None
    if proof.get("source_revision") != revision or not reason:
        return rows[0] if rows else None
    text = (
        f"The runner's regression proof does not pass on source {str(revision)[:12]}. {reason[:1500]}. "
        "Completion needs it to pass; correct the change or its tests."
    )
    evidence = f"Runner regression proof receipt: {proof.get('path')}"
    row = rows[0] if rows else None
    if row is None:
        row = {
            "id": findings_ledger.allocate_id(state),
            "source": SOURCE,
            "severity": "high",
            "finding": text,
            "evidence": evidence,
            "blocking": True,
            "status": "open",
            "opened_at": at,
            "opened_in": proof.get("path"),
            "scope": findings_ledger.report_scope(state),
            "times_reported": 1,
            "last_reported_at": at,
            "last_reported_in": proof.get("path"),
            "assigned_task": None,
            MARK: True,
        }
        findings_ledger.ledger(state).append(row)
    elif row.get("finding") != text or revision not in (row.get("failed_revisions") or []):
        row.update(
            finding=text,
            evidence=evidence,
            times_reported=row.get("times_reported", 1) + 1,
            last_reported_at=at,
            last_reported_in=proof.get("path"),
        )
    if revision not in row.setdefault("failed_revisions", []):
        row["failed_revisions"].append(revision)
    brief = {key: row[key] for key in ("id", "source", "severity", "finding", "evidence", "blocking")}
    state["unresolved_findings"] = [
        *[item for item in state.get("unresolved_findings") or [] if item.get("id") != row["id"]],
        brief,
    ]
    return row


def intermittent(state: RunState, revision) -> bool:
    """Whether an open proof finding failed this source only because a test the original code passes failed.

    The caller asks once the proof of ``revision`` passes again: reconcile leaves such a finding open (a later
    pass must not hide a failure that comes and goes), so without this route the completion gate stopped
    the run on it at PAUSED_COMPLETION_GATE, a stop about AutoCode's own machinery.
    """
    return any(
        revision in (row.get("failed_revisions") or []) and REGRESSED in (row.get("finding") or "")
        for row in open_rows(state)
    )


def send_back(state: RunState, revision, note=SEND_BACK_NOTE):
    """The fields for a completion request refused while the proof finding is open.

    The first time for this source the Completion Reviewer reviews again with ``note`` (SEND_BACK_NOTE, or
    INTERMITTENT_NOTE when the proof failed and then passed on it); the second pauses
    PAUSED_COMPLETION_REVIEW for the Investigator.
    """
    if state.get("completion_sent_back") == revision:
        return run_status.pause({}, "PAUSED_COMPLETION_REVIEW", STOP)
    state["completion_sent_back"] = revision
    return {"status": "RUNNING", "stop_reason": note}
