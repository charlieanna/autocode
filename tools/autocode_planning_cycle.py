"""A run's planning cycle: begin one, archive the one before it, and decide whether a Requirements handoff
renews it.

The cycle records ``turn_event_id`` from the human follow-up receipt; the small-brief
depth policy uses it to scope archived default charges, without resetting usage.
The cycle is the ``planning`` record: its Plan Reviewer charges, reports and final token. Pure steps over
the run state. The planning unit (units/autoplanner) re-exports them, so ``autocode_planning.start`` and the
others are the same functions as before. The goal lifecycle (autocode_goal_lifecycle) begins a cycle from
here when a drafted contract goes to plan review: it used to import the planning unit for that, and the
planning unit imports the goal lifecycle, which closed an import cycle.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as role_names
except ImportError:
    import autocode_roles as role_names


def planning_inputs(state: RunState):
    """What may renew a v2 planning cycle, as sorted IDs: the user's answers and brief feedback (corrections,
    queued interventions and follow-ups). Model output adds none of them."""
    feedback = [row for row in state.get("brief_feedback") or [] if isinstance(row, dict) and row.get("id")]
    return sorted(
        ["answer:" + str(key) for key in state.get("answers") or {}]
        + ["feedback:" + str(row["id"]) for row in feedback]
    )


def internal_answer_restart(state: RunState):
    """Planning restarts because AutoCode settled a reviewer's technical question itself, not the person.

    Under the small-brief total (#1564) the next cycle would carry a spent allowance into a plan that still
    needs its review; ``new_cycle`` renews it once per run for this cause. Any other cycle that already holds
    the Plan Reviewer's first review carries that review and its charges into the next one (inherits_review)."""
    planning = state.get("planning")
    if isinstance(planning, dict):
        planning["internal_answer_restart"] = True


# What an internal-answer restart carries from the archived cycle: the reviewer's first review and the Planner's
# answer to it, the allowance they were charged against and the depth decisions made for this plan.
INHERITED_REPORTS = ("astra_challenge", "glm_revise")
INHERITED_KEYS = (
    "review_call_limit",
    "review_call_limit_origin",
    "adaptive",
    "internal_answer_renewals",
    "revision_review_renewals",
    "revision_review_renewal",
)


def inherits_review(archived) -> bool:
    """Whether the cycle after ``archived`` continues its review instead of starting one.

    AutoCode settled a technical question with the Planner's proposed default (autocode_technical_questions.settle)
    after the Plan Reviewer had reviewed the draft. A live vague-brief run (2026-10-20) then redrafted under a
    fresh allowance and the reviewer, given nothing of its first review, reviewed the new draft cold and raised
    four new blocking concerns; the run reached its time cap with no plan. Model output never renews the
    allowance: the redraft is the Planner's answer to the saved review. The small-brief total (#1564) keeps its
    own one-time renewal, and the v2 flow has no legacy first review to carry."""
    return bool(
        archived
        and archived.get("internal_answer_restart")
        and not archived.get("review_depth_reduced")
        and ((archived.get("reports") or {}).get("astra_challenge") or {}).get("report")
    )


def review_limit(state: RunState, planning) -> int:
    """This cycle's review allowance, 0 for unlimited (units.autoplanner.review_call_limit validates it)."""
    limit = planning.get("review_call_limit", (state.get("settings") or {}).get("planning_review_call_limit", 2))
    return limit if type(limit) is int and limit >= 0 else 2


def remaining_reviews(state: RunState, planning):
    """Review calls left in this cycle; None when the allowance is unlimited."""
    limit = review_limit(state, planning)
    return None if limit == 0 else max(0, limit - planning.get("astra_calls", 0))


def _inherit_review(state: RunState, archived):
    planning = state["planning"]
    planning["reports"] = {
        stage: copy.deepcopy(archived["reports"][stage]) for stage in INHERITED_REPORTS if stage in archived["reports"]
    }
    planning["astra_calls"] = archived.get("astra_calls", 0)
    if archived.get("review_charges"):
        planning["review_charges"] = list(archived["review_charges"])
    for key in INHERITED_KEYS:
        if key in archived:
            planning[key] = copy.deepcopy(archived[key])
    planning["inherited_review"] = {
        "cycle": len(state.get("planning_history") or []) - 1,
        "astra_calls": planning["astra_calls"],
        "reports": sorted(planning["reports"]),
    }
    explicit = planning.get("review_call_limit_origin") == "user_explicit" or bool(
        (state.get("settings") or {}).get("planning_review_call_limit_origin")
    )
    if remaining_reviews(state, planning) == 0 and not planning.get("internal_answer_renewals") and not explicit:
        # The final review asked the question, so the allowance is spent: one more call, once per run, so the
        # redraft gets its final review instead of a budget pause nobody can answer. A limit the person chose
        # (a flag or config.toml) is never raised here.
        limit = review_limit(state, planning)
        planning.update(
            review_call_limit=limit + 1, review_call_limit_origin="runner_default", internal_answer_renewals=1
        )
        planning["inherited_review"]["renewed"] = {"from": limit, "to": limit + 1}


def new_cycle(state: RunState, *, new_turn=False):
    """Archive the planning record, with its reports and review charges, and begin a cycle under the configured
    review allowance. A v2 cycle records the inputs it began after (planning_inputs, read by admit_requirements).
    After an internal-answer restart the cycle inherits the archived review instead (inherits_review)."""
    archived = state.get("planning")
    if archived:
        state.setdefault("planning_history", []).append(copy.deepcopy(archived))
    state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
    if inherits_review(archived):
        _inherit_review(state, archived)
    # Scope default depth-policy history to this human turn, not the whole chat.
    # Internal replans keep the same event; model output cannot create one.
    turn = (state.get("turns") or [{}])[-1]
    if turn.get("event_id"):
        state["planning"]["turn_event_id"] = turn["event_id"]
    # Only a bound the person set during this run is explicit. The settings' origin names where a default
    # allowance came from (config.toml, #1565); that is the size of each cycle's allowance, not a total.
    explicit = bool(archived and archived.get("review_call_limit_origin") == "user_explicit")
    if archived and archived.get("review_depth_reduced") and (not new_turn or explicit):
        # A small clear brief pays one total allowance across cycles (#1564): the
        # revision restarts the record, not the counter.
        state["planning"]["review_depth_reduced"] = True
        state["planning"]["review_calls_before_policy"] = archived.get("review_calls_before_policy", 0)
        state["planning"]["astra_calls"] = archived.get("astra_calls", 0)
        renewals = archived.get("internal_answer_renewals", 0)
        state["planning"]["internal_answer_renewals"] = renewals
        if archived.get("revision_review_renewals"):  # once per run (#1815), as the internal-answer renewal
            state["planning"]["revision_review_renewals"] = archived["revision_review_renewals"]
        if archived.get("internal_answer_restart") and not renewals:
            # AutoCode settled the reviewer's technical question itself and planning restarted, so the
            # redrafted plan needs a review the carried total had already spent: a live run dead-ended at
            # 2/2 with nobody to ask. One fresh allowance per run; a second internal restart still stops.
            state["planning"].update(astra_calls=0, internal_answer_renewals=1)
        if archived.get("review_call_limit_origin") in ("user_explicit", "runner_default"):
            # Carry the saved bound with the same-turn charges it limits. A
            # runner-default renewal is no new allowance on this restart; a new
            # human turn still gets its configured default rather than this one.
            state["planning"].update(
                review_call_limit=archived["review_call_limit"],
                review_call_limit_origin=archived["review_call_limit_origin"],
            )
    if new_turn and archived and explicit:
        state["planning"].update(astra_calls=archived.get("astra_calls", 0))
        if "review_call_limit" in archived:
            state["planning"].update(
                review_call_limit=archived["review_call_limit"], review_call_limit_origin="user_explicit"
            )
    saved_review_limit = state.get("settings", {}).get("planning_review_call_limit")
    if type(saved_review_limit) is int and saved_review_limit == 0:
        state["planning"].update(review_call_limit=0, review_call_limit_origin="user_explicit")
    if role_names.planning_flow(state) == "v2":
        state["planning"]["inputs"] = planning_inputs(state)
    return state["planning"]


def admit_requirements(state: RunState, previous_token):
    """The planning record for the v2 Requirements handoff just accepted (#1252); ``previous_token`` is the
    requirements_artifact_token it replaced.

    A changed handoff after new input (planning_inputs: an answer, feedback or a follow-up)
    begins a new cycle, as each Planner draft does on the legacy flow: corrective feedback on a reviewed plan
    otherwise left the new plan's first review refused for the old cycle's charges. The same handoff admitted
    again, or a different one with no new input (a retried or repaired report), keeps the cycle and its spent
    reviews, so model output never renews the allowance. A record saved before this rule has no inputs: it is
    bound to the current ones and keeps its charges."""
    planning = state.get("planning")
    if not planning:
        return new_cycle(state)
    inputs = planning_inputs(state)
    if not isinstance(planning.get("inputs"), list):
        planning["inputs"] = inputs
        return planning
    if state.get("requirements_artifact_token") != previous_token and set(inputs) - set(planning["inputs"]):
        return new_cycle(state)
    return planning


def start(state: RunState):
    """Begin (or restart) planning: archive the previous planning record and route to plan review."""
    try:
        from . import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    planning = new_cycle(state)
    state.pop(human.PRIVATE, None)
    state.pop(human.PUBLIC, None)
    state.pop("user_request", None)
    next_stage = "plan_review" if role_names.planning_flow(state) == "v2" else "astra_challenge"
    if next_stage == "astra_challenge" and planning.get("inherited_review"):
        remaining = remaining_reviews(state, planning)
        if remaining is not None and remaining <= 1:
            # The redraft answers the inherited review as a revision would: with one call left, the final review
            # decides on it (what it cannot settle returns as blocking questions, as after any revision). With
            # more, the Plan Reviewer re-reviews it with its earlier concerns in the packet (previous_review).
            next_stage = "astra_finalize"
        planning["inherited_review"]["next_stage"] = next_stage
    state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": next_stage, "pending_questions": []})
