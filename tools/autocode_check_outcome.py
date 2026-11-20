"""What a check's receipt establishes: it passed, it failed, or it could not establish a result.

A check that could not establish a result (it timed out, its runner never started, its test session
aborted, or its supervision receipt does not show a completed run) was treated as if it had run and found
a product or test defect, so AutoCode gave a wrong verdict or sent paid work to the wrong stage (#3874,
#3889, #3895, #3877). The regression proof's preservation judgment, the protected-test replay and the
acceptance-test recovery read this one classification instead of each keeping a predicate of its own.

It decides only from what the receipt shows: the ``timed_out`` flag, ``exit_code``, ``error``, supervision
completeness (autocode_command_receipt.completed), ``results_expected``, whether the results file the
command was told to write exists (``results_file_written``), the per-test results and the parser's
``aborted`` marker. It never keys on a runner or a language. An undetermined outcome never passes:
it only stops being reported as a product or test failure.
"""

from __future__ import annotations

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_util as util

PASSED, FAILED, UNDETERMINED = "passed", "failed", "undetermined"
TIMEOUT = "timeout"
RUNNER_NOT_STARTED = "runner_not_started"
SESSION_ABORTED = "session_aborted"
INCOMPLETE_RECEIPT = "incomplete_receipt"
STATUS = "PAUSED_VERIFICATION_UNCERTAIN"


class CheckUndetermined(util.Paused):
    """A check could not establish a result: completion stays blocked, and no product edit is directed.

    ``sets_attempt_aside`` makes it a boundary pause (util.boundary_pause): the finished reviewing attempt
    that ran the check is set aside under this status instead of being recorded as a rejected answer
    (autocode_run_records.set_aside_at_completion_gate), so --resume-paused runs that stage again."""

    sets_attempt_aside = True

    def __init__(self, reason, *, outcome=None, receipt=None):
        super().__init__(STATUS, reason)
        self.outcome = outcome
        self.receipt = receipt


def _seconds(value) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _last_line(text) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def _result(outcome, reason="", detail="", failed=()) -> dict:
    return {"outcome": outcome, "reason": reason, "detail": detail, "failed": sorted(set(failed))}


def _incomplete(receipt) -> str:
    errors = receipt.get("supervision_errors")
    if isinstance(errors, list) and errors:
        return "supervision receipt is incomplete: " + "; ".join(str(error) for error in errors)[:300]
    if receipt.get("interrupted"):
        return "supervision receipt is incomplete: the run was interrupted"
    return "supervision receipt does not show a completed, cleaned-up run"


def classify(receipt) -> dict:
    """``{"outcome", "reason", "detail", "failed"}`` for one check receipt.

    ``outcome`` is PASSED, FAILED (a completed run with a nonzero exit, an error or named failures, which
    ``failed`` lists; its receipt already says why) or UNDETERMINED with a ``reason`` (TIMEOUT,
    INCOMPLETE_RECEIPT, SESSION_ABORTED or RUNNER_NOT_STARTED) and a ``detail``; an undetermined run keeps in
    ``failed`` any failure it had already recorded. A merged receipt (autocode_scratch_suites.merged) is read
    part by part: any failed part wins, with every failed part's names, else the first undetermined part,
    whose detail names it.
    """
    if isinstance(receipt, dict) and isinstance(receipt.get("suites"), list) and receipt["suites"]:
        parts = [classify(part) for part in receipt["suites"]]
        failing = [part for part in parts if part["outcome"] == FAILED]
        if failing:
            return _result(FAILED, failed=[name for part in failing for name in part["failed"]])
        for index, part in enumerate(parts, 1):
            if part["outcome"] == UNDETERMINED:
                return {**part, "detail": f"{part['detail']} (in part {index} of {len(parts)})"}
        return _result(PASSED)
    if not isinstance(receipt, dict):
        return _result(UNDETERMINED, INCOMPLETE_RECEIPT, "supervision receipt is missing: no run was recorded")
    results = receipt.get("results") if isinstance(receipt.get("results"), dict) else None
    recorded = [str(name) for name in (results or {}).get("failed") or []]
    exit_code = receipt.get("exit_code")
    if receipt.get("timed_out"):
        duration = receipt.get("duration_seconds")
        detail = "timed out" + (
            f" after {_seconds(duration)} s"
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else ""
        )
        return _result(UNDETERMINED, TIMEOUT, detail, recorded)
    if receipt.get("error"):
        # A legacy error string (zero tests reported, a scratch copy that could not be made) keeps today's
        # failure handling.
        return _result(FAILED, failed=recorded)
    if set(command_receipt.OWNERSHIP_FIELDS) & set(receipt) and not command_receipt.completed(receipt):
        return _result(UNDETERMINED, INCOMPLETE_RECEIPT, _incomplete(receipt), recorded)
    aborted = (results or {}).get("aborted")
    if isinstance(aborted, dict):
        diagnostic = str(aborted.get("diagnostic") or "")
        detail = f"aborted its test session (exit {aborted.get('exit_code')})" + (
            f": {diagnostic}" if diagnostic else ""
        )
        return _result(UNDETERMINED, SESSION_ABORTED, detail, recorded)
    if (
        results is None
        and receipt.get("results_expected")
        and receipt.get("results_file_written") is False
        and exit_code not in (0, None)
    ):
        # Only a runner told to write a results file, that wrote none, did not start. A runner that started and
        # could not load a test module (unittest exits 1 on an import-time error) failed: its test is defective.
        line = _last_line(receipt.get("tail"))
        detail = f"could not start its test runner (exit {exit_code})" + (f": {line}" if line else "")
        return _result(UNDETERMINED, RUNNER_NOT_STARTED, detail)
    if exit_code != 0 or recorded:
        return _result(FAILED, failed=recorded)
    return _result(PASSED)


def describe(subject: str, outcome: dict) -> str:
    """``subject`` (``the candidate half``) and what kept it from establishing a result, as one clause."""
    if outcome.get("reason") == INCOMPLETE_RECEIPT:
        return f"{subject}'s {outcome['detail']}"
    return f"{subject} {outcome.get('detail') or 'could not establish a result'}"
