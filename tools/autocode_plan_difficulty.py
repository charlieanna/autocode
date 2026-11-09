"""A hard plan gets more reasoning, on the same model (owner decision 2026-11-09).

The person's stage choices name the model and level each stage runs at. When the runner can show that
a plan is hard, the Planner and the Plan Reviewer run one level above that level, on the same model:
never another model, never past the strongest level the model offers. ``decide`` returns the decision
and its reason either way, and the launch records it on the stage record as ``effort_choice``, beside
the small-task rule that lowers a review (autocode_review_effort), so the person sees both.

Difficulty is read from facts the runner holds, never from a model's own rating of the task:

- ``large_plan``: the draft has three or more milestones or touches ten or more files, computed from the
  draft itself (autocode_adaptive_planning.plan_size).
- ``sensitive_paths``: the draft touches a file the run's ``sensitive-paths`` names
  (autocode_sensitive_paths); a run without that list raises nothing on this signal.
- ``blocked_again``: the Plan Reviewer has blocked the draft at least twice in this planning cycle.
- ``vague_request``: Requirements judged the request vague. This is a model's judgment, so it may only
  raise the level, as every model signal here may; nothing in this module lowers one.

Which stage reads which: the Planner's first draft (``astra_discovery``) has no draft yet, so only a
vague request raises it. A revision (``glm_revise``) and the Plan Reviewer's challenge and final review
(``astra_challenge``, ``astra_finalize``) read the draft's size and paths; a revision also reads a second
block. The level is raised only when it is the person's saved choice or the runner's default for the
role; a level typed as a flag or given as an answer is exact, a route a climb moved or a fallback route
on another model is left alone, and so is a model whose levels the run does not know.

Pure functions over the run state; imports only the plan-size rule, the path matcher, the route-fit
scale and the saved-route origins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_role_route as role_route
    from . import autocode_route_fit as route_fit
    from . import autocode_sensitive_paths as sensitive_paths
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_role_route as role_route
    import autocode_route_fit as route_fit
    import autocode_sensitive_paths as sensitive_paths

# The planning stages this rule may raise, by the route that launches them.
STAGES = {
    "astra_discovery": "glm",
    "glm_revise": "glm",
    "astra_challenge": "plan_reviewer",
    "astra_finalize": "plan_reviewer",
}
# Which difficulty signals each stage reads (see the module docstring).
SIGNALS = {
    "astra_discovery": ("vague_request",),
    "glm_revise": ("large_plan", "sensitive_paths", "blocked_again", "vague_request"),
    "astra_challenge": ("large_plan", "sensitive_paths", "vague_request"),
    "astra_finalize": ("large_plan", "sensitive_paths", "vague_request"),
}
# Only a level the person saved as a stage choice, or the runner's own default, is raised.
RAISABLE = (role_route.STAGE_CHOICE, role_route.RUNNER_DEFAULT, role_route.AUTO_SELECTED)
HARD_PLAN = "hard_plan"


def signals(state: RunState, stage) -> list[str]:
    """The difficulty signals that hold for ``stage`` now, in a fixed order."""
    wanted = SIGNALS.get(stage, ())
    workflow = state.get("workflow") or {}
    body = ((state.get("goal_contract") or {}).get("body") or {}) if stage != "astra_discovery" else {}
    found = []
    if "large_plan" in wanted and body and adaptive.plan_size(body)["size"] == "large":
        found.append("large_plan")
    if "sensitive_paths" in wanted and body and sensitive(state, body):
        found.append("sensitive_paths")
    if "blocked_again" in wanted and blocks(state) >= 2:
        found.append("blocked_again")
    if "vague_request" in wanted and workflow.get("clarity") == "vague":
        found.append("vague_request")
    return found


def sensitive(state: RunState, body: dict) -> list[str]:
    """The draft's files that the run's saved ``sensitive-paths`` names; none without a saved list."""
    patterns = (state.get("settings") or {}).get(sensitive_paths.SETTING)
    if not isinstance(patterns, list) or not patterns:
        return []
    paths = list((body.get("initial_task") or {}).get("affected_paths") or [])
    for row in body.get("milestones") or []:
        if isinstance(row, dict):
            paths.extend(row.get("affected_paths") or [])
    return sensitive_paths.matched(paths, patterns)


def blocks(state: RunState) -> int:
    """How many Plan Reviewer challenges in this planning cycle blocked the draft."""
    adaptive_record = (state.get("planning") or {}).get("adaptive") or {}
    challenges = adaptive_record.get("challenges")
    approved = adaptive_record.get("approved_at")
    if not isinstance(challenges, int) or approved:
        return 0
    # Every challenge that did not approve the draft sent it back with a blocking concern.
    return challenges


def above(model_levels, configured) -> str | None:
    """The next level above ``configured`` that the model offers; None at its strongest or off the scale."""
    if configured not in route_fit.SCALE:
        return None
    rank = route_fit.SCALE.index(configured)
    return next((level for level in route_fit.SCALE[rank + 1 :] if level in model_levels), None)


def decide(state: RunState, stage, route_role, report_only, configured, *, model=None) -> dict | None:
    """The level this planning launch runs at, with why; None when the rule does not apply.

    ``configured`` is the level the launch would pass and ``model`` the model it launches. A report
    repair, a stage outside planning, a route that is not the stage's own, a level a person typed and a
    launch on another model than the saved route's are none of this rule's business.
    """
    if report_only or STAGES.get(stage) != route_role or not configured:
        return None
    settings = state.get("settings") or {}
    if (settings.get("effort_origins") or {}).get(route_role) not in RAISABLE:
        return None
    route = (settings.get("roles") or {}).get(route_role) or {}
    if route.get("reasoning_effort") != configured or (model and route.get("model") != model):
        return None
    if any(isinstance(row, dict) and row.get("role") == route_role for row in state.get("reasoning_escalations") or ()):
        return None
    found = signals(state, stage)
    if not found:
        return None
    saved_model = route.get("model")
    facts = {"signals": found, "saved": configured}
    offered = _offered(settings, saved_model)
    if offered is None:
        return _kept(configured, "levels_unknown", f"the levels {saved_model} offers are not known", facts)
    higher = above(offered, configured)
    if higher is None:
        return _kept(configured, "strongest_level", f"{configured} is the strongest level {saved_model} offers", facts)
    return {
        "level": higher,
        "ceiling": higher,
        "lowered": False,
        "raised": True,
        "reason": HARD_PLAN,
        "text": "the plan is hard (" + ", ".join(TEXT[signal] for signal in found) + ")",
        **facts,
    }


TEXT = {
    "large_plan": "a large plan",
    "sensitive_paths": "it touches sensitive files",
    "blocked_again": "the Plan Reviewer blocked it twice",
    "vague_request": "Requirements found the request vague",
}


def note(decision) -> str | None:
    """One sentence for the live log when a launch runs above the person's level; None otherwise."""
    if not isinstance(decision, dict) or not decision.get("raised"):
        return None
    level, saved, text = decision.get("level"), decision.get("saved"), decision.get("text")
    if not all(isinstance(value, str) for value in (level, saved, text)):
        return None
    return f"Running at {level}, one level above your {saved}: {text}."


def _kept(configured, reason, text, facts) -> dict:
    return {
        "level": configured,
        "ceiling": configured,
        "lowered": False,
        "raised": False,
        "reason": reason,
        "text": text,
        **facts,
    }


def _offered(settings, model) -> list | None:
    """The levels the run knows ``model`` serves: the tool's listing and every level a probe passed."""
    if not isinstance(model, str):
        return None
    listed = route_fit.declared_for(settings, model)
    if listed is None:
        return None
    return sorted(set(listed) | set(route_fit.certified_for(settings, model)), key=route_fit.SCALE.index)
