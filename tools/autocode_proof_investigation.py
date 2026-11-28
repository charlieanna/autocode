"""A regression proof that still fails after the Builder's direct round goes to the failure Investigator.

The first proof of a source that does not pass goes straight back to the Builder (autocode_proof_findings
.builder_round, #4211). When the Builder's next source fails its proof too, the run used to launch the Tester,
which could only relay the runner's own verdict, then the Completion Reviewer, which asks for rework the proof
already decided, and often the Resolver before the Builder heard anything new. In the 208 saved runs of 13 to 16
November, the 6 runs whose proof failed on a second Builder source spent $5.14 on those relays ($0.86 a run),
where one failure classification costs about $0.29.

So, before the Tester of such a source, the failure goes to the Investigator's Builder-failure classification
(autocode_builder_failure), as a changed-code gap does after its direct repairs (autocode_changed_code_rework):
one read-only call that names the cause and the guidance the Builder takes next, or sends a plan defect back to
planning. A failing proof is AutoCode's own machinery, so the classification cannot ask the person
(``runner_observed`` in autocode_builder_failure), and one that names no cause gives the run back to the review
it stood in for, which runs as before.

Taken once for each source, never again for a finding whose classification named no cause or could not finish
(its ``proof_finding`` in the Investigator's history), and only when the classification can start now: the Investigator is enabled,
nothing else is running or being repaired, the Builder's record of this source is saved with its report,
and the milestone's classification budget (policy.classification_limit) is not spent. Otherwise the existing
route runs, the Tester first, and nothing pauses here. State: the proof finding row's ``investigated_revisions``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_failure as builder_failure
    from . import autocode_investigation_policy as investigation_policy
    from . import autocode_proof_findings as proof_findings
    from . import autocode_util as util
except ImportError:
    import autocode_builder_failure as builder_failure
    import autocode_investigation_policy as investigation_policy
    import autocode_proof_findings as proof_findings
    import autocode_util as util

REASON = "The regression proof of this change still fails after the Builder's direct round"


def builder_record(state: RunState, revision):
    """The last saved, accepted Builder record that produced ``revision``, or None."""
    for row in reversed(state.get("stages") or []):
        if row.get("stage") != "builder":
            continue
        if (
            row.get("source_revision") == revision
            and row.get("exit_code") == 0
            and not row.get("rejected")
            and row.get("output")
            and Path(row["output"]).is_file()
        ):
            return row
        return None  # a later Builder record than the one for this source: not this source's
    return None


def route(state: RunState, revision, review_stage="tester") -> bool:
    """Queue the failure classification for ``revision`` before ``review_stage``; True when it must not launch now.

    A classification that names no cause gives the run back to ``review_stage`` (autocode_stuck_job), never a pause.
    """
    proof = state.get("regression_proof") or {}
    if proof.get("source_revision") != revision or proof.get("verdict") not in ("FAIL", "UNVERIFIED"):
        return False
    row = next(
        (row for row in proof_findings.open_rows(state) if revision in (row.get("failed_revisions") or [])), None
    )
    if (
        row is None
        or len(row.get("builder_rounds") or []) < proof_findings.BUILDER_ROUNDS
        or revision in (row.get("investigated_revisions") or [])
        or any(entry.get("proof_finding") == row.get("id") for entry in state.get("stuck_investigations") or [])
        or not investigation_policy.enabled(state)
        or state.get("active_stage")
        or state.get("uncertain_artifacts")
        or state.get("pending_report_repair")
        or state.get("stuck_investigation")
        or state.get("builder_failure_hold")
    ):
        return False
    record = builder_record(state, revision)
    if record is None:
        return False
    receipt = proof.get("path")
    evidence = builder_failure.evidence(
        state,
        record,
        diagnosis={
            "regression_proof": {
                "verdict": proof.get("verdict"),
                "receipt": receipt,
                "finding_id": row.get("id"),
                "finding": row.get("finding"),
                "review_stage": review_stage,
            }
        },
    )
    if receipt and Path(receipt).is_file():
        # The Investigator may cite the proof's own receipt; its hash is pinned like the Builder's files.
        receipt = str(Path(receipt).resolve())
        evidence["evidence_refs"].append(receipt)
        evidence["binding"]["evidence_hashes"][receipt] = util.file_hash(receipt)
    identity = "builder-failure:" + evidence["failure_id"]
    if (
        not evidence["evidence_refs"]
        or evidence["record"].get("output") not in evidence["binding"]["evidence_hashes"]
        or any(entry.get("identity") == identity for entry in state.get("stuck_investigations") or [])
        or len(builder_failure.classified(state)) >= builder_failure.policy.classification_limit(state)
    ):
        return False
    reason = f"{REASON}. Finding {row.get('id')}: {row.get('finding')} Proof receipt: {proof.get('path')}"
    if not builder_failure.queue(state, evidence, reason, enabled=True, completed_record=record):
        return False
    row.setdefault("investigated_revisions", []).append(revision)
    return True
