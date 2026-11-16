"""Compatibility and saved-run replay support for the retired Requirements triage pass (#2940, #3652).

New runs and resumed live runs use the unified citation-verified ``requirements_gather``
contract from pass 1 across both interactive ``--spec-first`` and headless ``--no-chat``
flows. ``--requirements-triage`` and ``--no-requirements-triage`` remain accepted as
compatibility no-ops so existing scripts and saved-run resumes do not fail, while
saved runs and portable recordings that captured an earlier triage pass still load,
render and replay cleanly. Pure functions over run state; imports only lower layers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import copy

try:
    from . import autocode_requirements_report as requirements_report
    from . import autocode_roles as role_names
    from . import autocode_route_fit as route_fit
    from . import autocode_util as util
except ImportError:
    import autocode_requirements_report as requirements_report
    import autocode_roles as role_names
    import autocode_route_fit as route_fit
    import autocode_util as util

SETTING: Final = "requirements_triage"
# requirements_review.SETTING by value: that module registers this flag beside its own, so it
# imports this one and this one must not import it back.
SPEC_FIRST = "spec_first"
STAGE = "requirements_gather"
REASON = "requirements_triage"


def add_argument(parser) -> None:
    parser.add_argument(
        "--requirements-triage",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Retired (#3652); accepted as a compatibility no-op so existing scripts and resumes "
        "do not fail. Every new run uses the unified citation-verified Requirements pass directly",
    )


def new_run_setting(requested, joint: bool, v2: bool, *, task: str | None = None) -> bool:
    """Retired (#3652): new runs always use the unified citation-verified Requirements pass."""
    _ = requested, joint, v2, task
    return False


def resume_refused(saved_settings: dict, requested) -> bool:
    """Retired (#3652): passing either compatibility flag on resume is a harmless no-op."""
    _ = saved_settings, requested
    return False


def due(state: RunState) -> bool:
    """Whether the next requirements_gather dispatch replays a recorded legacy triage pass (#3652).

    Live and scripted runs never dispatch triage. Only offline replay of a saved recording
    whose first Requirements pass was recorded under ``requirements_triage`` takes this path.
    """
    settings = state.get("settings") or {}
    if not settings.get("replay_recording"):
        return False
    if not settings.get(SETTING) or not settings.get("joint_planning"):
        return False
    if role_names.planning_flow(settings) == "v2" or settings.get(SPEC_FIRST):
        return False
    return not any(
        state.get(key)
        for key in (
            SETTING,
            "requirements_handoff",
            "requirements_history",
            "answers",
            "user_events",
            "goal_contract",
            "brief_feedback",
        )
    )


def answer_schema(full: dict) -> dict:
    """Relaxed load-time schema for replaying a recorded legacy triage answer."""
    relaxed = copy.deepcopy(full)
    relaxed.setdefault("properties", {})["ready"] = {"type": "boolean"}
    relaxed["required"] = ["summary", "intended_outcome"]
    return relaxed


def tolerant(full: dict) -> dict:
    """The ordinary full schema plus an optional ``ready`` for replaying a legacy triage pass."""
    relaxed = copy.deepcopy(full)
    relaxed.setdefault("properties", {})["ready"] = {"type": "boolean"}
    return relaxed


def validate_application(state: RunState, stage, value, checked=None, *, schema_for, validate):
    """Apply the report contract while preserving assumption-text backfill and replay tolerance."""
    if stage == STAGE and isinstance(value, dict):
        backfilled = requirements_report.backfill_assumption_text(value)
        if backfilled is not value:
            value["proposed_assumptions"] = backfilled["proposed_assumptions"]
    if checked is None:
        checked = value
        if stage == STAGE and isinstance(value, dict) and isinstance(value.get("proposed_assumptions"), list):
            checked = {
                **value,
                "proposed_assumptions": [row for row in value["proposed_assumptions"] if not isinstance(row, str)],
            }
    elif stage == STAGE and isinstance(checked, dict):
        checked = requirements_report.backfill_assumption_text(checked)
    triaging = stage == STAGE and due(state)
    if triaging and is_answer(value):
        return value
    schema = schema_for(state, stage)
    validate(checked, tolerant(schema) if triaging else schema)
    return value


def is_answer(value: dict) -> bool:
    """Whether ``value`` is a triage answer rather than an ordinary full handoff.

    Presence alone is not the signal: provider machinery materializes an optional field with its
    empty value (a repaired report gained ``ready: false`` beside a full draft, 2026-11-08).
    Strict wire schemas (``model_output_schema``) mark every property required, so a live model
    answering triage with ``ready: true`` or blocking ``open_questions`` may also populate draft
    arrays without having received ``requirement_coverage_checklist``; route those as triage, while
    keeping ``ready: false`` with no questions and non-empty draft fields on the ordinary path.
    """
    if "ready" not in value:
        return False
    if value.get("ready") is True or questions(value):
        return True
    return not any(value.get(key) for key in ("requirements", "required_behaviors", "acceptance_tests"))


def questions(value: dict) -> list:
    return value.get("open_questions") or []


def handoff(value: dict, not_yet_drafted=()) -> dict:
    """Shape a questions-bearing triage answer into the questions-only handoff _accept already takes.

    ``not_yet_drafted`` is the brief's requirement-like sentence list (goals.scan_texts /
    goals.cue_sentences at the call site): the coverage checker holds the *full* pass to quoting
    every brief sentence, so the triage pass marks them explicitly instead of leaving them silent.
    """
    return {
        "summary": str(value.get("summary") or ""),
        "intended_outcome": str(value.get("intended_outcome") or ""),
        "requirements": [],
        "required_behaviors": [],
        "constraints": [],
        "acceptance_tests": [],
        "source_refs": [],
        "proposed_assumptions": [],
        "open_questions": questions(value),
        "ignored_statements": list(not_yet_drafted),
        "conflicts": [],
    }


def mark(state: RunState, outcome: str, *, count: int = 0) -> None:
    """Record the triage pass in the run state, so every later Requirements turn is the full pass."""
    state[SETTING] = {"pass": 1, "outcome": outcome, "questions": count, "at": util.now()}


def effort_decision(state: RunState, stage: str, configured) -> dict | None:
    """The triage dispatch runs at the lowest level the requirements model serves, with why.

    ``None`` when this launch is not the triage pass, the level is already the floor or off
    the scale. Mirrors autocode_review_effort's decision shape, so the launch records it the
    same way as ``effort_choice``.
    """
    if stage != STAGE or not due(state) or not configured or configured not in route_fit.SCALE:
        return None
    settings = state.get("settings") or {}
    model = ((settings.get("roles") or {}).get("requirements") or {}).get("model")
    offered = _offered(settings, model)
    if offered is None:
        return _kept(configured, "levels_unknown", f"the levels {model or 'this model'} offers are not known")
    floor = offered[0]
    if floor == configured:
        return None
    return {
        "level": floor,
        "ceiling": configured,
        "lowered": True,
        "reason": REASON,
        "text": f"the first Requirements pass only asks its blocking questions, so it runs at {floor}",
    }


def _kept(configured, reason: str, text: str) -> dict:
    return {"level": configured, "ceiling": configured, "lowered": False, "reason": reason, "text": text}


def _offered(settings: dict, model) -> list | None:
    """The levels the run knows ``model`` serves, lowest first; None when it knows none (review_effort)."""
    if not isinstance(model, str):
        return None
    listed = route_fit.declared_for(settings, model)
    certified = route_fit.certified_for(settings, model)
    if listed is None:
        return None
    return sorted(set(listed) | set(certified or ()), key=route_fit.SCALE.index)
