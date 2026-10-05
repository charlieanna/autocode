"""Turn an AutoCode run's final status and the oracle's checks into a verdict.

The oracle is the authority on whether the work is correct; AutoCode's status
only says whether AutoCode claimed completion. The worst outcome is a false
completion: AutoCode said done and the oracle disagrees.
"""
from __future__ import annotations

import inspect
import traceback
from dataclasses import dataclass, field

from .oracle import Check

PASS = "PASS"                        # AutoCode ended the way the scenario expects and the oracle agrees
FALSE_COMPLETE = "FALSE_COMPLETE"    # AutoCode completed but the oracle found failures, or it should have stopped
HONEST_BLOCKER = "HONEST_BLOCKER"    # AutoCode stopped and said why, without claiming completion
ERROR = "ERROR"                      # the run or the oracle broke; no judgement possible
SKIPPED = "SKIPPED"                  # a required tool or capability is missing
NOT_EXERCISED = "NOT_EXERCISED"      # the run never reached a stage the scenario exists to test

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


def exercised(outcome: str, summary: str, requires_stages, model_stages) -> tuple[str, str]:
    """A run that ended well but never reached a stage the scenario exists to test
    (``[run] requires_stages``) proves nothing about that stage. False completions
    and errors keep their verdict: they are findings whatever else happened."""
    missing = [stage for stage in requires_stages if stage not in model_stages]
    if missing and outcome in (PASS, HONEST_BLOCKER):
        return NOT_EXERCISED, f"never reached {', '.join(missing)} ({outcome}: {summary})"
    return outcome, summary


def turn_not_reached(outcome: str, summary: str, turn: int) -> tuple[str, str]:
    """The run stopped before follow-up turn ``turn`` could be said (driver.TurnNotReached). A
    follow-up continues only a finished run, so that is the product stopping, not the harness:
    the verdict stands, but it is never better than HONEST_BLOCKER, since a conversation the
    scenario did not finish cannot pass."""
    return (HONEST_BLOCKER if outcome == PASS else outcome), f"stopped before turn {turn}: {summary}"


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
