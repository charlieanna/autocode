"""A plan-only run (`--plan-only`, the "Plan only" run mode): plan and review, then end at the accepted plan.

Requirements, the Planner and the Plan Reviewer run as in every run. The reviewed plan is shown with the same
approval, bound to the same displayed token, but named for what it does here (``ACCEPT_LABEL``): taking it
ends the run with that plan as its result (``accept``). The Builder, the Tester and the Completion Reviewer
never run, and nothing in the project is changed.

Chosen when a new run starts and saved as ``settings.plan_only``; a saved run keeps its choice. A follow-up on
the accepted plan is planned and reviewed again under the same choice. `autocode plan --export` writes the
accepted plan as a file to review or share.

The run ends in this job (autocode_jobs.ended_in): ``render`` is its completion summary, and the completion
checks of a built run (changed-code evidence, independent validation) do not apply to it, since it built
nothing (``owns``). Imports only lower-level modules: never the runner, Autopilot or a module importing them.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_run_status as run_status

SETTING = "plan_only"
FLAG = "--plan-only"
ACCEPT_LABEL = "Accept plan"
ACCEPT_EFFECT = "Ends this chat with the reviewed plan as its result. Nothing is built or changed."
ACCEPTED_HEADLINE = "Plan accepted"
# The stages a plan-only run never reaches (autocode_stage_decisions shows them skipped once it ends).
NOT_BUILT = ("builder", "tester", "review_completion")


def add_argument(parser) -> None:
    parser.add_argument(
        FLAG,
        action="store_true",
        default=None,
        help="New run: plan and review only. Requirements, the Planner and the Plan Reviewer run; approving "
        "the reviewed plan accepts it and ends the run, and nothing is built. Needs joint planning",
    )


def new_run_setting(args, joint: bool) -> bool:
    """Off unless asked for; refused where no Plan Reviewer checks the plan or the run approves it itself."""
    if not getattr(args, "plan_only", None):
        return False
    if not joint:
        raise ValueError(f"{FLAG} needs joint planning: the Plan Reviewer checks the plan before you accept it")
    if getattr(args, "skip_to_build", False):
        raise ValueError(f"{FLAG} cannot be combined with --skip-to-build: a plan-only run never builds")
    return True


def resume_refused(saved_settings: dict, args) -> bool:
    """A saved run keeps its choice; repeating it is harmless."""
    requested = getattr(args, "plan_only", None)
    return requested is not None and bool(requested) != bool(saved_settings.get(SETTING))


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def describe_approval(state: RunState, need: dict) -> dict:
    """The plan approval of a plan-only run, named for what it does there; any other need unchanged."""
    if need.get("kind") != "approve_plan" or not enabled(state):
        return need
    return {**need, "plan_only": True, "label": ACCEPT_LABEL, "effect": ACCEPT_EFFECT}


def accept(state: RunState) -> None:
    """End a plan-only run whose plan was just approved: the plan is the result, and no stage runs next."""
    if not enabled(state) or cast(dict, state.get("goal_contract") or {}).get("approval_status") != "approved":
        return
    run_status.complete(state, completed_at=dt.datetime.now(dt.UTC).isoformat())


def owns(state: RunState) -> bool:
    """Whether this run ended at its accepted plan (autocode_jobs.ended_in)."""
    return (
        enabled(state)
        and state.get("status") == "TASK_COMPLETE"
        and cast(dict, state.get("goal_contract") or {}).get("approval_status") == "approved"
    )


def render(state: RunState) -> str:
    """The completion summary: the accepted plan's goal and milestones, and how to build it later."""
    contract = cast(dict, state.get("goal_contract") or {})
    body = cast(dict, contract.get("body") or {})
    lines = ["PLAN ACCEPTED — nothing was built or changed", ""]
    if outcome := str(body.get("intended_outcome") or "").strip():
        lines += [outcome, ""]
    milestones = [row for row in body.get("milestones") or [] if isinstance(row, dict)]
    if milestones:
        lines.append("Milestones:")
        lines += [f"  - {row.get('id')}: {row.get('objective')}" for row in milestones]
        lines.append("")
    criteria = [row for row in body.get("acceptance_criteria") or [] if isinstance(row, dict)]
    if criteria:
        lines.append(f"Acceptance criteria: {len(criteria)}")
        lines.append("")
    lines.append("`autocode plan --export PLAN.md` saves it as a file to review or share.")
    return "\n".join(lines) + "\n"
