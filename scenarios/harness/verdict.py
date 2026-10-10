"""Turn an AutoCode run's final status and the oracle's checks into a verdict.

The oracle is the authority on whether the work is correct; AutoCode's status
only says whether AutoCode claimed completion. The worst outcome is a false
completion: AutoCode said done and the oracle disagrees.
"""

from __future__ import annotations

import dataclasses
import inspect
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .oracle import Check
from . import evidence_reports

PASS = "PASS"  # AutoCode ended the way the scenario expects and the oracle agrees
FALSE_COMPLETE = "FALSE_COMPLETE"  # AutoCode completed but the oracle found failures, or it should have stopped
HONEST_BLOCKER = "HONEST_BLOCKER"  # AutoCode stopped and said why, without claiming completion
ERROR = "ERROR"  # the run or the oracle broke; no judgement possible
SKIPPED = "SKIPPED"  # a required tool or capability is missing
NOT_EXERCISED = "NOT_EXERCISED"  # the run never reached a stage the scenario exists to test
INTERRUPTED_UNGRADED = "INTERRUPTED_UNGRADED"  # supervision stopped; no final delivery/usage judgement exists
PENDING_UNGRADED = "PENDING_UNGRADED"  # an admitted harness owner has not published a final result

# An oracle may also define diagnosis(project, run): how a stage judged the failure the scenario plants
# (issue #59), scored apart from the run verdict above. Its verdicts, besides NOT_EXERCISED (the planted
# failure never reached the stage) and ERROR (diagnosis() itself crashed):
CORRECT = "CORRECT"  # the stage ran on the planted failure and every required check passed
INCORRECT = "INCORRECT"  # it ran on the planted failure and a required check failed
UNSCORED = "UNSCORED"  # it launched on the planted failure but saved no output to score
DIAGNOSIS_VERDICTS = (CORRECT, INCORRECT, UNSCORED, NOT_EXERCISED)

COMPLETE_STATUSES = ("TASK_COMPLETE", "COMPLETE")
STOPPED_PREFIXES = ("PAUSED_", "BLOCKED_HUMAN", "AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER")

# Why a run is not a pass, for campaigns that must not mix a wrong deliverable with a
# provider outage or a budget death (#455). The verdict stays the contract; this is a
# secondary class. Reuses the existing status/stop_reason vocabulary, never a second one.
STOP_CLASS_PASSED = "passed"
STOP_CLASS_FALSE = "false_completion"  # claimed done; wrong or should have stopped
STOP_CLASS_SAFETY = "safety_stop"  # correct pause/blocker for a person or a real defect
STOP_CLASS_PROVIDER = "provider_failure"  # provider, transport, or sandbox/setup
STOP_CLASS_HARNESS = "harness_failure"  # the harness or oracle broke; no judgement
STOP_CLASS_BUDGET = "budget_exhaustion"  # time, attempt or supervision budget
STOP_CLASS_NOT_EXERCISED = "not_exercised"
STOP_CLASS_SKIPPED = "skipped"
STOP_CLASSES = (
    STOP_CLASS_PASSED,
    STOP_CLASS_FALSE,
    STOP_CLASS_SAFETY,
    STOP_CLASS_PROVIDER,
    STOP_CLASS_HARNESS,
    STOP_CLASS_BUDGET,
    STOP_CLASS_NOT_EXERCISED,
    STOP_CLASS_SKIPPED,
)

# Status words that name a provider/setup stop, not a product decision.
_PROVIDER_STATUS = (
    "PAUSED_TOOL_CONTAINMENT",
    "PAUSED_PROVIDER_TIMEOUT",
    "PAUSED_CONTENT_FILTER",
    "PAUSED_AUTH",
    "PAUSED_QUOTA_ROUTE",
    "PAUSED_PROVIDER",
)
_BUDGET_STATUS = (
    "PAUSED_BUDGET",
    "PAUSED_INTERRUPTED",
    "PAUSED_ITERATION_LIMIT",
    "PAUSED_TIME_BUDGET",
    "PAUSED_STAGE_TIMEOUT",
)


@dataclass
class OracleResult:
    checks: list[Check] = field(default_factory=list)
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and bool(self.checks) and all(check.ok for check in self.checks)

    @property
    def summary(self) -> str:
        if self.error:
            return f"oracle error: {self.error.strip().splitlines()[-1]}"
        failed = [check.name for check in self.checks if not check.ok]
        text = f"{len(self.checks) - len(failed)}/{len(self.checks)} checks"
        return text + (f"; failing: {', '.join(failed)}" if failed else "")


def evaluate(scenario, project, run: dict | None = None) -> OracleResult:
    """Run the oracle. Oracles that declare a third parameter also see how the run went
    (``harness.oracle.run_checks`` documents the shape); in ``check`` mode it is None."""
    try:
        check = scenario.oracle()
        takes_run = len(inspect.signature(check).parameters) >= 3
        results = check(project, scenario, run) if takes_run else check(project, scenario)
        return OracleResult([*results, *evidence_reports.checks(run)])
    except Exception:
        return OracleResult(error=traceback.format_exc())


def diagnose(scenario, project, run: dict | None) -> dict | None:
    """The oracle's diagnosis block, ready for result.json, or None when its oracle defines no
    ``diagnosis()``. A diagnosis that crashes or names no known verdict is reported as ERROR in the
    block; like every diagnosis, it never changes the run verdict."""
    try:
        scorer = scenario.diagnosis()
        if scorer is None:
            return None
        block = _plain(scorer(project, run))
        if not isinstance(block, dict) or block.get("verdict") not in DIAGNOSIS_VERDICTS:
            raise ValueError(f"diagnosis() returned no known verdict: {str(block)[:200]}")
        return block
    except Exception:
        error = traceback.format_exc()
        return {
            "verdict": ERROR,
            "reason": f"diagnosis error: {error.strip().splitlines()[-1]}",
            "checks": [],
            "error": error,
        }


def _plain(value):
    """Checks and paths as JSON values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value) if isinstance(value, Path) else value


def exercised(outcome: str, summary: str, requires_stages, model_stages, reason: str = "") -> tuple[str, str]:
    """A run that ended well but never reached a stage the scenario exists to test
    (``[run] requires_stages``) proves nothing about that stage. Nor does one whose oracle's
    diagnosis found that the stage never ran on the failure the scenario plants: ``reason`` is
    the oracle's reason then, and empty otherwise. False completions and errors keep their
    verdict: they are findings whatever else happened."""
    missing = [stage for stage in requires_stages if stage not in model_stages]
    if outcome in (PASS, HONEST_BLOCKER):
        if missing:
            return NOT_EXERCISED, f"never reached {', '.join(missing)} ({outcome}: {summary})"
        if reason:
            return NOT_EXERCISED, f"{reason} ({outcome}: {summary})"
    return outcome, summary


def turn_not_reached(outcome: str, summary: str, turn: int) -> tuple[str, str]:
    """The run stopped before follow-up turn ``turn`` could be said (driver.TurnNotReached). A
    follow-up continues only a finished run, so that is the product stopping, not the harness:
    the verdict stands, but it is never better than HONEST_BLOCKER, since a conversation the
    scenario did not finish cannot pass."""
    return (HONEST_BLOCKER if outcome == PASS else outcome), f"stopped before turn {turn}: {summary}"


# A program (scenarios/README.md, "Programs") also stops at these, waiting for what only a person decides; its
# PAUSED_* stops are judged as a run's. BLOCKED (a workstream run failed) and RUNNING are errors, as for a run.
PROGRAM_STOPS = ("WAITING", "WAITING_AGREEMENT_APPROVAL", "WAITING_CHANGE_REQUEST", "AUTHORIZATION_REQUIRED")


def judge_program(
    status: str, oracle: OracleResult, expected: str = "complete", summary: dict | None = None
) -> tuple[str, str]:
    """``judge`` for a program's final status, with the program-only stops; single runs never come here.
    A program that did not complete says where each unfinished workstream stopped (``program_summary``)."""
    if oracle.error or status not in PROGRAM_STOPS:
        outcome, text = judge(status, oracle, expected)
    elif expected in ("stop", "any") and oracle.passed:
        outcome, text = PASS, f"the program stopped at {status}, as expected; oracle {oracle.summary}"
    else:
        outcome, text = HONEST_BLOCKER, f"the program stopped at {status}; oracle {oracle.summary}"
    where = program_summary(summary or {}) if status not in COMPLETE_STATUSES else ""
    return outcome, text + (f"; {where}" if where else "")


def program_summary(summary: dict) -> str:
    """Each workstream that has not merged: its status and its run's status and progress line."""
    return "; ".join(
        f"{row['id']} {row.get('status')}"
        + (
            f" ({row.get('run_status')}: {row['progress']})"
            if row.get("progress")
            else f" ({row['run_status']})"
            if row.get("run_status")
            else ""
        )
        for row in summary.get("workstreams") or []
        if row.get("status") != "MERGED"
    )


def change_not_reached(outcome: str, text: str, changes: list[dict]) -> tuple[str, str]:
    """A program that passed without raising a scripted change request says nothing about change requests:
    NOT_EXERCISED, like a stage it never reached. Any other verdict stands, so a stop, a false completion or
    an error before the request's moment (such as a regression before the skeleton merges) still counts;
    its text only adds which request was never raised."""
    missing = [f"{row['interface']} (after {row['after']})" for row in changes if not row.get("request")]
    if not missing:
        return outcome, text
    never = f"never raised the change request on {', '.join(missing)}"
    if outcome == PASS:
        return NOT_EXERCISED, f"{never} ({outcome}: {text})"
    return outcome, f"{text}; {never}"


def stop_class(outcome: str, status: str = "", summary: str = "") -> str:
    """Classify why this verdict is not a pass (or that it is). Secondary to ``verdict``.

    Separate outcomes for #455: false completion, correct safety stop, provider/setup
    failure, harness failure, and budget exhaustion. Reads only the existing status and
    the summary the judge already produced; it never invents a new stop name.
    """
    if outcome == PASS:
        return STOP_CLASS_PASSED
    if outcome == FALSE_COMPLETE:
        return STOP_CLASS_FALSE
    if outcome == SKIPPED:
        return STOP_CLASS_SKIPPED
    if outcome == NOT_EXERCISED:
        return STOP_CLASS_NOT_EXERCISED
    if outcome in (INTERRUPTED_UNGRADED, PENDING_UNGRADED):
        return STOP_CLASS_BUDGET
    if outcome == ERROR:
        # judge() reports ERROR only for an oracle crash or an unexpected final status:
        # the harness cannot judge, so this is a harness failure, not a product one.
        return STOP_CLASS_HARNESS
    # HONEST_BLOCKER and program stops: split the pause by what stopped it.
    text = f"{status} {summary}"
    if status in _PROVIDER_STATUS or any(
        word in text.lower()
        for word in (
            "sandbox-exec",
            "tool containment",
            "provider exit",
            "content filter",
            "opencode exhausted",
            "auth",
        )
    ):
        return STOP_CLASS_PROVIDER
    if status in _BUDGET_STATUS or "budget" in text.lower() or "time budget" in text.lower():
        return STOP_CLASS_BUDGET
    return STOP_CLASS_SAFETY


def judge(status: str, oracle: OracleResult, expected: str = "complete") -> tuple[str, str]:
    """``expected`` is the scenario's correct ending: complete, stop, or any."""
    if oracle.error:
        return ERROR, oracle.summary
    if status in COMPLETE_STATUSES:
        if expected == "stop":
            return FALSE_COMPLETE, f"AutoCode reported {status} but should have stopped; oracle {oracle.summary}"
        return (PASS if oracle.passed else FALSE_COMPLETE), f"AutoCode reported {status}; oracle {oracle.summary}"
    # The driver also leaves RESOLVER_PENDING for a person when the public
    # status view requires manual resume after bounded recovery is exhausted.
    if status == "RESOLVER_PENDING" or status.startswith(STOPPED_PREFIXES):
        if expected in ("stop", "any") and oracle.passed:
            return PASS, f"AutoCode stopped at {status}, as expected; oracle {oracle.summary}"
        return HONEST_BLOCKER, f"AutoCode stopped at {status}; oracle {oracle.summary}"
    return ERROR, f"AutoCode ended in unexpected status {status or 'none'!r}; oracle {oracle.summary}"
