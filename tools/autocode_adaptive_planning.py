"""Adaptive planning: planning depth set by evidence, not a fixed stage sequence.

On by default for new runs that use joint planning on the default flow (since
2026-10-02, after two live comparisons); ``--no-adaptive-planning`` opts out, and
``--adaptive-planning`` insists on it. Saved as ``settings["adaptive_planning"]``; a
saved run keeps its planning flow. Without it the build pipeline is the fixed one:
requirements, draft, challenge, revise, finalize. With it, each decision is made by
the first stage that has the evidence:

- Clarity, from the request text, at recognition. A build request the recognizer
  calls ``clear`` skips the Requirements stage; the Planner takes the requirements
  from the request itself. A follow-up with a saved requirements handoff refreshes
  that handoff first, so the new request is not bound to the previous request's IDs.
  Clarity is a property of the text, so the recognizer, which reads no files, can judge it.
- Convergence, from the Plan Reviewer. The Planner's draft carries its initial_task,
  so a first review with no blocking concern approves an ordinary draft as written and it
  goes to the user. Progressive delegations retain revision and final independent review.
  Blocking concerns get a revision, then another review while the
  allowance leaves room for a final one.
- Size, from the draft itself, computed here rather than asked of a model: its
  milestones and the files they touch. A large plan gets one more review call than
  the default allowance.
- Feedback on a shown plan, from the run's own status. Feedback sent while a complete
  plan waits for approval goes to the Planner, which revises that plan, instead of
  restarting from Requirements. The runner checks the revision covers the feedback
  (it becomes a traced requirement), and the Planner can send feedback that changes
  what is being built back to the Requirements stage.

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
    revises_plan, on a brief_feedback event: {"requirements_handoff": the output path of the
        requirements handoff the feedback was given against, or ""}, saved by
        autocode_goals.feedback from feedback_marker. Read by feedback_requirements here, which
        autocode_goals.check_requirement_trace and units.autoplanner.trace_rows use.
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

# Feedback on a shown plan. The rows are requirement_trace_rows whose requirement_id is a feedback event ID.
FEEDBACK_RULE = """
FEEDBACK ON THE PLAN THE USER SAW. The user was shown goal_contract for approval and asked for a change instead:
the requirement_trace_rows whose requirement_id is a feedback event ID quote what they said. No Requirements stage
ran for it. Revise goal_contract to apply that feedback: start from it, change only what the feedback needs, and
keep every other requirement, criterion and milestone as it is. Record each protected item you change or remove
in contract_changes with basis user_feedback and that feedback ID as answer_id. Trace each feedback row like any
requirement: covered by the acceptance criterion or required_behaviors entry that now delivers it. Where the feedback
contradicts part of the shown plan, change that item and record it in contract_changes; leave conflict_resolutions
empty, since they settle only conflicts a Requirements report recorded. Ask a blocking question only for a choice the
feedback leaves open.
"""

RERUN_RULE = """
If the feedback changes what is being built, so that the requirements must be gathered again (a different
product, user or outcome, not an added or changed behavior), do not revise: set requirements_rerun to one sentence
saying why. The runner then discards this draft and runs the Requirements stage. Otherwise leave it empty.
"""

FEEDBACK_TRACE_RULE = """
FEEDBACK ROWS. requirement_trace_rows whose requirement_id is a feedback event ID are the user's feedback on a plan
they were shown. Trace them like any requirement: covered by an acceptance criterion or required_behaviors entry.
"""

FEEDBACK_REVIEW_RULE = """
FEEDBACK REVISION. This draft revises a plan the user was shown, to apply their feedback: the
requirement_trace_rows whose requirement_id is a feedback event ID. Check that each is applied as the user said
it, without reading more into it, and that nothing they asked for earlier was lost. A feedback row the plan does
not deliver is a blocking concern.
"""
FEEDBACK = "revises_plan"


def enabled(state: dict) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def new_run_setting(requested, joint: bool, v2: bool) -> bool:
    """Whether a new run plans adaptively. ``requested`` is the flag: None (not given) means whenever the run can,
    which is joint planning on the default flow; False opts out; True insists, and is refused where it cannot
    apply."""
    if requested is True and (not joint or v2):
        raise ValueError("--adaptive-planning needs joint planning and the default planning flow")
    return requested is not False and joint and not v2


def resume_refused(saved_settings: dict, requested) -> bool:
    """Whether the adaptive-planning flag on a resumed run would change it. A saved run keeps its planning flow:
    --adaptive-planning on one that lacks it, or --no-adaptive-planning on an adaptive one, is refused; repeating
    the run's own choice is harmless (drivers pass the same launch flags on every resume), and no flag changes
    nothing."""
    if requested is None:
        return False
    return bool(requested) != bool(saved_settings.get(SETTING))


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
    """Skip Requirements for clarity only when no prior-turn handoff needs refreshing."""
    refresh_follow_up = bool(state.get("turns") and state.get("requirements_handoff"))
    if (enabled(state) and value.get("workflow") == "build" and value.get("clarity") == "clear"
            and then == "requirements_gather" and not refresh_follow_up):
        return planner_stage
    return then


def report_schema(state: dict, stage: str, base: dict, planning_body: dict) -> dict:
    """A Planner report schema whose contract carries initial_task in adaptive runs. A draft that revises
    the shown plan from feedback may also send it back to Requirements (requirements_rerun, optional)."""
    if not enabled(state) or stage not in ("astra_discovery", "glm_revise"):
        return base
    schema = copy.deepcopy(base)
    schema["properties"]["contract"] = copy.deepcopy(planning_body)
    if stage == "astra_discovery" and can_rerun(state):
        schema["properties"]["requirements_rerun"] = {"type": "string"}
    return schema


def prompt_rule(state: dict, stage: str) -> str:
    if not enabled(state):
        return ""
    feedback = bool(feedback_requirements(state))
    if stage == "astra_discovery":
        return (PLANNER_RULE + ("" if state.get("requirements_handoff") else NO_REQUIREMENTS_RULE)
                + (FEEDBACK_RULE if feedback else "") + (RERUN_RULE if can_rerun(state) else ""))
    if stage == "glm_revise":
        return PLANNER_RULE + (FEEDBACK_TRACE_RULE if feedback else "")
    if stage == "astra_challenge":
        return REVIEW_RULE + (FEEDBACK_REVIEW_RULE if feedback else "")
    if stage == "astra_finalize":
        return FEEDBACK_TRACE_RULE if feedback else ""
    return ""


def _handoff(state: dict) -> str:
    """The current requirements handoff, by its report's output path; "" when Requirements has not run."""
    return str((state.get("requirements_handoff") or {}).get("output") or "")


def feedback_marker(state: dict) -> dict:
    """What to save on a brief_feedback event given now: {FEEDBACK: ...} when it revises the plan the user was
    shown (an adaptive run, on the default joint-planning flow, waiting for approval of a complete plan), else {}.
    Feedback while questions are open, or queued during execution, keeps the full pipeline."""
    settings = state.get("settings") or {}
    if (enabled(state) and settings.get("joint_planning") and settings.get("planning_flow") != "v2"
            and state.get("status") == "AWAITING_GOAL_APPROVAL" and (state.get("goal_contract") or {}).get("body")):
        return {FEEDBACK: {"requirements_handoff": _handoff(state)}}
    return {}


def feedback_stage(event: dict, default: str) -> str:
    """The first stage after a brief_feedback event: the Planner when the feedback revises the shown plan."""
    return "astra_discovery" if event.get(FEEDBACK) else default


def feedback_requirements(state: dict) -> list[dict]:
    """Feedback the Planner must trace as requirements, as requirements-handoff rows quoting the user: each
    feedback that revised a shown plan and that no Requirements report has read since (a later one takes it in).

    Only feedback on this turn's plans: feedback given before the newest follow-up revised an earlier job's
    plan. A handoff that follow-up archived (autocode_follow_up.plan_afresh) would otherwise make feedback
    marked with no handoff, which a Requirements report has since read, match again."""
    current = _handoff(state)
    turns = [turn for turn in state.get("turns") or [] if isinstance(turn, dict)]
    since = str(turns[-1].get("at") or "") if turns else ""
    return [{"id": event["id"], "text": event.get("text", ""), "source_quote": event.get("text", "")}
            for event in state.get("brief_feedback") or []
            if isinstance(event, dict) and event.get("id") and isinstance(event.get(FEEDBACK), dict)
            and event[FEEDBACK].get("requirements_handoff") == current and str(event.get("at") or "") >= since]


def can_rerun(state: dict) -> bool:
    """Whether a Planner draft may send the feedback it revises from back to Requirements: only while there is
    such feedback and the run has a Requirements stage to send it to."""
    return bool(feedback_requirements(state)) and "requirements" in ((state.get("settings") or {}).get("roles") or {})


def requirements_rerun(state: dict, value: dict) -> str:
    """The Planner's reason to gather requirements again instead of revising the shown plan; "" to revise."""
    reason = str(value.get("requirements_rerun") or "").strip() if enabled(state) else ""
    return reason if reason and can_rerun(state) else ""


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
