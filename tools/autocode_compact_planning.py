"""Legacy saved-run compact planning for a reproduced, localized bug fix (#2021, the first #1867 checkpoint).

An earlier runtime's bug-fix runs could plan on a compact path: after the Investigator (``investigate_bug``)
reproduced the bug, the runner wrote the draft itself from that diagnosis and the Requirements record, and the
Plan Reviewer reviewed it. AutoCode no longer runs the Investigator, so no run starts that path any more: every
task plans with the Planner. What remains serves a run saved while its compact draft was under review: a clean
review goes to the person's exact-token approval through the adaptive early approval
(units.autoplanner.after_challenge), a blocking concern sends the run to the Planner's draft with the concern in
its packet (fall_back, fallback_context, fallback_rule). The draft is never approved by a policy:
autocode_workflows.POLICY_ORIGINS does not know its origin. The recorded decision is in
``state["workflow"]["stage_policy"]`` (autocode_stage_policy), shown by the status view as ``stage_policy``.

The flag stays so that a saved run's recorded setting is still checked on resume (resume_refused); a new run
cannot enable it (new_run_setting).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_prompts as prompts
    from . import autocode_run_status as run_status
    from . import autocode_stage_policy as stage_policy
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_prompts as prompts
    import autocode_run_status as run_status
    import autocode_stage_policy as stage_policy
    import autocode_workflows as workflows

FLAG = "--compact-bugfix-planning"
SETTING = "compact_bugfix_planning"
ORIGIN = "bugfix_compact_draft"
FALLBACK_RULE = prompts.get("fragments/autoplanner/compact-fallback-rule.md")


def add_argument(parser) -> None:
    parser.add_argument(
        FLAG,
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Retired for new runs: bug fixes use Requirements, Planner and Plan Reviewer. "
        "A saved run keeps its recorded planning policy",
    )


def enabled(state: RunState) -> bool:
    settings = state.get("settings") or {}
    return (
        bool(settings.get(SETTING)) and settings.get(workflows.IMPLEMENTATION_FLOW) != workflows.STANDARD_IMPLEMENTATION
    )


def new_run_setting(requested, joint: bool) -> bool:
    """New runs always retain the Planner; saved runs use their recorded policy."""
    if requested:
        raise ValueError(f"{FLAG} is retired for new runs; bug fixes use the standard planning sequence")
    return False


def resume_refused(saved_settings: dict, requested) -> bool:
    """Whether the flag on a resumed run would change it: the choice is made when the run starts."""
    if requested is None:
        return False
    return bool(requested) != bool(saved_settings.get(SETTING))


def reviewing(state: RunState) -> bool:
    """Whether the draft under review is the runner's compact draft."""
    return (state.get("goal_contract") or {}).get("origin") == ORIGIN


def fallback_context(state: RunState) -> dict | None:
    """What the Planner's packet carries after a refused compact draft: the reason and the blocking concern IDs."""
    entry = stage_policy.current(state)
    if not entry or not entry.get("fallback") or not reviewing(state):
        return None
    concerns = ((state.get("planning") or {}).get("reports") or {}).get("review_plan", {}).get("report", {})
    return {
        "reason": entry["fallback"]["reason"],
        "blocking_concerns": [c["id"] for c in adaptive.blocking(concerns.get("concerns") or [])],
    }


def fallback_rule(state: RunState) -> str:
    """The Planner's instruction after a refused compact draft (prompt fragment), or ""."""
    return FALLBACK_RULE if fallback_context(state) else ""


def fall_back(state: RunState, reason: str) -> None:
    """The Plan Reviewer could not approve the compact draft: continue on the full path, at the Planner's
    draft, with the review on record for it (autocode_stage_policy.fall_back). Never a second compact draft."""
    # The compact draft's review was the runner's extra call: the full path keeps the review allowance it would
    # have had without it (a live fallback otherwise left the final review no call: PAUSED_PLANNING_BUDGET).
    planning = state.get("planning")
    refunded = False
    if (
        isinstance(planning, dict)
        and type(planning.get("plan_review_calls")) is int
        and planning["plan_review_calls"] > 0
    ):
        planning["plan_review_calls"] -= 1
        refunded = True
    stage_policy.fall_back(state, stage="review_plan", reason=reason, review_call_refunded=refunded)
    run_status.running(state, "PLANNING", next_stage=workflows.planner_stage(state))
