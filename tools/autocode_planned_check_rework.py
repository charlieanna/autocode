"""Send the Builder back when the approved plan's own check fails on the candidate (#2815 follow-up).

A Tester's or Completion Reviewer's PASS is refused when a check the approved plan names
(autocode_check_replay: a verification method or the task's validation plan step) fails the runner's
clean replay of the current source. A report repair cannot change that result, so before this module the
run paused as invalid output, and the Investigator asked the person whether the Builder or the Resolver
should take it. A live claude-tiers run (ladder-24, 2026-11-07) stopped there: the plan named seven tests
under their classes and the Builder had put them in new ones.

When the check names a test, module or function the run's output reports missing, the source does not
define what the approved plan names, and the approved plan is the contract: the runner returns the task to
the Builder with the failing check, its output and the rule to make it pass as written. Rewording the step
would change the approved plan, which is the person's to approve. Any other failure, such as a step that
expects a command to fail, keeps the existing route. It is bounded:

- only a refusal of a reviewer stage's report (``sol``, ``astra_checkpoint``) by a plain
  ``PlannedCheckFailure`` that names its command, whose output reports a name of that command missing
  (autocode_proof_seam.missing_names); an original-gate failure has its own route
  (autocode_superseded_correction);
- one send-back for each command at each source, and ``ROUNDS`` for each command in a task.

Anything else keeps the existing pause. State: ``planned_check_reworks``, written here and read here and
by autocode_resolver_recovery's admission (``owns_builder_round``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_proof_seam as proof_seam
    from . import autocode_run_status as run_status
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_proof_seam as proof_seam
    import autocode_run_status as run_status
    import autocode_util as util
    import autocode_verification_plan as verification_plan

KEY = "planned_check_reworks"
ROUNDS = 2
REVIEW_STAGES = ("sol", "astra_checkpoint")


def _task(state: RunState):
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    return {
        "task_id": task.get("id"),
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
    }


def send_back(state: RunState, record, error) -> bool:
    """Return the task to the Builder for a reviewer report refused by a failing approved check.

    False, leaving the state as it was, for any other refusal or once the bound is spent.
    """
    if type(error) is not verification_plan.PlannedCheckFailure:
        return False
    command, revision = getattr(error, "command", None), record.get("source_revision")
    stage = record.get("original_stage") or str(record.get("stage") or "").removesuffix("_report_repair")
    if stage not in REVIEW_STAGES or not isinstance(command, str) or not command or not revision:
        return False
    missing = proof_seam.missing_names(str(getattr(error, "tail", "") or "")) & proof_seam.words(command)
    if not missing:
        return False
    binding = _task(state)
    if not binding["task_id"]:
        return False
    earlier = [
        row
        for row in state.get("planned_check_reworks") or []
        if row.get("command") == command and all(row.get(key) == value for key, value in binding.items())
    ]
    if len(earlier) >= ROUNDS or any(row.get("source_revision") == revision for row in earlier):
        return False
    state.pop("pending_report_repair", None)  # the source, not a repaired report, settles this refusal
    state["iteration"] += 1
    state.setdefault("planned_check_reworks", []).append(
        {
            **binding,
            "command": command,
            "source_revision": revision,
            "iteration": state["iteration"],
            "output": record.get("output"),
            "at": util.now(),
        }
    )
    run_status.running(
        state,
        "EXECUTING",
        next_stage="terra",
        next_action=(
            f"The approved plan's check `{command}` must exit 0, and on the current source it cannot find "
            f"{', '.join(sorted(missing))}. Define each name exactly where the command names it (a test under "
            "the class and module the command gives), keep its assertions, and run the command from the "
            "repository root before you report. Do not change the plan's commands, criteria or scope.\n"
            f"Runner replay: {error}"
        ),
    )
    return True


def owns_builder_round(state: RunState, revision) -> bool:
    """True when the next Builder round is the one ``send_back`` queued for this source and iteration.

    A recovery packet the current task came from does not own that round, and ``ROUNDS`` bounds it.
    """
    rows = state.get("planned_check_reworks") or []
    last = rows[-1] if rows else {}
    return (
        isinstance(last, dict)
        and bool(revision)
        and last.get("source_revision") == revision
        and last.get("iteration") == state.get("iteration")
        and all(last.get(key) == value for key, value in _task(state).items())
    )
