"""Proofs refused for the runner's own reasons, superseded by fresh runner proof.

Two kinds: a disk-only collection failure, and a proof that judged nothing because what it ran on
changed while it ran (``disturbed``): AutoCode's own code was updated, the dependencies were
reinstalled, or the follow-up baseline moved. Before, such a proof became a blocking finding that only
a new source could close, so the Builder was sent to "correct the change or its tests" when nothing
was wrong with either, and the run paused at the completion gate (live run, 2026-11-26).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_util as util
    import autocode_verification_schedule as schedule


# The proof's own words when what it ran on changed under it (autocode_regression._proving and
# autocode_changed_code_continuation): it judged nothing about the change.
CONTEXT_CHANGED = "Execution context changed while proving the candidate"
FOLLOW_UP_CHANGED = "Follow-up baseline changed while proving the candidate"
CONTINUATION_CHANGED = "Execution context changed during mutation continuation"
DISTURBED = (CONTEXT_CHANGED, FOLLOW_UP_CHANGED, CONTINUATION_CHANGED)


def disturbed(proof) -> bool:
    """Whether ``proof`` (a proof summary or receipt) is UNVERIFIED because its context changed while it ran.

    Such a proof runs again in the context that holds now (autocode_regression.prove), never sends the
    Builder back. A proof that also lists a failure still counts, since nothing it saw can be trusted."""
    if not isinstance(proof, dict) or proof.get("verdict") != "UNVERIFIED":
        return False
    reasons = proof.get("unverified") or []
    return any(isinstance(reason, str) and reason.startswith(DISTURBED) for reason in reasons)


def disturbed_refusal(state: RunState, row, revision):
    """True when ``row``'s retained receipt refused only because its context changed, and a later proof passes.

    The caller must authenticate the current passing proof. A receipt that also names a failing test or
    another reason is not settled this way: that refusal judged the change."""
    prior = _superseded_receipt(state, row, revision)
    if prior is None or not disturbed(prior) or prior.get("failures"):
        return False
    return all(isinstance(reason, str) and reason.startswith(DISTURBED) for reason in prior.get("unverified") or [])


def _superseded_receipt(state: RunState, row, revision):
    """The runner-owned receipt ``row`` was last reported in, for ``revision``, when a later proof of it passes."""
    try:
        proof = state.get("regression_proof") or {}
        if proof.get("verdict") != "PASS" or proof.get("source_revision") != revision:
            return None
        root = Path(state["run_dir"]) / "regression"
        path = Path(row["last_reported_in"])
        if (
            not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
            or path.stat().st_nlink != 1
            or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root))
        ):
            return None
        record = next(
            (
                r
                for r in reversed(state.get("stages", []))
                if r.get("stage") == "regression_proof" and r.get("runner_owned") and r.get("output") == str(path)
            ),
            None,
        )
        if not record or datetime.fromisoformat(proof["proved_at"]) <= datetime.fromisoformat(record["finished_at"]):
            return None
        prior = util.read_object(path)
        return prior if isinstance(prior, dict) and prior.get("source_revision") == revision else None
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return None


def disk_collection_failure(state: RunState, row, revision):
    """True only for retained runner checks that collected no behavioral failure.

    The caller must authenticate the current passing proof. A report's assertion
    about infrastructure cannot settle a finding: the old owned receipt and logs
    must show disk exhaustion and collection errors, with no failing test names.
    """
    try:
        prior = _superseded_receipt(state, row, revision)
        if prior is None:
            return False
        path = Path(row["last_reported_in"])
        checks = prior["checks"]
        labels = {"regression_on_base", "regression_on_candidate", "suite_on_candidate"}
        if (
            prior.get("verdict") != "FAIL"
            or prior.get("source_revision") != revision
            or not isinstance(checks, dict)
            or not labels <= checks.keys()
            or not {"suite_on_base", "suite_on_base_with_tests"} & checks.keys()
            or not schedule.proof_intact(checks, root=path.parent)
        ):
            return False
        for label, check in checks.items():
            result = check.get("results") or {}
            failed = result.get("failed") or []
            collection = result.get("collection_errors") or []
            output = Path(check["output"])
            if (
                check.get("exit_code") != 1
                or not failed
                or not set(failed) <= set(collection)
                or any(not name.endswith("::[build failed]") for name in failed)
                or label.startswith("regression_")
                and result.get("passed")
                or util.file_hash(output) != check.get("output_sha256")
                or "no space left on device" not in output.read_text(errors="replace").lower()
            ):
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return False
