"""A larger brief's first Requirements pass asks its blocking questions early (#2940).

When triage is enabled on a fresh joint-planning run on the default flow, the first
``requirements_gather`` dispatch is a *triage pass*: a small prompt (the task and a
workspace inventory, no criteria contract) at the lowest reasoning level the requirements model serves, whose
only outputs are the genuinely blocking questions — or ``ready`` when nothing blocks.
Today the first question a person sees waits on the one full-weight Requirements call
(291 s of the 360 s first-interaction target, docs/bugs/2026-10-10-first-interaction-latency.md),
while the questions-only pause and the re-run with the answers already exist
(autocode_requirements_review). This module decides when the triage pass is due, renders
its prompt and schema, picks its effort level and shapes its answer into the
questions-only handoff that machinery already accepts. With questions, the pause and the
answer-driven re-run are exactly the existing flow; with ``ready``, the next dispatch is
the ordinary full pass. The pass is recorded in ``requirements_triage`` so later
Requirements turns never triage again.

A provider that answers the triage prompt with a full handoff — every scripted fake
does, and a live model told a smaller contract may still answer fully — changes nothing:
the report carries no ``ready`` field, so acceptance falls through to the ordinary path
and the run proceeds exactly as before. Saved runs keep their saved setting; spec-first,
planning-v2, follow-up and mid-conversation turns never triage. Pure functions over run
state; imports only lower layers. New small briefs use the ordinary Requirements
pass directly, at the saved level: a questions-only probe would add a model call
before the same validated handoff. Explicit choices and saved flows stay exact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import copy
import json

try:
    from . import autocode_prompts as prompts
    from . import autocode_roles as role_names
    from . import autocode_route_fit as route_fit
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_prompts as prompts
    import autocode_roles as role_names
    import autocode_route_fit as route_fit
    import autocode_util as util
    import autocode_workflows as workflows

SETTING = "requirements_triage"
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
        help="First Requirements pass asks its blocking questions from a small prompt at the lowest "
        "level before the full report (#2940). Defaults to larger briefs in new joint default-flow runs; "
        "small briefs run the ordinary Requirements pass directly",
    )


def new_run_setting(requested, joint: bool, v2: bool, *, task: str | None = None) -> bool:
    """Default larger briefs to triage; retain explicit choices and the ordinary handoff gates."""
    if requested is True and (not joint or v2):
        raise ValueError("--requirements-triage needs joint planning and the default planning flow")
    if requested is not None:
        return requested is True
    return joint and not v2 and (task is None or workflows.brief_size(task) != "small")


def resume_refused(saved_settings: dict, requested) -> bool:
    """A saved run keeps its flow; repeating its own choice is harmless, no flag changes nothing."""
    return requested is not None and bool(requested) != bool(saved_settings.get(SETTING))


def due(state: RunState) -> bool:
    """Whether the next requirements_gather dispatch is the triage pass of a fresh run.

    The first dispatch only: once any Requirements report exists, any answer, feedback or
    saved user event has arrived, or the triage pass itself has run (``requirements_triage``),
    every later dispatch is the ordinary full pass.
    """
    settings = state.get("settings") or {}
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


def request_schema(full: dict) -> dict:
    """The triage dispatch's request schema: the ordinary requirements schema with everything but
    the two summary lines optional and one added ``ready`` field.

    A provider that follows the small prompt returns only the triage fields; one that answers with
    a full report (every scripted fake does) still validates, because the full report's fields are
    all known and optional here. :func:`is_report` routes on ``ready``'s presence either way, so
    neither answer is refused at the provider boundary.
    """
    relaxed = copy.deepcopy(full)
    relaxed.setdefault("properties", {})["ready"] = {"type": "boolean"}
    relaxed["required"] = ["summary", "intended_outcome"]
    return relaxed


def answer_schema(full: dict) -> dict:
    """:func:`request_schema` at load time: the same relaxed shape, so what the provider boundary
    was promised is not re-rejected by the strict wire schema a transport recorded."""
    return request_schema(full)


def tolerant(full: dict) -> dict:
    """The ordinary full schema plus an optional ``ready``: a full report whose optional ``ready``
    was materialized (by provider machinery or a repair) still validates while the dispatch is
    triage-eligible; every ordinary requirement of the full schema stays in force."""
    relaxed = copy.deepcopy(full)
    relaxed.setdefault("properties", {})["ready"] = {"type": "boolean"}
    return relaxed


def prompt(state: RunState, inventory, engine) -> str:
    """The triage prompt: instruction, the shared packet framing, a small packet.

    ``engine`` is the packet's ``execution_engine`` (planning.engine_for at the call site):
    provider transports assert on it exactly as they do on the joint packet's.
    """
    packet = {
        "task": state["task"],
        "workspace": state["workspace"],
        "joint_planning": True,
        "execution_engine": engine,
        "stage": STAGE,
        # The joint packet's requirements-gather fields, None-valued the same way: scripted
        # providers read these keys on every prompt whatever the pass (fake_codex, fake_command_tool).
        "goal_contract": None,
        "requirements_handoff": None,
        "requirements_history": None,
        "saved_answers": state.get("answers") or {},
        "workspace_inventory": inventory,
    }
    return (
        prompts.get("requirements-triage.md")
        + prompts.get("fragments/autoplanner/context-03.md")
        + json.dumps(packet, separators=(",", ":"))
    )


def is_answer(value: dict) -> bool:
    """A triage answer carries the ready field and drafts nothing: no requirements, behaviors or tests.

    Presence alone is not the signal: provider machinery materializes an optional field with its
    empty value (a repaired report gained ``ready: false`` beside a full draft, 2026-11-08), so a
    report that drafted anything routes through the ordinary path whatever it carries.
    """
    return "ready" in value and not any(
        value.get(key) for key in ("requirements", "required_behaviors", "acceptance_tests")
    )


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
