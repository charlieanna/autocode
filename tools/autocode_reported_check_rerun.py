"""Run a reviewer stage again when a check it cited does not reproduce (a real run, 2026-11-14).

A Tester's or checkpoint reviewer's report is refused when a check it reported as passing fails the runner's
clean replay from the repository root (autocode_check_replay). The refusal used to go to a report repair. A
repair has no tools: it cannot run another command, so all it can do is withdraw the check and mark what it
supported NOT_VERIFIED. That blocked report sent the task to the Builder, which had nothing to build, and the
run stopped for the person (``PAUSED_BUILDER_CLASSIFICATION``) with working code and a passing regression
proof. The check in that run was ``cd ../web && npm test --silent``: it had passed only from the directory
the Tester's previous command left it in.

The stage that can settle such a refusal is the one that ran the check. It has its tools, so it can run the
check from the repository root, or one that sets up what it needs, and cite that. The runner starts the same
stage again on the same source, with the refused command, how its replay ended and the rule. It is bounded:

- only a refusal of the stage's own report (``tester``, ``review_checkpoint``), never of a report repair,
  by a plain ``ReportedCheckFailure`` that names its command;
- one run for each source of a task, and ``ROUNDS`` for a task.

Anything else, and a second refusal at the same source, keeps the existing route: a report repair, then the
existing pause. State: ``reported_check_reruns``, written and read only here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_run_status as run_status
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_run_status as run_status
    import autocode_util as util
    import autocode_verification_plan as verification_plan

KEY = "reported_check_reruns"
ROUNDS = 2
REVIEW_STAGES = ("tester", "review_checkpoint")


def _task(state: RunState):
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    return {
        "task_id": task.get("id"),
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
    }


def run_again(state: RunState, record, error) -> bool:
    """Start the reviewer stage again for a report refused by a cited check that does not reproduce.

    True when it took the refusal; the stage that reported is then next again, with the reason as its action.
    False, leaving the state as it was, for any other refusal or once the bound is spent.
    """
    if type(error) is not verification_plan.ReportedCheckFailure:
        return False
    command, revision, stage = getattr(error, "command", None), record.get("source_revision"), record.get("stage")
    if (
        record.get("report_only")
        or stage not in REVIEW_STAGES
        or state.get("next_stage") != stage
        or state.get("pending_report_repair")
        or not isinstance(command, str)
        or not command
        or not revision
    ):
        return False
    binding = _task(state)
    if not binding["task_id"]:
        return False
    earlier = [
        row
        for row in state.get("reported_check_reruns") or []
        if all(row.get(key) == value for key, value in binding.items())
    ]
    if len(earlier) >= ROUNDS or any(row.get("source_revision") == revision for row in earlier):
        return False
    state.setdefault("reported_check_reruns", []).append(
        {
            **binding,
            "stage": stage,
            "command": command,
            "source_revision": revision,
            "iteration": state.get("iteration"),
            "output": record.get("output"),
            "at": util.now(),
        }
    )
    run_status.running(
        state,
        "EXECUTING",
        next_stage=stage,
        next_action=(
            f"Check this task again on the same source. The last report cited `{command}` as passing, and it "
            "does not pass when the runner runs it from the repository root in a clean copy of the current "
            "source. Run every check you cite from the repository root, as one command that enters any "
            "directory it needs itself (`cd packages/web && npm test`), and that sets up anything else it "
            "needs. If the behavior cannot be shown that way, report what you observed as a finding or as not "
            "verified. Never cite a command whose result depends on an earlier command of yours.\n"
            f"Runner replay: {error}"
        ),
    )
    return True
