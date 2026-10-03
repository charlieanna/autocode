"""Adaptive planning: planning depth set by evidence, not a fixed stage sequence.

Opt-in per run (``--adaptive-planning``, saved as ``settings["adaptive_planning"]``).
Without it the build pipeline is unchanged: requirements, draft, challenge, revise,
finalize. With it, each decision is made by the first stage that has the evidence:

- Clarity, from the request text, at recognition. A build request the recognizer
  calls ``clear`` skips the Requirements stage; the Planner takes the requirements
  from the request itself. Clarity is a property of the text, so the recognizer,
  which reads no files, can judge it.
- Convergence, from the Plan Reviewer. The Planner's draft carries its initial_task,
  so a first review with no blocking concern approves an ordinary draft as written and it
  goes to the user. Progressive delegations retain revision and final independent review.
  Blocking concerns get a revision, then another review while the
  allowance leaves room for a final one.
- Size, from the draft itself, computed here rather than asked of a model: its
  milestones and the files they touch. A large plan gets one more review call than
  the default allowance.

Nothing is skipped that a later gate would not cover: the user still approves every
build plan, and the Validator and Completion Owner still judge the work.

This module is pure. It imports nothing from the runner; callers pass state in.

State keys written by callers from these decisions:
    workflow.clarity: "clear" | "vague", saved by autocode_workflows.apply. Evidence of why
        Requirements was skipped; nothing in tools/ branches on it after recognition.
        scenarios/harness/plan_compare.py reads it from state.json.
    planning.adaptive: {"size", "signals", "review_limit", "challenges", "approved_at",
        "final_stage"}, saved by units.autoplanner.after_challenge. Read by
        units.autoplanner.after_revise (challenges), autocode_resolver_human._decision
        (final_stage: which review is the final planning evidence), review_notes here (for
        autocode_goal_lifecycle.render), and plan_compare.summarize via state.json. The status
        view (autocode_run_view) does not show either key yet.
"""
from __future__ import annotations

import copy

SETTING = "adaptive_planning"
CLARITY = ("clear", "vague")
# The default review allowance is two calls (challenge and finalize). A large plan
# gets one more, so it can be revised twice before the final decision.
LARGE_PLAN_REVIEW_LIMIT = 3
# With an unlimited allowance (0), stop re-reviewing after this many challenges.
MAX_CHALLENGES = 3

RECOGNIZER_RULE = """
Also judge how clear the request is, from its text alone. "clarity" is "clear" when the request says what
to build or change and how to tell it is done, and leaves no product choice open (who it is for, what it
must and must not do, which of several behaviors). It is "vague" when it names a goal but leaves scope,
behavior or acceptance to be worked out ("make onboarding better", "add analytics"). Length is not
clarity: a long request can still leave the key choice open. When unsure, answer "vague". For any kind
other than build, answer "clear". Add "clarity" to the JSON you return.
"""

PLANNER_RULE = """
ADAPTIVE PLANNING. Include initial_task in your contract now, with the same fields the final plan uses:
objective, affected_paths, kind (implement or validate), milestone_id, requirements, acceptance_criteria IDs,
validation_plan; its milestone has depends_on []. While a blocking question remains, use kind=none with empty
strings and lists. If the Plan Reviewer raises no blocking concern, this contract goes to the user for approval
exactly as you wrote it, with no further planning round, so make it complete. A progressive_proposal
delegating future slices still needs revision and final independent review before user approval.
"""

NO_REQUIREMENTS_RULE = """
No Requirements stage ran: the request was judged clear enough to plan from directly. Take the requirements
from the task text and saved user events yourself, keep the user's literal outcome, and ask a blocking question
only for a choice the request genuinely leaves open.
"""

REVIEW_RULE = """
ADAPTIVE PLANNING. Mark a concern blocking only when the plan must change before anyone builds it: a missing or
weakened requirement, a wrong dependency or ownership claim, an untestable criterion, an unsafe or incoherent
initial_task. If none of your concerns is blocking, your review approves the plan as drafted: it goes to the user
with your non-blocking concerns as notes, and there is no revise or final round. Progressive delegations retain
revision and final independent review even without blocking concerns. Do not raise a blocking concern
only to get another round. If planning.reports already holds a glm_revise report, this is a re-review of the
revised plan: judge whether the Planner's responses settled your earlier concerns, and raise only what remains
or what the revision introduced.
"""


def enabled(state: dict) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def resume_refused(saved_settings: dict, requested: bool) -> bool:
    """Whether --adaptive-planning on a resumed run would change it. A saved run keeps its planning flow, so
    turning the flag on for one that lacks it is refused; repeating it on an adaptive run is harmless (drivers
    pass the same launch flags on every resume, as with --joint-planning)."""
    return bool(requested) and not saved_settings.get(SETTING)


def recognizer_rule(state: dict) -> str:
    return RECOGNIZER_RULE if enabled(state) else ""


def recognizer_schema(state: dict, base: dict) -> dict:
    """The recognizer's report schema; adaptive runs also report clarity."""
    if not enabled(state):
        return base
    schema = copy.deepcopy(base)
    schema["properties"]["clarity"] = {"type": "string", "enum": list(CLARITY)}
    return schema


def entry_stage(state: dict, value: dict, then: str, planner_stage: str) -> str:
    """The build pipeline's first stage after recognition: the Planner for a clear request."""
    if (enabled(state) and value.get("workflow") == "build" and value.get("clarity") == "clear"
            and then == "requirements_gather"):
        return planner_stage
    return then


def report_schema(state: dict, stage: str, base: dict, planning_body: dict) -> dict:
    """A Planner report schema whose contract carries initial_task in adaptive runs."""
    if not enabled(state) or stage not in ("astra_discovery", "glm_revise"):
        return base
    schema = copy.deepcopy(base)
    schema["properties"]["contract"] = copy.deepcopy(planning_body)
    return schema


def prompt_rule(state: dict, stage: str) -> str:
    if not enabled(state):
        return ""
    if stage == "astra_discovery":
        return PLANNER_RULE + ("" if state.get("requirements_handoff") else NO_REQUIREMENTS_RULE)
    if stage == "glm_revise":
        return PLANNER_RULE
    if stage == "astra_challenge":
        return REVIEW_RULE
    return ""


def plan_size(body: dict) -> dict:
    """Size a drafted contract from the scope it declares (milestones and the files they touch), not from a
    model's estimate. Acceptance criteria are reported but do not count: they measure how thoroughly a plan is
    tested, not how much it changes, and counting them made 6 of 11 live plans "large", 3 of them one-milestone
    changes (2026-09-30)."""
    milestones = body.get("milestones") or []
    paths = {path for row in milestones for path in row.get("affected_paths") or []}
    paths.update((body.get("initial_task") or {}).get("affected_paths") or [])
    signals = {"milestones": len(milestones), "affected_paths": len(paths),
               "acceptance_criteria": len(body.get("acceptance_criteria") or []),
               "dependency_edges": sum(len(row.get("depends_on") or []) for row in milestones)}
    large = signals["milestones"] >= 3 or signals["affected_paths"] >= 10
    small = signals["milestones"] <= 1 and signals["affected_paths"] <= 4
    return {"size": "large" if large else "small" if small else "medium", "signals": signals}


def blocking(concerns: list) -> list:
    return [concern for concern in concerns if concern.get("blocking")]


def approvable(body: dict) -> bool:
    """Whether a draft is complete enough to go to the user without a final review."""
    task = body.get("initial_task") or {}
    return not body.get("open_blocking_questions") and task.get("kind") in ("implement", "validate")


def review_limit(size: str, current: int) -> int:
    """The allowance for a plan of this size; never lowers the current one, and 0 stays unlimited."""
    if current == 0:
        return 0
    return max(current, LARGE_PLAN_REVIEW_LIMIT) if size == "large" else current


def review_notes(planning: dict) -> list[str]:
    """Display lines for a plan the Plan Reviewer approved early: its non-blocking concerns, as notes."""
    if not (planning.get("adaptive") or {}).get("approved_at"):
        return []
    concerns = ((planning.get("reports") or {}).get("astra_challenge") or {}).get("report", {}).get("concerns") or []
    lines = []
    for concern in concerns:
        lines += [f"  Reviewer note [{concern['id']}]: {concern['concern']}",
                  "    Suggested: " + concern["requested_change"]]
    return lines


def after_revise(limit: int, calls_used: int, challenges: int) -> str:
    """After a revision: review it again while that leaves a call for the final decision."""
    remaining = None if limit == 0 else limit - calls_used
    if (remaining is None or remaining >= 2) and challenges < MAX_CHALLENGES:
        return "astra_challenge"
    return "astra_finalize"
