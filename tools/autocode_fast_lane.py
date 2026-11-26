"""Fast lane: fewer model stages, the same evidence (owner request 2026-11-23, made the person's choice 2026-11-26).

The person picks the fast lane or the slow lane for a new task (the "Fast lane" run mode, autocode_run_modes).
``--fast-lane`` removes two calls and nothing else:

- **One combined review.** The run uses the combined review routing that already exists
  (autocode_review_routing, ``--review-routing combined``): the Completion Reviewer independently tests the
  Builder's work and judges completion in one read-only call. The policy is written into the plan the person
  approves, as it is for ``--review-routing combined``.
- **No Plan Reviewer.** The Planner's first draft goes straight to the person's exact-token approval, recorded as
  the plan's final planning evidence. AutoCode does not second-guess the choice by sizing the plan, judging the
  request's clarity or matching ``sensitive-paths`` (owner decision 2026-11-26: "avoid this plan qualifies
  thing"). Feedback on the shown plan goes to the Planner and its revision is reviewed as usual.

A draft is still reviewed only when approving it without a review is impossible, each read from the run, never
asked of a model (``decide``):

- it cannot go to the person as written (an open question, no executable first task: ``adaptive.approvable``);
- a record only the independent Plan Reviewer may seal is needed: an original-brief or API-risk observation
  (autocode_brief_obligations, autocode_risk_obligations), or a source trace looser than the strictest one
  (autocode_source_trace.strict_ready), which the later gates read;
- the run is progressive, has a design manifest, or does not plan on the default (v3) adaptive joint flow.

What does not change: the person approves the exact plan, the Builder builds it, and the regression proof, the
changed-code evidence (diff coverage, mutation check, per-line reasons), claim evidence and the completion gate
all still decide whether the run may finish. The fast lane removes model calls, never a gate.

Pure over the run state; imports lower-level policy modules only. The resolver's queue is passed in.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_brief_obligations as brief_obligations
    from . import autocode_goals as goals
    from . import autocode_progressive_state as progressive_state
    from . import autocode_review_routing as review_routing
    from . import autocode_risk_obligations as risk_obligations
    from . import autocode_roles as role_names
    from . import autocode_source_trace as source_trace
    from . import autocode_util as util
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_brief_obligations as brief_obligations
    import autocode_goals as goals
    import autocode_progressive_state as progressive_state
    import autocode_review_routing as review_routing
    import autocode_risk_obligations as risk_obligations
    import autocode_roles as role_names
    import autocode_source_trace as source_trace
    import autocode_util as util

FLAG = "--fast-lane"
SETTING = "fast_lane"
STAGE = "draft_plan"
APPROVED_AT = "draft_plan#fast_lane"  # planning.adaptive.approved_at for a draft the fast lane sent to the person
SKIPPED = ("review_plan", "revise_plan", "finalize_plan")
SKIP_TEXT = "fast lane: you chose the fast lane, so the plan went to you without a Plan Reviewer call"
REASONS = {
    "not_approvable": "the draft cannot go to you as written",
    "needs_reviewer_observations": "the brief or its risks need the independent Plan Reviewer's observations",
    "progressive": "a progressive plan needs its independent review",
    "design": "a design-backed plan needs its independent review",
}


def add_argument(parser) -> None:
    parser.add_argument(
        FLAG,
        action=argparse.BooleanOptionalAction,
        default=None,
        help="New runs: the fast lane. The first plan goes to you without a Plan Reviewer call, and one combined "
        "review replaces the separate Tester and Completion Reviewer; every evidence gate still applies. "
        "Disclosed in the plan you approve",
    )


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def configured(settings: dict, requested, *, started: bool, review_requested=None, checkpoints=False) -> bool:
    """Whether the run takes the fast lane. A saved run keeps its choice; a new run takes the flag.

    The fast lane needs the combined review, so it refuses an explicit ``--review-routing separate`` and
    enforced milestone checkpoints, which combined review cannot use.
    """
    saved = bool(settings.get(SETTING))
    if started:
        if requested is not None and bool(requested) != saved:
            raise ValueError(f"{FLAG} is a new-run choice; a saved run keeps its lane")
        return saved
    if not requested:
        return False
    if review_requested == "separate":
        raise ValueError(f"{FLAG} uses one combined review; it cannot run with --review-routing separate")
    if checkpoints:
        raise ValueError(f"{FLAG} uses one combined review, which cannot use enforced milestone checkpoints")
    return True


def new_run_settings(settings: dict, requested, *, review_requested=None, checkpoints=False) -> dict:
    """The settings a new run saves for the fast lane: the lane itself and the combined review it implies."""
    if not configured(settings, requested, started=False, review_requested=review_requested, checkpoints=checkpoints):
        return {}
    return {SETTING: True, review_routing.SETTING: review_routing.configured(settings, "combined", started=False)}


def _reviewer_needed(state: RunState, body: dict) -> bool:
    """Whether the draft holds anything only the independent Plan Reviewer may judge.

    An exact-output declaration in the brief (autocode_brief_obligations) or a declared API risk
    (autocode_risk_obligations) needs the reviewer's own observations. Criteria that cite the request need its
    source trace: the fast lane checks the strictest one instead (nothing out of scope, no negation dropped,
    autocode_source_trace.strict_ready), which only a reviewer may loosen, and a draft that fails it is reviewed.
    """
    if any(body.get(key) is not None for key in (brief_obligations.KEY, risk_obligations.KEY, source_trace.KEY)):
        return True
    try:
        if brief_obligations.inventory(state) or risk_obligations.inventory(state):
            return True
        if source_trace.any_citations(body):
            source_trace.strict_ready(state, body, brief_obligations.sources(state))
    except (ValueError, OSError):
        return True
    return False


def decide(state: RunState, body: dict) -> dict:
    """Whether the Planner's first draft goes to the person without a Plan Reviewer call, with the reason.

    The plan's size and the request's clarity are recorded but decide nothing, and ``sensitive-paths`` is not
    consulted: the person chose the lane. Only a draft that cannot be approved without a review is reviewed.
    """
    size = adaptive.plan_size(body)
    workflow = state.get("workflow") or {}
    settings = state.get("settings") or {}
    found = []
    if not adaptive.approvable(body):
        found.append("not_approvable")
    if progressive_state.enabled(state) or (state.get("planning") or {}).get("progressive"):
        found.append("progressive")
    if settings.get("design_manifest"):
        found.append("design")
    if not found and _reviewer_needed(state, body):
        found.append("needs_reviewer_observations")
    decision = {
        "skip_plan_review": not found,
        "reasons": found,
        "text": SKIP_TEXT if not found else "; ".join(REASONS[reason] for reason in found),
        "size": size["size"],
        "signals": size["signals"],
        "clarity": workflow.get("clarity"),
        "at": util.now(),
    }
    return decision


def after_draft(state: RunState, stage, record, *, queue) -> dict | None:
    """After the Planner's draft is installed: send a qualifying draft to the person, and record the decision.

    ``queue`` is autocode_resolver_human.queue. Returns the decision, or None when the fast lane does not apply
    (another stage, a run without the lane, a draft that waits on questions or is not headed for review).
    """
    if stage != STAGE or not enabled(state) or state.get("next_stage") != "review_plan":
        return None
    settings = state.get("settings") or {}
    if not settings.get("joint_planning") or not adaptive.enabled(state) or role_names.planning_flow(state) == "v2":
        return None
    planning = state.get("planning")
    contract = state.get("goal_contract")
    if not isinstance(planning, dict) or not isinstance(contract, dict) or planning.get("adaptive"):
        return None  # only the first draft of a planning cycle; a revision is reviewed as usual
    decision = decide(state, contract["body"])
    planning["fast_lane"] = decision
    if not decision["skip_plan_review"]:
        return decision
    planning["adaptive"] = {
        "size": decision["size"],
        "signals": copy.deepcopy(decision["signals"]),
        "approved_at": APPROVED_AT,
        "final_stage": STAGE,
        "challenges": 0,
        "review_limit": planning.get("review_call_limit"),
    }
    planning["final_token"] = goals.token(contract)
    output = record.get("output")
    evidence = {"hashes": {output: util.file_hash(Path(output))} if output and Path(output).is_file() else {}}
    proposed_by = {
        "stage": record.get("stage") or STAGE,
        **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record},
    }
    queue(
        state,
        "goal_approval",
        proposed_by,
        questions=[],
        evidence=evidence,
        status="AWAITING_GOAL_APPROVAL",
        phase="AWAITING_GOAL_APPROVAL",
        next_stage="plan_task",
    )
    return decision


def skipped(state: RunState) -> bool:
    """Whether this planning cycle's draft went to the person without a Plan Reviewer call."""
    return ((state.get("planning") or {}).get("adaptive") or {}).get("approved_at") == APPROVED_AT
