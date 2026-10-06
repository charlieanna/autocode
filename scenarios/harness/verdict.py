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

PASS = "PASS"                        # AutoCode ended the way the scenario expects and the oracle agrees
FALSE_COMPLETE = "FALSE_COMPLETE"    # AutoCode completed but the oracle found failures, or it should have stopped
HONEST_BLOCKER = "HONEST_BLOCKER"    # AutoCode stopped and said why, without claiming completion
ERROR = "ERROR"                      # the run or the oracle broke; no judgement possible
SKIPPED = "SKIPPED"                  # a required tool or capability is missing
NOT_EXERCISED = "NOT_EXERCISED"      # the run never reached a stage the scenario exists to test

# An oracle may also define diagnosis(project, run): how a stage judged the failure the scenario plants
# (issue #59), scored apart from the run verdict above. Its verdicts, besides NOT_EXERCISED (the planted
# failure never reached the stage) and ERROR (diagnosis() itself crashed):
CORRECT = "CORRECT"                  # the stage ran on the planted failure and every required check passed
INCORRECT = "INCORRECT"              # it ran on the planted failure and a required check failed
UNSCORED = "UNSCORED"                # it launched on the planted failure but saved no output to score
DIAGNOSIS_VERDICTS = (CORRECT, INCORRECT, UNSCORED, NOT_EXERCISED)

COMPLETE_STATUSES = ("TASK_COMPLETE", "COMPLETE")
STOPPED_PREFIXES = ("PAUSED_", "BLOCKED_HUMAN", "AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER")


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
        return OracleResult(check(project, scenario, run) if takes_run else check(project, scenario))
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
        return {"verdict": ERROR, "reason": f"diagnosis error: {error.strip().splitlines()[-1]}", "checks": [],
                "error": error}


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


def judge_program(status: str, oracle: OracleResult, expected: str = "complete",
                  summary: dict | None = None) -> tuple[str, str]:
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
    return "; ".join(f"{row['id']} {row.get('status')}"
                     + (f" ({row.get('run_status')}: {row['progress']})" if row.get("progress") else
                        f" ({row['run_status']})" if row.get("run_status") else "")
                     for row in summary.get("workstreams") or [] if row.get("status") != "MERGED")


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
