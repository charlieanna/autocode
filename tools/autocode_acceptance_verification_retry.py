"""Bounded runner-only retries of a completed acceptance writer's verification.

The acceptance_tests record owns the pending report, its pins and spent retries.
No receipt, model call, usage reset or plan authority is supplied here.

A retry whose ``kind`` is RUNNER_NOT_STARTED (#3877: the test command could not start, so the
written tests were never judged) runs again on each explicit resume instead of stopping at LIMIT:
it makes no model call, and once its runner starts it is archived (``runner_started``) and the
ordinary verdict paths, with the normal per-case proof, decide.

What the next resume does (re-check the saved tests, retire the retry for a fresh Tester attempt under
test-cause Investigator advice, or nothing because the bound is spent) is the one answer of
autocode_acceptance_retry_next, which the status view, recovery card, chat input and progress line read too.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_provenance as provenance  # noqa: F401 - tests patch it here
    from . import autocode_acceptance_retry_admission as admission
    from . import autocode_acceptance_retry_next as retry_next
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_planning_advice as recovery_binding  # noqa: F401 - tests bind advice here
    from . import autocode_report_source as report_source
    from . import autocode_result_application as application
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_provenance as provenance  # noqa: F401 - tests patch it here
    import autocode_acceptance_retry_admission as admission
    import autocode_acceptance_retry_next as retry_next
    import autocode_acceptance_tests as acceptance
    import autocode_planning_advice as recovery_binding  # noqa: F401 - tests bind advice here
    import autocode_report_source as report_source
    import autocode_result_application as application
    import autocode_run_status as run_status
    import autocode_util as util

# The bound itself is the one answer's (autocode_acceptance_retry_next.LIMIT, unchanged at 1).
KEY = retry_next.KEY
LIMIT = retry_next.LIMIT
RUNNER_NOT_STARTED = retry_next.RUNNER_NOT_STARTED
ARTIFACTS = admission.ARTIFACTS


_record_current = admission.record_current


def retain(state: RunState, value, record, workspace, reason, *, kind=None, receipt=None):
    """Queue one fresh local verification; never replenish a saved allowance.

    ``kind`` (RUNNER_NOT_STARTED) is kept on the record, also on one already pending, so a re-run whose
    test command could not start is retried on the next resume; ``receipt`` (the failed command's command,
    exit code, output and tail) is kept in its failure entry."""
    saved = state.setdefault(acceptance.KEY, {})
    pending = saved.get(KEY)
    failure = {"at": util.now(), "reason": str(reason), **({"kind": kind} if kind else {}), **(receipt or {})}
    if pending:
        pending.setdefault("failures", []).append(failure)
        if kind == RUNNER_NOT_STARTED:
            pending["kind"] = kind
        return False
    try:
        if not _record_current(state, record, workspace) or value != util.read(record["output"]):
            return False
        pins = {record[key]: util.file_hash(record[key]) for key in ARTIFACTS}
    except (OSError, KeyError, TypeError, ValueError):
        return False
    saved[KEY] = {
        "record": copy.deepcopy(record),
        "value": copy.deepcopy(value),
        "pins": pins,
        "attempts": 0,
        "failures": [failure],
        **({"kind": kind} if kind else {}),
    }
    run_status.running(state, "EXECUTING", next_stage=acceptance.STAGE)
    return True


def retain_legacy(state: RunState, workspace):
    """An explicit resume may recover this old execution-only pause from pinned events."""
    if (
        state.get("status") != "PAUSED_ACCEPTANCE_TESTS"
        or state.get("next_stage") != acceptance.STAGE
        or not str(state.get("stop_reason", "")).startswith(
            "The runner could not establish the approved pre-build proof: Cannot classify passing tests "
            "against the original reference:"
        )
        or any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "uncertain_artifacts", "pending_report_repair")
        )
        or (state.get(acceptance.KEY) or {}).get(KEY)
    ):
        return False
    try:
        record = state["stages"][-1]
        if record.get("engine") != "opencode" or record.get("output_mode") != "opencode_events":
            return False
        pin = record["metrics"]["request_context"]["accounting"]["event_file"]
        if pin["path"] != record["events"] or util.file_hash(pin["path"]) != pin["sha256"]:
            return False
        extracted = report_source.original_report_for_repair(record)
        value = extracted["report"]
        if extracted.get("extraction_error") or not isinstance(value, dict) or value.get("cannot_write"):
            return False
        return retain(state, value, record, workspace, state["stop_reason"])
    except (OSError, IndexError, KeyError, TypeError, ValueError):
        return False


def finish(state: RunState, *, retired_by=None):
    """Archive the retry and its failures after a genuine verification transition.

    ``retired_by`` is the Investigator result ({identity, requested_at}) whose test-cause advice retired it
    for a fresh Tester attempt; the archived attempts and failures stay as evidence."""
    saved = state.get(acceptance.KEY) or {}
    if pending := saved.pop(KEY, None):
        if retired_by is not None:
            pending["retired_by"] = copy.deepcopy(retired_by)
        saved.setdefault("verification_retries", []).append(pending)


def runner_started(state: RunState):
    """A runner-not-started retry whose test command now started is archived; the ordinary paths continue."""
    if ((state.get(acceptance.KEY) or {}).get(KEY) or {}).get("kind") == RUNNER_NOT_STARTED:
        finish(state)


def dispatch(runtime, state: RunState, stage, workspace, run_dir, *, apply):
    """Apply a pinned report through ordinary owned checks, without saving/charging it twice.

    None: no retained retry to apply, or test-cause Investigator advice retired it; the caller then
    dispatches the ordinary test-writing step."""
    pending = (state.get(acceptance.KEY) or {}).get(KEY)
    if stage != acceptance.STAGE or pending is None:
        return None
    checking = False
    try:
        answer = retry_next.decide(state, workspace) or {}
        if replacement := answer.get("replacement"):
            candidate = copy.deepcopy(state)
            candidate[acceptance.KEY][KEY]["superseded_by"] = replacement
            finish(candidate)
            candidate.setdefault("user_events", []).append(
                {"kind": "acceptance_report_superseded", "actor": "runner", "at": util.now(), **replacement}
            )
            runtime.write_json(Path(run_dir) / "state.json", candidate)
            cast(dict, state).clear()
            state.update(candidate)
            return None
        if refusal := admission.problem(state, pending, workspace):
            raise ValueError(refusal)
        record = copy.deepcopy(pending["record"])
        value = runtime.load_stage_report(record, workspace, state=state)
        if value != pending["value"]:
            raise ValueError("Retained acceptance report no longer matches its validated value")
        answer = retry_next.decide(state, workspace) or {}
        if answer.get("next") == retry_next.TESTER:
            # The Investigator put the cause in the saved tests or report: re-reading the pinned report cannot
            # edit them. Retire the retry (its attempts and failures stay as evidence) and let the ordinary
            # test-writing step run with the guidance; this result never retires another retry.
            finish(state, retired_by=answer["retired_by"])
            runtime.write_json(Path(run_dir) / "state.json", state)
            return None
        if answer.get("next") != retry_next.RECHECK:
            raise ValueError("The bounded acceptance verification retry is exhausted")
        pending["attempts"] += 1
        if answer.get("grant"):
            pending.setdefault("investigations", []).append(answer["grant"])
        if answer.get("provenance"):
            pending["provenance_retry"] = True
        checking = True
        state.setdefault("reconciliation_notes", []).append(
            {
                "at": util.now(),
                "stage": stage,
                "reason": retry_next.RECHECK_REASON,
                "output": record["output"],
                "attempt": pending["attempts"],
            }
        )
        # Spend before execution: a crash or another Resume never resets this bound.
        runtime.write_json(Path(run_dir) / "state.json", state)
        application.commit(
            state,
            run_dir,
            stage,
            lambda candidate: apply(stage, candidate, value, record, workspace, run_dir=run_dir),
            lambda candidate: runtime.commit_boundary_candidate(state, candidate, run_dir, workspace),
            persist=runtime.write_json,
        )
        return record
    except (OSError, ValueError, KeyError) as error:
        if checking:
            failure = {"at": util.now(), "reason": str(error)}
            original_reference = getattr(error, "original_reference", None)
            if original_reference:
                failure["original_reference"] = copy.deepcopy(original_reference)
            pending["failures"].append(failure)
            runtime.write_json(Path(run_dir) / "state.json", state)
        # Existing bounded investigation owns machinery after the local retry.
        raise util.Paused(
            "PAUSED_INVALID_OUTPUT", f"Acceptance verification retry could not finish: {error}"
        ) from error
