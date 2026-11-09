"""A small, clear task's reviews run one level below the person's saved level (owner decision 2026-11-05).

The person's stage choices (``autocode stages``, autocode_stage_choices) name a model and a reasoning
level for each stage. For the three review stages that level is a ceiling rather than a fixed value:
on a small, clear task the Plan Reviewer, the Tester and the Completion Reviewer run one level below it.
In the 2026-11-05 slugify run the Plan Reviewer spent 612 of the run's 1,680 s in one pass at high,
almost all of it reasoning, on a two-line fix whose plan it then approved as drafted. The Builder, the
Planner, Requirements and every other stage run at the saved level.

``decide`` applies the rule in this order, and returns the decision with its reason either way:

- Only a level taken from the stage choices is a ceiling (``settings.effort_origins[role]`` is
  ``stage_choice``, autocode_role_route). A level typed as a flag or given as an answer is exact, and
  the runner's own default keeps its existing tiers (the Plan Reviewer's reduced depth, #1564).
- A route a climb moved, or a fallback route on another model, is not the saved ceiling any more.
- ``--plan-review-full`` keeps the Plan Reviewer at full depth, as it does for the review allowance.
- A stage that already ran for this task (a rejected answer, a send-back, another look) runs at the
  ceiling, so a cheap first pass never costs a second one at the same low level.
- The task is small and clear when Requirements judged it clear and the drafted plan is small (one
  milestone touching at most four files, autocode_adaptive_planning.plan_size); before a draft is
  sized, the brief's own size decides (autocode_workflows.brief_size).
- The level one below the ceiling must be one the model offers, by the run's saved listing or a probe
  that passed. A model without it (GLM 5.3 offers low, high and max) keeps the ceiling rather than drop
  two levels, and so does a model whose levels the run does not know.

The launch records the decision on the stage record as ``effort_choice``; the usage view carries it to
``/details`` and the live log says when a stage runs below the ceiling. Pure functions over the run
state; imports only the route-fit scale, the saved-route facts and the Builder's light tier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_light_builder as light_builder
    from . import autocode_repair_effort as repair_effort
    from . import autocode_requirements_triage as requirements_triage
    from . import autocode_role_route as role_route
    from . import autocode_route_fit as route_fit
except ImportError:
    import autocode_light_builder as light_builder
    import autocode_repair_effort as repair_effort
    import autocode_requirements_triage as requirements_triage
    import autocode_role_route as role_route
    import autocode_route_fit as route_fit

# The review stages whose saved level is a ceiling, by the route that launches them.
STAGES = {
    "astra_challenge": "plan_reviewer",
    "astra_finalize": "plan_reviewer",
    "sol": "sol",
    "astra_review": "completion",
}
PLANNING = ("astra_challenge", "astra_finalize")
SMALL_CLEAR = "small_clear"
KEPT = ("not_small_clear", "earlier_attempt", "no_lower_level", "levels_unknown", "climbed", "full_review")


def small_clear(state: RunState) -> bool:
    """Whether Requirements judged the request clear and the plan (or, before one is sized, the brief) is small."""
    workflow = state.get("workflow") or {}
    if workflow.get("clarity") != "clear":
        return False
    plan = ((state.get("planning") or {}).get("adaptive") or {}).get("size")
    if plan in ("small", "medium", "large"):
        return plan == "small"
    return workflow.get("brief_size") == "small"


def below(ceiling) -> str | None:
    """The level one step below ``ceiling`` on AutoCode's scale; None at the bottom or off the scale."""
    if ceiling not in route_fit.SCALE:
        return None
    index = route_fit.SCALE.index(ceiling)
    return route_fit.SCALE[index - 1] if index else None


def decide(state: RunState, stage, route_role, report_only, configured, *, model=None) -> dict | None:
    """The level this launch runs at under the saved ceiling, with why; None when the rule does not apply.

    ``configured`` is the level the launch would pass and ``model`` the model it launches. A report
    repair, a stage outside the reviews, a role whose level is not a stage choice and a launch on
    another model than the saved route's are none of this rule's business.
    """
    if report_only or STAGES.get(stage) != route_role or not configured:
        return None
    settings = state.get("settings") or {}
    if (settings.get("effort_origins") or {}).get(route_role) != role_route.STAGE_CHOICE:
        return None
    route = (settings.get("roles") or {}).get(route_role) or {}
    if route.get("reasoning_effort") != configured or (model and route.get("model") != model):
        return None
    saved_model = route.get("model")
    facts = _facts(state)
    lower = below(configured)
    if any(row.get("role") == route_role for row in _rows(state.get("reasoning_escalations"))):
        return _kept(configured, "climbed", "a climb raised this stage's level", facts)
    if route_role == "plan_reviewer" and settings.get("plan_review_full"):
        return _kept(configured, "full_review", "this run asked for full plan review", facts)
    if _earlier(state, stage):
        return _kept(configured, "earlier_attempt", "this stage already ran for this task", facts)
    if not small_clear(state):
        return _kept(configured, "not_small_clear", "the task is not both small and clear", facts)
    offered = _offered(settings, saved_model)
    if lower is None or offered is not None and lower not in offered:
        return _kept(configured, "no_lower_level", f"{saved_model} offers no level just below {configured}", facts)
    if offered is None:
        return _kept(configured, "levels_unknown", f"the levels {saved_model} offers are not known", facts)
    return {
        "level": lower,
        "ceiling": configured,
        "lowered": True,
        "reason": SMALL_CLEAR,
        "text": f"the task is small and clear, so this review runs one level below your {configured}",
        **facts,
    }


def launch(state: RunState, stage, route_role, report_only, route: dict) -> tuple:
    """``(level, decision)`` for a stage launch, before the boundary fit (autocode_route_fit.launch).

    A small task's first Builder attempt runs low (autocode_light_builder, #1710); a review runs under
    its stage-choice ceiling (``decide``); a format repair runs below the configured level
    (autocode_repair_effort, #2868); the first Requirements pass asks its blocking questions from a
    small prompt at the floor level (autocode_requirements_triage, #2940). ``decision`` is None when
    no rule considered the launch.
    """
    configured = light_builder.effort(state, stage, report_only, route.get("reasoning_effort"))
    if report_only:
        decision = repair_effort.decide(state, configured, model=route.get("model"))
    else:
        decision = decide(state, stage, route_role, report_only, configured, model=route.get("model"))
    if triage_decision := requirements_triage.effort_decision(state, stage, configured):
        return triage_decision["level"], triage_decision
    return effort(decision, configured), decision


def effort(decision: dict | None, configured):
    """The level to pass: the decision's, or ``configured`` when the rule did not apply."""
    return decision["level"] if decision else configured


def note(decision) -> str | None:
    """One sentence for the live log when a launch runs below the person's level; None otherwise."""
    if not isinstance(decision, dict) or not decision.get("lowered"):
        return None
    level, ceiling = decision.get("level"), decision.get("ceiling")
    if not isinstance(level, str) or not isinstance(ceiling, str):
        return None
    if decision.get("reason") == SMALL_CLEAR:
        return f"Running at {level}, one level below your {ceiling}: the task is small and clear."
    text = decision.get("text")
    return f"Running at {level}, below your {ceiling}: {text}." if isinstance(text, str) else None


def _kept(configured, reason, text, facts) -> dict:
    return {"level": configured, "ceiling": configured, "lowered": False, "reason": reason, "text": text, **facts}


def _facts(state: RunState) -> dict:
    workflow = state.get("workflow") or {}
    return {
        "clarity": workflow.get("clarity"),
        "brief_size": workflow.get("brief_size"),
        "plan_size": ((state.get("planning") or {}).get("adaptive") or {}).get("size"),
    }


def _offered(settings, model) -> list | None:
    """The levels the run knows ``model`` serves: the tool's listing and every level a probe passed."""
    if not isinstance(model, str):
        return None
    listed = route_fit.declared_for(settings, model)
    certified = route_fit.certified_for(settings, model)
    if listed is None:
        return None
    return sorted(set(listed) | set(certified), key=route_fit.SCALE.index)


def _earlier(state: RunState, stage) -> bool:
    """Whether ``stage`` already launched for this task: a plan review in this iteration, a Tester or
    Completion Reviewer for the current build task (this iteration while there is none)."""
    task = None if stage in PLANNING else (state.get("current_task") or {}).get("id")
    iteration = state.get("iteration")
    for row in _rows(state.get("stages")):
        if stage not in (row.get("stage"), row.get("original_stage")):
            continue
        if task and row.get("task_id") == task:
            return True
        if not task and row.get("iteration") == iteration:
            return True
    return False


def _rows(values) -> list[dict]:
    return [row for row in values or () if isinstance(row, dict)]
