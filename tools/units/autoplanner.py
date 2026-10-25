"""Autoplanner owns requirements, draft plans and independent plan review."""

from __future__ import annotations

try:
    from .. import autocode_plan_chat as plan_chat
    from .. import autocode_plan_retention as retention
    from .. import autocode_planning_recovery_policy as planning_recovery_policy
    from .. import autocode_prompts as prompts
except ImportError:
    import autocode_plan_chat as plan_chat
    import autocode_plan_retention as retention
    import autocode_planning_recovery_policy as planning_recovery_policy
    import autocode_prompts as prompts


import copy
import json
import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..autocode_run_state import RunState

try:
    from .. import autocode_acceptance_policy as acceptance_policy
    from .. import autocode_adaptive_planning as adaptive
    from .. import autocode_brief_acceptance as brief_acceptance
    from .. import autocode_brief_literals as brief_literals
    from .. import autocode_brief_obligations as brief_obligations
    from .. import autocode_bug_job as bug_job
    from .. import autocode_compact_planning as compact_planning
    from .. import autocode_design_plan as design_plan
    from .. import autocode_draft_examples as examples
    from .. import autocode_failures as failures
    from .. import autocode_follow_up as follow_up
    from .. import autocode_goals as goals
    from .. import autocode_native_test_names as native_test_names
    from .. import autocode_planner_escalation as planner_escalation
    from .. import autocode_planning_artifacts as artifacts
    from .. import autocode_planning_report_policy as report_policy
    from .. import autocode_progressive_state as progressive
    from .. import autocode_regression_reference as regression_reference
    from .. import autocode_requirement_cues as requirement_cues
    from .. import autocode_risk_obligations as risk_obligations
    from .. import autocode_role_route as role_route
    from .. import autocode_roles as role_names
    from .. import autocode_route_fit as route_fit
    from .. import autocode_source_trace as source_trace
    from .. import autocode_stage_context as stage_context
    from .. import autocode_support as s
    from .. import autocode_task_authority as task_authority
    from .. import autocode_test_cases as test_cases
    from .. import autocode_trace_coverage as trace_coverage
    from .. import autocode_util as util
    from .. import autocode_utility_route as utility_route
    from .. import autocode_verification_plan as verification_plan
    from .. import autocode_workflows as workflows
except ImportError:
    import autocode_acceptance_policy as acceptance_policy
    import autocode_adaptive_planning as adaptive
    import autocode_brief_acceptance as brief_acceptance
    import autocode_brief_literals as brief_literals
    import autocode_brief_obligations as brief_obligations
    import autocode_bug_job as bug_job
    import autocode_compact_planning as compact_planning
    import autocode_design_plan as design_plan
    import autocode_draft_examples as examples
    import autocode_failures as failures
    import autocode_follow_up as follow_up
    import autocode_goals as goals
    import autocode_native_test_names as native_test_names
    import autocode_planner_escalation as planner_escalation
    import autocode_planning_artifacts as artifacts
    import autocode_planning_report_policy as report_policy
    import autocode_progressive_state as progressive
    import autocode_regression_reference as regression_reference
    import autocode_requirement_cues as requirement_cues
    import autocode_risk_obligations as risk_obligations
    import autocode_role_route as role_route
    import autocode_roles as role_names
    import autocode_route_fit as route_fit
    import autocode_source_trace as source_trace
    import autocode_stage_context as stage_context
    import autocode_support as s
    import autocode_task_authority as task_authority
    import autocode_test_cases as test_cases
    import autocode_trace_coverage as trace_coverage
    import autocode_util as util
    import autocode_utility_route as utility_route
    import autocode_verification_plan as verification_plan
    import autocode_workflows as workflows
# Which role and engine run a stage, and the planning cycle, live below this unit so the stage context and the
# goal lifecycle use them without importing it back; they keep their names here (autocode_planning, autopilot).
try:
    from ..autocode_planning_cycle import admit_requirements, new_cycle, planning_inputs, start  # noqa: F401 - planning API
    from ..autocode_stage_roles import (
        RECOGNIZE,
        STAGES,
        V2_STAGE_ROLES,
        V2_STAGES,
        enabled,
        engine_for,
        is_planning,
        role_for,
    )
except ImportError:
    from autocode_planning_cycle import admit_requirements, new_cycle, planning_inputs, start  # noqa: F401 - planning API
    from autocode_stage_roles import (
        RECOGNIZE,
        STAGES,
        V2_STAGE_ROLES,
        V2_STAGES,
        enabled,
        engine_for,
        is_planning,
        role_for,
    )
from .model_request import ModelRequest

# A build that implements an approved design (autocode_design_check_job) skips requirements
# gathering; every planning stage gets this rule and the design's binding decisions.
APPROVED_DESIGN_RULE = prompts.get("fragments/autoplanner/approved-design-rule.md")
# A reproduced bug the Investigator sized large (autocode_bug_job.large_correction)
# uses the diagnosis as technical evidence; the human task remains authoritative.
BUG_DIAGNOSIS_RULE = prompts.get("fragments/autoplanner/bug-diagnosis-rule.md")
# A follow-up that acts on an earlier review in the same run (autocode_follow_up) is planned
# from the review's findings; requirements gathering is skipped.
REVIEW_FINDINGS_RULE = prompts.get("fragments/autoplanner/review-findings-rule.md")
# Features get the bug-fix proof too: the plan states testable criteria as concrete
# examples marked "test:", and the runner proves each one at its milestone (autocode_test_cases).
# A test: criterion must be new behavior: a live parallel-diamond plan made "the contract package is
# importable" one, which passes before the change (a namespace package), so the milestone could never
# be proven and the run stalled on it (2026-09-29).
# Tests must check behavior, not the repository's file listing: a live port-policy-go plan turned
# "deliver these four files" into a test that failed once its checker built policy.bin (2026-09-29).
EXAMPLE_CRITERIA_RULE = prompts.get("fragments/autoplanner/example-criteria-rule.md") + test_cases.NAMED_PROOF_NOTE
# Two live ladder runs (Claude models, 2026-09-30) approved an example that contradicted its own rule: "2024-02-28
# to 2024-03-01 is 4 dates", and an entry with a TTL of 2**63 still present at time 1e300. Both plan reviews passed
# it, the Builder bent its test to fit, and the run stopped for a person after the build.
EXAMPLE_CHECK_RULE = prompts.get("fragments/autoplanner/example-check-rule.md")
# A live greenfield run (2026-10-01, docs/bugs/2026-10-01-reliability-live-cases.md) transcribed the brief's
# literal "ID TEXT [open|done]" into examples without the brackets; the Builder, the tests, the Validator and
# the completion gate then all honestly served the corrupted criteria and the run completed falsely. Every
# other handoff has an independent check; the brief-to-criteria transcription had none. The runner now
# rejects a draft that drops a backticked brief literal (autocode_brief_literals); this rule asks the
# reviewer whether the examples agree with it, which no mechanical check can decide.
BRIEF_TRACE_RULE = prompts.get("fragments/autoplanner/brief-trace-rule.md")
# The same question the runner raises mechanically (autocode_trace_coverage.proxy_gaps, #2068): a
# criterion traced to a user-visible requirement may prove an internal value while a note places
# the visible result outside this checkout, and only the person can confirm the connection.
OUT_OF_CHECKOUT_PROXY_RULE = prompts.get("fragments/autoplanner/out-of-checkout-proxy-rule.md")
# A live cent-drift plan (2026-09-30) required a 175,712-cart enumeration to "finish in under about 10 seconds". The
# Builder asserted elapsed time, the test took 10.39 s on a loaded machine, and the run stopped after two retries
# with correct billing code: no code change could make the criterion hold.
NO_TIMING_RULE = prompts.get("fragments/autoplanner/no-timing-rule.md")
# A design job delivers documents only (autocode_test_cases.design_only), so it gets this instead of the
# example-criteria rule, which made a live design run plan every criterion as a test and add tests/.
DESIGN_DELIVERABLES_RULE = prompts.get("fragments/autoplanner/design-deliverables-rule.md")
# Planning is otherwise never told how execution captures test evidence, so plans invented
# scratch copies outside the workspace and reviewers blocked them for a "missing capture
# command" (bugfix-cent-drift, 2026-09-28: three planning rounds).
EVIDENCE_FACTS = (
    prompts.get("fragments/autoplanner/evidence-facts-02.md")
    + verification_plan.GIT_STATUS_RULE
    + "\n"
    + verification_plan.SHELL_SYNTAX_RULE
    + prompts.get("fragments/autoplanner/evidence-facts.md")
)
REVISION_CONFLICT_RULE = prompts.get("fragments/autoplanner/revision-conflict-rule.md")
PRIOR_REJECTION_RULE = prompts.get("fragments/autoplanner/prior-rejection-rule.md")
RESPONSE_EVIDENCE_RULE = prompts.get("fragments/autoplanner/response-evidence-rule.md")
# A live review-then-fix plan (2026-09-29) marked "the diff touches only the two fixes" for human
# review although its own verification method was "Validator reads git diff"; the run then
# stopped for an approval nobody needed.
HUMAN_REVIEW_NOTE = prompts.get("fragments/autoplanner/human-review-note.md")


CONTRACT_FIELDS_RULE = prompts.get("fragments/autoplanner/contract-fields-rule.md") + HUMAN_REVIEW_NOTE + "\n"
# Planner reports were sent back for repair with "Planner dropped requirements with no trace" in
# several live runs (Claude models, 2026-09-29): the report's requirement_trace was [] although the
# handoff listed R1..Rn, buried in requirements_handoff. The stages that must trace them get the
# IDs as a short list (requirement_trace_rows) and this rule; the runner's check is unchanged.
REQUIREMENT_TRACE_RULE = prompts.get("fragments/autoplanner/requirement-trace-rule.md")
# #1822: a greenfield Requirements handoff stated behaviors but cited nothing, though README.md existed.
# The validator required a citation and neither the stage prompt nor the repair handoff said so; the
# repair then cited an archived .autocode/ log. Both prompts now carry this one rule.
SOURCE_REFS_RULE = prompts.get("fragments/autoplanner/source-refs-rule.md")
TRACE_STAGES = ("astra_discovery", "glm_revise", "astra_finalize")
# Stages that draft or review contract criteria cite and check source traces (#595).
CITATION_STAGES = (
    "requirements",
    "astra_discovery",
    "glm_revise",
    "astra_challenge",
    "astra_finalize",
    "plan",
    "plan_review",
    "plan_revise",
    "plan_finalize",
)


def source_inventory(state: RunState):
    """The handoff list models quote in criterion sources (#595); shared by prepare and report repair."""
    return [
        {"id": row["id"], "kind": row["kind"], "text": row["text"]}
        for row in source_trace.quotable(brief_obligations.sources(state))
    ]


def repair_source_inventory(state: RunState, stage):
    """Citation stages keep source_inventory on report-repair handoffs (#595 / #1201)."""
    return source_inventory(state) if stage in CITATION_STAGES else None


def repair_citation_context(state: RunState, stage, workspace):
    """The handoff's citation context: source sentences, plus the citable files a Requirements repair may name.

    The Requirements handoff check requires a complete report to cite an existing workspace file (#1822);
    the repair previously repeated only its error, and a greenfield repair then cited an archived
    .autocode/ log. workspace_inventory names the files the runner will accept, and SOURCE_REFS_RULE
    states the same rule the stage prompt carries."""
    context = {"source_inventory": repair_source_inventory(state, stage)}
    if stage == "requirements_gather":
        context["workspace_inventory"] = workspace_inventory(workspace, state.get("task", ""))
        context["source_refs_rule"] = SOURCE_REFS_RULE
    return context


# Stages that write criteria, which cite sentences by id; the reviewing stages only check coverage.
SENTENCE_ID_STAGES = tuple(
    stage
    for stage in CITATION_STAGES
    if stage not in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize")
)


def source_sentences(state: RunState, stage):
    """The numbered sentences a drafting stage's criteria must cover, with the requirement rows each supports."""
    if stage not in SENTENCE_ID_STAGES:
        return []
    return source_trace.sentence_rows(state, brief_obligations.sources(state), trace_rows(state, stage))


def resolve_sentence_citations(state: RunState, contract):
    """Turn a report's sentence-id citations into the verbatim quotes the rest of the runner checks."""
    if isinstance(contract, dict):
        source_trace.resolve_sentence_ids(contract, brief_obligations.sources(state))


def record_uncited_sentences(state: RunState, contract):
    """Save the source sentences ``contract``'s criteria leave uncited, for the next stage's handoff (#1630)."""
    if isinstance(state.get("planning"), dict):
        state["planning"]["uncited_sentences"] = source_trace.uncited_sentences(
            state, contract, brief_obligations.sources(state)
        )


def check_revision_coverage(state: RunState, contract):
    """A revision may not re-leave a sentence the last draft left uncited and its review did not excuse.

    The handoff already named those sentences to the Plan Reviewer, who excuses one only through out_of_scope.
    Refusing here costs no reviewer call; a first draft is never refused, since only the reviewer can say a
    sentence is out of scope (#595)."""
    planning = state.get("planning") or {}
    previous = planning.get("uncited_sentences") or []
    if not previous:
        return
    review = ((planning.get("reports") or {}).get("astra_challenge") or {}).get("report") or {}
    left = source_trace.unexcused_repeats(
        state,
        contract,
        brief_obligations.sources(state),
        previous,
        (review.get("source_trace") or {}).get("out_of_scope"),
    )
    if left:
        raise ValueError(
            "The revision still leaves source sentences uncited that the Plan Reviewer did not place out of scope: "
            + "; ".join(f"[{row['source_id']}] {row['sentence'][:100]!r}" for row in left)
            + ". Cite each in a criterion's sources."
        )


def traces_coverage(contract):
    """Whether a planner report's covered trace rows must cite this contract's criteria or behaviors.

    Not while the draft still has open_blocking_questions: such a draft may have no acceptance criteria yet
    (a clarification-only draft), so "covered" can only mean pending the answer. Every live plan that opened
    with a question paid a report repair for that (feature-refund-window, 2026-09-29). The trace must still
    list every requirement exactly once, exclusions and supersessions still need a saved user event, the
    draft cannot be approved, and the next draft, written after the answer, is checked in full.
    """
    return not (contract or {}).get("open_blocking_questions")


def trace_rows(state: RunState, stage):
    """The requirements a stage's requirement_trace must cover, one row each; [] when there are none."""
    if stage not in (*TRACE_STAGES, "astra_challenge"):
        return []
    handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    # Feedback on a shown plan that no Requirements report has read yet is traced like a requirement.
    return [
        {"requirement_id": row["id"], "requirement": row.get("text", ""), "source_quote": row.get("source_quote", "")}
        for row in (handoff.get("requirements") or []) + adaptive.feedback_requirements(state)
        if isinstance(row, dict) and row.get("id")
    ]


# A late question (the final review returned an unresolved concern to the user, usually permission to change
# a protected criterion) restarts planning after the answer, and the next review saw only the new draft:
# 11 of 31 answer-driven re-drafts in live runs (2026-09-28..10-01) reviewed a whole plan again from scratch.
REREVIEW_RULE = prompts.get("fragments/autoplanner/rereview-rule.md")
SENTENCE_ID_RULE = prompts.get("fragments/autoplanner/source-sentence-ids-rule.md")
UNCITED_REVIEW_RULE = prompts.get("fragments/autoplanner/uncited-sentences-review.md")
UNCITED_REVISE_RULE = prompts.get("fragments/autoplanner/uncited-sentences-revise.md")


def previous_review(state: RunState):
    """The last planning cycle's review when it ended in questions that have since been answered, so the next
    review checks the answers instead of reviewing the plan from scratch; None otherwise.

    An answer is the person's (``answers``) or AutoCode's own technical default (a ``technical_decision`` event,
    autocode_technical_questions.settle): after an internal-answer restart the redraft applies that default, and a
    review given nothing of its first review judged a live redraft cold (2026-10-20). Each answered question
    names who answered it, so the reviewer weighs a default as the Planner's choice, not the person's."""
    history = state.get("planning_history") or []
    reports = ((history[-1] if history else None) or {}).get("reports") or {}
    last: dict = next(
        (
            reports[stage]["report"]
            for stage in ("astra_finalize", "glm_revise")
            if (reports.get(stage) or {}).get("report")
        ),
        {},
    )
    questions = (last.get("contract") or {}).get("open_blocking_questions") or []
    answers = {
        qid: {"text": row.get("text", ""), "answered_by": "user"}
        for qid, row in (state.get("answers") or {}).items()
        if isinstance(row, dict)
    }
    for event in state.get("user_events") or []:
        if event.get("kind") == "technical_decision" and event.get("actor") == "autocode" and event.get("question_id"):
            answers.setdefault(event["question_id"], {"text": event.get("text", ""), "answered_by": "autocode"})
    if not questions or any(question.get("id") not in answers for question in questions):
        return None
    concerns = ((reports.get("astra_challenge") or {}).get("report") or {}).get("concerns") or []
    return {
        "concerns": [{key: row.get(key) for key in ("id", "concern", "blocking")} for row in concerns],
        "decisions": [
            {key: row.get(key) for key in ("concern_id", "decision", "resolved")} for row in last.get("decisions") or []
        ],
        "answered_questions": [
            {
                "id": question["id"],
                "question": question.get("question", ""),
                "answer": answers[question["id"]]["text"],
                "answered_by": answers[question["id"]]["answered_by"],
            }
            for question in questions
        ],
    }


def fill_trace_id(state: RunState, stage, value):
    """Name the one requirement_trace row a Planner report left without requirement_id, when exactly one
    requirement it must trace is missing from the trace: that row can only be for it, so no report repair is
    spent on the missing field (GLM 5.3 left it out when tracing feedback, 2026-10-02; 16 repairs to date).
    When nothing must be traced, rows without an ID are dropped. Anything ambiguous is left for the schema
    and trace checks to refuse, and a named row's evidence is still checked."""
    if not adaptive.enabled(state):
        return value
    trace = value.get("requirement_trace") if stage in TRACE_STAGES else None
    if not isinstance(trace, list) or not all(isinstance(row, dict) for row in trace):
        return value
    unnamed = [index for index, row in enumerate(trace) if "requirement_id" not in row]
    expected = trace_rows(state, stage)
    if unnamed and not expected:
        return {**value, "requirement_trace": [row for row in trace if "requirement_id" in row]}
    ids = [row["requirement_id"] for row in expected]
    assigned = [row["requirement_id"] for row in trace if "requirement_id" in row]
    if (
        len(set(ids)) != len(ids)
        or len(trace) != len(ids)
        or any(not isinstance(rid, str) or rid not in ids for rid in assigned)
        or len(set(assigned)) != len(assigned)
    ):
        return value
    untraced = [rid for rid in ids if rid not in assigned]
    if len(unnamed) != 1 or len(untraced) != 1:
        return value
    trace = [dict(row) for row in trace]
    trace[unnamed[0]]["requirement_id"] = untraced[0]
    return {**value, "requirement_trace": trace}


S, SS, obj = goals.STRING, goals.STRINGS, goals.obj
CONCERN = obj(
    {
        "id": S,
        "concern": S,
        "evidence_refs": SS,
        "requested_change": S,
        "acceptance_test": S,
        "blocking": {"type": "boolean"},
    }
)
RESPONSE = obj({"concern_id": S, "response": S, "evidence_refs": SS, "change": S, "acceptance_test": S})
DECISION = obj({"concern_id": S, "decision": S, "rationale": S, "acceptance_test": S, "resolved": {"type": "boolean"}})
REQUIREMENT = obj({"id": S, "text": S, "source_quote": S})
CONFLICT = obj({"requirement_ids": SS, "description": S})
CONFLICT_RESOLUTION = obj(
    {
        "requirement_ids": SS,
        "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]},
        "answer_id": S,
        "source_quote": S,
        "resolution": S,
    }
)
CHANGE = obj(
    {
        "item": S,
        "change": {"type": "string", "enum": ["removed", "reworded", "permission_changed"]},
        "basis": {"type": "string", "enum": ["user_answer", "user_feedback", "agent_proposed"]},
        "answer_id": S,
        "replacement": S,
    }
)
# Only a draft example correction carries a receipt. Generation schemas require every field, so a change
# that is not one says null; as a plain object field GLM 5.3's null failed every report declaring a
# contract change (2026-10-02). Readers treat anything but an object as no correction.
CHANGE["properties"]["example_correction"] = {**examples.RECEIPT_SCHEMA, "type": ["object", "null"]}
TRACE = obj(
    {
        "requirement_id": S,
        "disposition": {"type": "string", "enum": ["covered", "excluded", "superseded"]},
        "evidence": S,
    }
)
# New reports use the structured form; this is also the generation schema, so
# the model needs a concrete item shape. A report produced before structured
# assumptions (a plain string item) is still accepted by apply_planning, which
# validates only the structured items; goals.normalize_assumption reads both.
ASSUMPTION = obj(
    {
        "id": S,
        "text": S,
        "kind": goals.QUESTION["properties"]["kind"],
        "category": goals.QUESTION["properties"]["category"],
        "convention_ref": S,
        "rationale": S,
        "supports": SS,
    }
)
ASSUMPTIONS = {"type": "array", "items": ASSUMPTION}
IGNORED_REQUIREMENT = obj(
    {
        "requirement_id": S,
        "reason": S,
        "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]},
        "event_id": S,
    }
)
SCHEMAS = {
    "requirements_gather": obj(
        {
            "summary": S,
            "intended_outcome": S,
            "required_behaviors": SS,
            "constraints": SS,
            "acceptance_tests": SS,
            "source_refs": SS,
            "proposed_assumptions": ASSUMPTIONS,
            "open_questions": {"type": "array", "maxItems": 3, "items": goals.QUESTION},
            "requirements": {"type": "array", "items": REQUIREMENT},
            "ignored_statements": SS,
            "conflicts": {"type": "array", "items": CONFLICT},
        }
    ),
    "astra_discovery": obj(
        {
            "contract": goals.BODY_SCHEMA,
            "summary": S,
            "code_refs": SS,
            "alternatives": SS,
            "uncertainties": SS,
            "contract_changes": {"type": "array", "items": CHANGE},
            "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
            "requirement_trace": {"type": "array", "items": TRACE},
        }
    ),
    "astra_challenge": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
    "glm_revise": obj(
        {
            "contract": goals.BODY_SCHEMA,
            "summary": S,
            "code_refs": SS,
            "responses": {"type": "array", "items": RESPONSE},
            "contract_changes": {"type": "array", "items": CHANGE},
            "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
            "requirement_trace": {"type": "array", "items": TRACE},
        }
    ),
    "astra_finalize": obj(
        {
            "contract": goals.PLANNING_BODY_SCHEMA,
            "summary": S,
            "decisions": {"type": "array", "items": DECISION},
            "contract_changes": {"type": "array", "items": CHANGE},
            "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
            "requirement_trace": {"type": "array", "items": TRACE},
        }
    ),
}
SCHEMAS.update(
    {
        "requirements": obj({"requirements": goals.REQUIREMENTS_BODY_SCHEMA, "summary": S}),
        "plan": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S}),
        "plan_review": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
        "plan_revise": obj(
            {"contract": goals.PLANNING_BODY_SCHEMA, "summary": S, "responses": {"type": "array", "items": RESPONSE}}
        ),
        "plan_finalize": obj(
            {"contract": goals.PLANNING_BODY_SCHEMA, "summary": S, "decisions": {"type": "array", "items": DECISION}}
        ),
    }
)
# Only independent reviewers may propose executable observations; the runner seals their provenance.
BRIEF_OBSERVATION_CHANGE = obj({"previous_hash": S, "declaration_id": S, "source_event_id": S})
for _stage in ("astra_challenge", "astra_finalize", "plan_finalize"):
    SCHEMAS[_stage]["properties"]["brief_observations"] = brief_obligations.PROPOSALS_SCHEMA
    SCHEMAS[_stage]["properties"]["risk_observations"] = risk_obligations.PROPOSALS_SCHEMA
    SCHEMAS[_stage]["properties"]["risk_observation_changes"] = {"type": "array", "items": BRIEF_OBSERVATION_CHANGE}
    SCHEMAS[_stage]["properties"]["brief_observation_changes"] = {"type": "array", "items": BRIEF_OBSERVATION_CHANGE}
# The final reviewer pins which source sentences the plan deliberately leaves
# uncovered, and which quoted negations it accepts dropping (#595); the runner
# seals that judgment with the review's provenance (autocode_source_trace).
SOURCE_TRACE_ENTRY = obj({"source_id": S, "quote": S, "reason": S})
SOURCE_TRACE_SCHEMA = obj(
    {
        "out_of_scope": {"type": "array", "items": SOURCE_TRACE_ENTRY},
        "negation_waivers": {"type": "array", "items": SOURCE_TRACE_ENTRY},
    }
)
for _stage in ("astra_challenge", "astra_finalize", "plan_finalize"):
    SCHEMAS[_stage]["properties"]["source_trace"] = SOURCE_TRACE_SCHEMA

# Live planning reports were rejected, each costing a report repair, for a contract whose deliverables,
# required_behaviors or permission_boundaries was an empty list (VALIDATION.md 2026-09-26; two Claude-model
# trials, 2026-09-29). The field was present, so requiring it changes nothing, and a hard minItems would refuse a
# draft that still has open_blocking_questions, where empty lists are legitimate. The model is told what each
# list is for, in the schema it is given and in CONTRACT_FIELDS_RULE. Descriptions do not affect validation.
CONTRACT_FIELD_NOTES = {
    "deliverables": prompts.get("fragments/autoplanner/contract-field-notes.md"),
    "required_behaviors": prompts.get("fragments/autoplanner/contract-field-notes-02.md"),
    "permission_boundaries": prompts.get("fragments/autoplanner/contract-field-notes-03.md"),
    "important_failure_cases": prompts.get("fragments/autoplanner/contract-field-notes-04.md"),
    "scope_exclusions": prompts.get("fragments/autoplanner/contract-field-notes-05.md"),
    "constraints": prompts.get("fragments/autoplanner/contract-field-notes-06.md"),
}


def _described(body):
    body = copy.deepcopy(body)
    for key, note in CONTRACT_FIELD_NOTES.items():
        body["properties"][key] = {**body["properties"][key], "description": note}
    criteria = body["properties"]["acceptance_criteria"]["items"]["properties"]
    criteria["human_review"] = {**criteria["human_review"], "description": HUMAN_REVIEW_NOTE}
    return body


for _stage in ("astra_discovery", "glm_revise", "astra_finalize", "plan", "plan_revise", "plan_finalize"):
    SCHEMAS[_stage]["properties"]["contract"] = _described(SCHEMAS[_stage]["properties"]["contract"])
# Optional for old saved reports; new prompts require this whenever intent must change.
SCHEMAS["requirements_gather"]["properties"]["proposed_reframes"] = {
    "type": "array",
    "items": obj({"requirement_id": S, "proposal": S, "question_id": S}),
}
# Optional tags name actual Requirements questions, never model-authored human statements.
for _requirements_stage in ("requirements_gather", "requirements"):
    SCHEMAS[_requirements_stage]["properties"]["project_conventions"] = {
        "type": "array",
        "maxItems": 32,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 128},
    }
SCHEMAS[RECOGNIZE] = workflows.SCHEMA
# Optional; required only when a refreshed handoff drops a requirement the
# previous handoff had (goals.check_requirement_handoff enforces the citation).
SCHEMAS["requirements_gather"]["properties"]["ignored_requirements"] = {"type": "array", "items": IGNORED_REQUIREMENT}
SCHEMAS["requirements_gather"]["properties"]["assistant_reply"] = S
# Requirements names the job it understood (#1846, #1860; workflows.from_requirements), or "none"
# while no software outcome is named yet. Optional for saved reports; asked for in every run.
SCHEMAS["requirements_gather"]["properties"]["job"] = {"type": "string", "enum": [*workflows.WORKFLOWS, "none"]}
SCHEMAS["requirements_gather"]["properties"]["design_document"] = S
# Judged with the job in an adaptive run (adaptive.requirements_judge_clarity), as the recognizer did.
SCHEMAS["requirements_gather"]["properties"]["clarity"] = {"type": "string", "enum": list(adaptive.CLARITY)}
# A discoverable question is answered from the workspace, never by the user.
# machine_resolutions are accepted only during the runner's one investigation
# pass, bound to the report that raised the question (handoff_hash). An
# access_blocker records that the source needed is missing or unreadable; the
# question must then remain as a kind="decision" question for the user.
MACHINE_RESOLUTION = obj({"question_id": S, "resolution": S, "source_refs": SS, "handoff_hash": S})
ACCESS_BLOCKER = obj({"question_id": S, "reason": S})
INVESTIGATION_STAGES = ("requirements_gather", "astra_discovery", "glm_revise")
for _stage in INVESTIGATION_STAGES:
    SCHEMAS[_stage]["properties"]["machine_resolutions"] = {"type": "array", "items": MACHINE_RESOLUTION}
    SCHEMAS[_stage]["properties"]["access_blockers"] = {"type": "array", "items": ACCESS_BLOCKER}
# A rejected assumption becomes a runner-owned obligation. The Planner proposes
# how the requirements it supported are still met (remediation_records); only a
# Plan Reviewer decision bound to that exact record's hash discharges it.
REMEDIATION = obj(
    {
        "obligation_id": S,
        "assumption_id": S,
        "approach": S,
        "evidence_refs": SS,
        "covered_requirements": SS,
        "episode_id": S,
    }
)
OBLIGATION_DECISION = obj(
    {"obligation_id": S, "remediation_hash": S, "resolved": {"type": "boolean"}, "rationale": S, "evidence_refs": SS}
)
for _stage in ("astra_discovery", "glm_revise"):
    SCHEMAS[_stage]["properties"]["remediation_records"] = {"type": "array", "items": REMEDIATION}
for _stage in ("astra_challenge", "astra_finalize"):
    SCHEMAS[_stage]["properties"]["obligation_decisions"] = {"type": "array", "items": OBLIGATION_DECISION}
# Optional progressive proposal for goals that only succeed as several useful
# end-to-end slices. The runner validates it, generates the plan-card disclosure
# from it and seals it at ordinary approval; a report without one keeps the
# ordinary path. Old saved reports remain valid.
PROGRESSIVE_CHECK = obj(
    {
        "id": S,
        "method": S,
        "relation": {"type": "string", "enum": ["contributes_to", "fully_verify"]},
        "criterion_ids": SS,
    }
)
PROGRESSIVE_SLICE = obj(
    {
        "id": S,
        "intended_result": S,
        "criterion_ids": SS,
        "paths": SS,
        "depends_on": SS,
        "checks": {"type": "array", "items": PROGRESSIVE_CHECK},
        "tentative": {"type": "boolean"},
    }
)
PROGRESSIVE_PROPOSAL = obj(
    {
        "version": {"type": "integer"},
        "needed_because": S,
        "shared_decisions": SS,
        "outstanding_criteria": SS,
        "done_slices": SS,
        "slices": {"type": "array", "items": PROGRESSIVE_SLICE},
    }
)
for _stage in ("astra_discovery", "glm_revise", "astra_finalize", "plan", "plan_revise", "plan_finalize"):
    SCHEMAS[_stage]["properties"]["progressive_proposal"] = PROGRESSIVE_PROPOSAL


# The job type travels requirements -> contract -> approval. Every planning stage still runs;
# for a bug fix they plan and review a small, defect-shaped plan instead of a feature.
SCHEMAS["requirements_gather"]["properties"]["task_kind"] = goals.TASK_KIND
JOB_TYPE_POLICY = goals.JOB_TYPE_POLICY


def entry_stage(state: RunState):
    return "requirements" if role_names.planning_flow(state) == "v2" else "requirements_gather"


def next_after(state: RunState, stage):
    if role_names.planning_flow(state) == "v2":
        return dict(zip(V2_STAGES, V2_STAGES[1:], strict=False)).get(stage)
    return {
        "requirements_gather": "astra_discovery",
        "astra_discovery": "astra_challenge",
        "astra_challenge": "glm_revise",
        "glm_revise": "astra_finalize",
    }.get(stage)


# Workflow stages that run on a route of their own (their unit's prepare() creates it). run_role
# derives engine, effort and session from route_for, so without this they silently ran on the
# Plan Reviewer's route: its effort, and its saved session (context leaking between stages).
JOB_ROUTES = {
    "investigate_bug": "investigator",
    "review_design": "architect",
    "check_design": "architect",
    "answer_question": "analyst",
    "investigate_stuck": "stuck_investigator",
    "write_acceptance_tests": "acceptance_tester",
}


def _resolve_route(state: RunState, stage, role=None):
    """Return the saved model route for a semantic workflow role.

    Completion remains a Plan Reviewer-format decision stage, but it intentionally has
    its own model, reasoning level, and session so plan review and completion
    ownership can be tuned independently.
    """
    if stage in ("astra_resolve", "astra_diagnose"):
        return "resolver"
    if utility := utility_route.route(state, stage):  # --utility-model (autocode_roles.UTILITY_STAGES)
        return utility
    if JOB_ROUTES.get(stage) in state.get("settings", {}).get("roles", {}):
        return JOB_ROUTES[stage]
    if stage == "requirements_gather":
        return "requirements"
    if stage == RECOGNIZE:
        return role_for(state, stage)
    if role_names.planning_flow(state) == "v2" and stage in V2_STAGE_ROLES:
        route = V2_STAGE_ROLES[stage]
        if route not in state.get("settings", {}).get("roles", {}):
            raise s.Paused("PAUSED_PLANNING_ROUTE", f"v2 planning requires the configured {route} role")
        return route
    role = role or role_for(state, stage)
    roles = state.get("settings", {}).get("roles", {})
    if stage in ("astra_challenge", "astra_finalize") and "plan_reviewer" in roles:
        return "plan_reviewer"
    if stage in ("astra_review", "astra_checkpoint") and "completion" in roles:
        return "completion"
    return role


def route_for(state: RunState, stage, role=None):
    """The saved route for a stage, with the reduced-depth policy active at the
    Plan Reviewer's own resolution (#1564).

    The reviewer launch reads its role record after this resolution, so activating
    the policy here (once per planning record) makes the first launch consume the
    reduced effort tier without rewriting saved settings mid-launch.
    """
    route = _resolve_route(state, stage, role)
    if route == "plan_reviewer":
        apply_review_depth_policy(state)
    return route


# Independent Plan Reviewer route (user 2026-09-26): never the Planner's model.
# Astra is too expensive and only for the Resolver (user 2026-09-28).
PINNED_REVIEWER_MODEL = "openai/gpt-6-sol"


def review_depth_reduced(state: RunState) -> bool:
    """Whether this run's review allowance counts across planning cycles (#1564).

    A small clear brief pays a total allowance, not a fresh one per revision: the
    2026-10-18 dogfood run spent three grants because each requirements revision
    restarted the cycle counter. ``--plan-review-full`` opts a run out; anything
    not clearly small at recognition (and whose drafted plan is not small once it
    exists) keeps today's per-cycle counting. While the policy is active the Plan
    Reviewer launches at the reduced effort tier.
    """
    if (state.get("settings") or {}).get("plan_review_full"):
        return False
    workflow = state.get("workflow") or {}
    if workflow.get("clarity") != "clear":
        return False
    if workflow.get("brief_size") == "small":
        return True
    adaptive_record = (state.get("planning") or {}).get("adaptive") or {}
    return adaptive_record.get("size") == "small"


def reduced_reviewer_effort(route):
    """The reduced Plan Reviewer effort tier for one role record (#1564).

    A provider's optional ``light_effort`` is the tier when it defines one;
    otherwise the configured effort steps one rung down the effort ladder
    (autocode_util.EFFORTS). An already-lowest effort stays lowest; an unset or
    off-ladder effort is returned unchanged, never an error.
    """
    light = route.get("light_effort")
    if isinstance(light, str) and light:
        return light
    effort = route.get("reasoning_effort")
    if effort not in util.EFFORTS:
        return effort
    index = util.EFFORTS.index(effort)
    return util.EFFORTS[index - 1] if index else effort


def apply_reviewer_effort_tier(state: RunState):
    """Write the reduced tier into the saved Plan Reviewer role record (#1564).

    Only the reasoning effort moves: the reviewer model and its pin are the
    cross-family contract and stay exactly as configured. ``--plan-review-full``
    and runs without a Plan Reviewer role keep their configured effort. Explicit
    efforts and legacy saved routes without effort provenance stay unchanged.
    """
    settings = state.get("settings") or {}
    if settings.get("plan_review_full"):
        return
    # Replay starts from archived effective routes. Reducing them again would
    # change the recorded effort; the replay transport still checks every dispatch.
    if settings.get("replay_recording"):
        return
    if not role_route.reviewer_effort_can_reduce(settings):
        return
    route = (settings.get("roles") or {}).get("plan_reviewer")
    if not isinstance(route, dict):
        return
    tier = reduced_reviewer_effort(route)
    if tier is None:
        return
    # The rung below may be one the model does not declare (GLM 5.3 declares low, high and max: a step from high
    # lands on medium). Fit it at the one boundary rule (#2015, #2057), and record the move; with no listing the
    # launch records the plain rung as unverified, as before (#2020).
    model = route.get("model")
    declared = route_fit.declared_for(settings, model) if isinstance(model, str) else None
    fitted = route_fit.fit(settings, "plan_reviewer", effort=tier, declared=declared)
    if fitted.reason == "fitted":
        route_fit.record(settings, "plan_reviewer", fitted, cause="review_depth")
    route["reasoning_effort"] = fitted.effort


def apply_review_depth_policy(state: RunState):
    """Activate the reduced depth at review admission; earlier calls join the total.

    Runs once per planning record: the first admission that finds the policy
    applicable carries every archived cycle's usage into the current counter, so
    reviews that already ran at full depth still count against the total, and
    applies the reduced Plan Reviewer effort tier once for the run.
    """
    if not review_depth_reduced(state):
        return
    planning = state.get("planning")
    if not isinstance(planning, dict):
        planning = state["planning"] = {}
    if planning.get("review_depth_reduced"):
        return
    archived = sum(
        row.get("astra_calls", 0)
        for row in state.get("planning_history") or []
        if row.get("turn_event_id") == planning.get("turn_event_id")
    )
    planning["review_depth_reduced"] = True
    planning["review_calls_before_policy"] = archived
    planning["astra_calls"] = archived + planning.get("astra_calls", 0)
    apply_reviewer_effort_tier(state)


def renew_for_revision(state: RunState, stage, limit) -> bool:
    """One more review of a revision that answered blocking concerns, once per run (#1815).

    Under the small-brief total (#1564) a live worker run spent its two calls on two challenges that both had
    blocking concerns; the Planner revised, and nothing was left to review the revision, so the run stopped for
    a person when the next step was simply the final review. A limit the person chose (a flag or config.toml)
    is never raised here."""
    planning = state.get("planning") or {}
    reports = planning.get("reports") or {}
    concerns = ((reports.get("astra_challenge") or {}).get("report") or {}).get("concerns") or []
    if (
        stage != "astra_finalize"
        or state.get("next_stage") != stage
        or not planning.get("review_depth_reduced")
        or planning.get("revision_review_renewals")
        or planning.get("review_call_limit_origin") == "user_explicit"
        or (state.get("settings") or {}).get("planning_review_call_limit_origin")
        or not adaptive.blocking(concerns)
        or "glm_revise" not in reports
    ):
        return False
    planning.update(
        review_call_limit=limit + 1,
        review_call_limit_origin="runner_default",
        revision_review_renewals=1,
        revision_review_renewal={
            "stage": stage,
            "challenge_output": reports["astra_challenge"].get("output"),
            "revision_output": reports["glm_revise"].get("output"),
            "blocking_ids": [str(concern.get("id")) for concern in adaptive.blocking(concerns)],
            "from": limit,
            "to": limit + 1,
            "at": s.now(),
        },
    )
    return True


def review_call_limit(state: RunState):
    limit = cast(dict, state.get("planning", {})).get(
        "review_call_limit", state.get("settings", {}).get("planning_review_call_limit", 2)
    )
    if type(limit) is not int or (limit != 0 and limit < 2):
        raise ValueError("Planning review call limit must be 0 (unlimited) or an integer of at least 2")
    return limit


def set_review_call_limit(state: RunState, limit):
    """An explicit allowance, preserving usage and approval boundaries."""
    try:
        from .. import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    published = human.current(state)
    issued_pause = None
    if published and published["scope"] == "operational_exhaustion":
        issued_pause = state["resolver"]["human_escalations"][published["request_id"]]["identity"]["proposal"][
            "origin"
        ].get("pause_status")
    unlimited_checkpoint = (
        type(limit) is int and limit == 0 and state.get("status") in ("PAUSED_STAGE_ABANDONED", "PAUSED_REQUESTED")
    )
    if (
        not enabled(state)
        or not state.get("planning")
        or (
            not unlimited_checkpoint
            and (
                (limit == 0 and state.get("status") != "PAUSED_PLANNING_BUDGET")
                or (
                    limit != 0
                    and (state.get("status") != "PAUSED_PLANNING_BUDGET" and issued_pause != "PAUSED_PLANNING_BUDGET")
                )
                or state.get("next_stage") not in ("astra_challenge", "astra_finalize")
            )
        )
        or any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts"))
    ):
        raise ValueError("Planning allowance requires a reconciled PAUSED_PLANNING_BUDGET checkpoint")
    previous = review_call_limit(state)
    if type(limit) is not int or (
        limit != 0 and (limit < 2 or limit < previous or limit < state["planning"]["astra_calls"])
    ):
        raise ValueError(
            "Planning review call limit must be 0 (unlimited) or an integer no smaller than the current limit and usage"
        )
    if limit == previous and state["planning"].get("review_call_limit_origin") == "user_explicit":
        return
    state["planning"]["review_call_limit"] = limit
    progressive.set_explicit_limits(state, review_calls=limit)
    state["planning"]["review_call_limit_origin"] = "user_explicit"
    if limit == 0:
        state["settings"]["planning_review_call_limit"] = 0
    human.supersede_operational(state, "Operator explicitly selected the planning review allowance")
    state.setdefault("user_events", []).append(
        {
            "kind": "planning_budget_change",
            "actor": "user_cli",
            "at": s.now(),
            "previous_limit": previous,
            "limit": limit,
            "calls_used": state["planning"]["astra_calls"],
            "stage": state["next_stage"],
            "contract_token": goals.token(state["goal_contract"]),
        }
    )


def refund_unreported(state: RunState, planning):
    """Only a review that returned a report counts against the allowance (user decision, 2026-09-29).

    A call is charged at admission, before anyone knows how it ends. An attempt that timed out or whose
    provider failed returned no review, so its call is given back here, before the next admission. The
    repeated-failure limit, not this allowance, stops a review that keeps failing. planning["review_charges"]
    holds the charge IDs of this planning cycle's ordinarily admitted calls (a recovery grant is never
    refunded), so a restarted cycle never refunds an earlier cycle's attempts.
    """
    charges = planning.get("review_charges") or []
    for row in state.get("stages", []):
        if row.get("planning_review_charge") in charges and (
            row.get("timed_out") or (type(row.get("exit_code")) is int and row["exit_code"] != 0)
        ):
            charges.remove(row["planning_review_charge"])
            planning["astra_calls"] -= 1
            row["planning_review_refunded"] = True


def charge(state: RunState, stage, record=None, workspace=None):
    if stage not in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize"):
        return
    if progressive.charge_review(state, stage, record):
        return
    planning = state["planning"]
    apply_review_depth_policy(state)
    refund_unreported(state, planning)
    limit = review_call_limit(state)
    if (
        limit
        and planning["astra_calls"] >= limit
        and not planning.get("recovery_review_grants")
        and renew_for_revision(state, stage, limit)
    ):
        limit = review_call_limit(state)
        if record is not None:
            record["planning_revision_renewal"] = True
    if limit and planning["astra_calls"] >= limit:
        if planning.get("recovery_review_grants"):
            try:
                from .. import autocode_resolver_runtime as resolver
            except ImportError:
                import autocode_resolver_runtime as resolver
            grant = resolver.validate_operational_grant(state, stage, workspace)
            if (
                not record
                or not record.get("output")
                or record.get("stage") != stage
                or planning.get("recovery_review_calls_used", 0) >= resolver.MAX_PLANNING_RECOVERY_GRANTS
                or planning["astra_calls"] >= limit + resolver.MAX_PLANNING_RECOVERY_GRANTS
            ):
                raise s.Paused("PAUSED_RESOLVER_OPERATIONAL", "Planning recovery admission is exhausted or unbound")
            grant.update(
                consumed=True,
                consuming_output=record["output"],
                consuming_iteration=record.get("iteration"),
                consumed_at=s.now(),
            )
            record["planning_recovery_grant"] = grant["id"]
            record["resolver_receipt_id"] = grant["id"]
            planning["recovery_review_calls_used"] = planning.get("recovery_review_calls_used", 0) + 1
            planning["astra_calls"] += 1
            return
        raise s.Paused(
            "PAUSED_PLANNING_BUDGET",
            f"{planning['astra_calls']}/{limit} plan-review calls used. "
            "AutoResolver could not authorize another safe operational call. "
            "Retained requirements and review evidence are unchanged; no approval is implied. "
            "Allow more reviews with --resume-paused --planning-review-call-limit N (more than the calls used), "
            "or give the Planner direction with --feedback TEXT.",
        )
    planning["astra_calls"] += 1
    if record is not None:
        record["planning_review_charge"] = uuid.uuid4().hex
        planning.setdefault("review_charges", []).append(record["planning_review_charge"])


def _coverage(rows, concerns):
    """Return the rows that answer plan-review concerns: exactly one substantive row per concern.

    A row for something that is not a concern (a question ID, or an empty placeholder row) answers
    nothing. It is neither an error nor checked, and callers keep only the returned rows, so no
    later step (unresolved decisions, progressive activation, the plan shown for approval) reads it.
    """
    concern_ids = {c["id"] for c in concerns}
    answers = [row for row in rows if row["concern_id"] in concern_ids]
    ids = [row["concern_id"] for row in answers]
    if len(ids) != len(set(ids)) or set(ids) != concern_ids:
        raise ValueError("Every plan-review concern needs exactly one response/decision using its ID")
    for row in answers:
        if any(isinstance(value, str) and not value.strip() for value in row.values()):
            raise ValueError("Planning responses and decisions must be substantive")
    return answers


PROMPTS = {
    "requirements_gather": prompts.get("requirements-gather.md"),
    "astra_discovery": prompts.get("discovery.md"),
    "astra_challenge": prompts.get("plan-challenge.md"),
    "glm_revise": prompts.get("requirements-revise.md") + RESPONSE_EVIDENCE_RULE,
    "astra_finalize": prompts.get("plan-finalize.md"),
}
PROMPTS.update(
    {
        "requirements": """You are the independently configured Requirements Planner. Return only the strict
requirements artifact. Do not create a technical approach, milestones, dependency graph, or implementation.
Blocking human questions are proposals for AutoResolver adjudication; never claim they were issued or answered.
""",
        "plan": """You are the independently configured Technical Planner. Read the exact verified requirements
artifact and delta. Return a complete technical plan with declared dependencies and affected_paths. Do not implement.
""",
        "plan_review": """You are the independently configured Plan Reviewer. Read the exact verified plan artifact
and delta. Return concise evidence-based concerns. Do not ask the human directly and do not implement.
""",
        "plan_revise": """You are the Technical Planner. Read the exact verified review artifact and delta, respond
to every concern, and return the revised complete plan. Do not implement.
""",
        "plan_finalize": """You are the independent Plan Reviewer. Read the exact verified revision artifact and
delta, settle every concern, and return the final plan. Blocking questions remain private proposals for AutoResolver.
Do not implement.
""",
    }
)


QUESTION_POLICY = prompts.get("fragments/autoplanner/question-policy.md")

ASSUMPTION_POLICY = prompts.get("fragments/autoplanner/assumption-policy.md")

INVESTIGATION_POLICY = prompts.get("fragments/autoplanner/investigation-policy.md")


OBLIGATION_POLICY = prompts.get("fragments/autoplanner/obligation-policy.md")


def obligation_policy(stage):
    """Request only obligation fields this planning stage can return."""
    if stage in ("astra_discovery", "glm_revise"):
        return OBLIGATION_POLICY + prompts.get("fragments/autoplanner/obligation-planner-policy.md")
    if stage == "astra_challenge":
        return OBLIGATION_POLICY + prompts.get("fragments/autoplanner/obligation-review-policy.md")
    if stage != "astra_finalize":
        return OBLIGATION_POLICY
    return """
REJECTED ASSUMPTIONS. deferred_obligations lists assumptions the user rejected; never rely on a
rejected assumption again, even reworded. An open obligation of kind human_decision must be asked
as a kind="decision" question whose id is the obligation id; the plan stays clarification-only
until the user answers it. Review the Planner's saved remediation proposals. Add one
obligation_decisions entry {obligation_id, remediation_hash, resolved, rationale, evidence_refs}
for every pending_review obligation, using its current remediation_hash and substantive evidence.
Any obligation still unresolved is asked as a decision question under its id in
contract.open_blocking_questions, and contract.initial_task.kind must be "none".
When no obligations await review, use [] for obligation_decisions. Omit remediation_records;
the finalization report does not propose remediations.
"""


PROGRESSIVE_POLICY = prompts.get("fragments/autoplanner/progressive-policy.md")


def progressive_policy(stage):
    """A concern-only review checks proposals; it cannot return one."""
    instruction = (
        prompts.get("fragments/autoplanner/progressive-review-policy.md")
        if stage == "astra_challenge"
        else prompts.get("fragments/autoplanner/progressive-proposal-policy.md")
    )
    return instruction + PROGRESSIVE_POLICY


def repair_rules(stage, schema):
    """Repeat planning semantics only for fields allowed by the saved repair schema."""
    fields = schema.get("properties", {})
    if stage not in (*TRACE_STAGES, "plan", "plan_revise", "plan_finalize") or "contract" not in fields:
        return ""
    if "retain_contract" in fields["contract"].get("properties", {}):
        return (
            "Use clarification_context.retained_contract as the current reference and "
            "clarification_context.retained_criteria for retained criterion identities.\n"
            + retention.final_rule(schema)
        )
    return (
        REVISION_CONFLICT_RULE
        + (retention.delta_rule(schema) if retention.allows_delta(schema) else "")
        + prompts.get("fragments/autoplanner/repair-citations.md")
        + (RESPONSE_EVIDENCE_RULE if "responses" in fields else "")
        + (progressive_policy(stage) if "progressive_proposal" in fields else "")
    )


def split_code_ref(root, ref):
    """(path, line citation) of a cited source entry. A line citation follows a colon ("path:12",
    "path:12-20 why"). Prose after an existing path ("path — why", "path: why") is the model's explanation,
    not a malformed citation: the runner checks the file, so the entry is not rejected for it."""
    token = ref.split(maxsplit=1)[0] if ref.strip() else ref
    if token != ref and ":" not in token and (Path(root) / token).is_file():
        return token, ""
    head, _, rest = ref.partition(":")
    if rest[:1].isspace() and (Path(root) / head).is_file():
        return head, ""
    return head, rest


# Paths the runner or the tools own: a plan may not cite them as evidence. They are transient (a run's
# active-processes.json is gone when the run ends), so a plan built on one breaks when the citation is
# re-read (VALIDATION.md, 2026-09-26: a bugfix plan cited .autocode/active-processes.json).
RUNNER_OWNED_PARTS = (".autocode", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def cited_file(root, path, field, ref):
    """The workspace file a citation names, or ValueError: it must exist inside the workspace and must not
    be one the runner or a tool owns or generates."""
    root = Path(root).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"{field} entry {ref} is not a file in the workspace")
    if target.suffix == ".pyc" or any(part in RUNNER_OWNED_PARTS for part in target.relative_to(root).parts):
        raise ValueError(
            f"{field} entry {ref} is a file the runner or a tool owns (.autocode/, .git/, "
            "__pycache__/ and caches): cite source files, which outlive the run"
        )
    return target


def workspace_inventory(workspace, task, limit=40, scan_limit=5000):
    """Bounded filesystem inventory; works in repositories and ordinary directories."""
    root = Path(workspace)
    # #1822: list no file cited_file refuses; a pytest cache sorted ahead of README.md and was offered as citable.
    ignored = {".venv", "venv", "node_modules", ".next", "dist", "build", ".cache", *RUNNER_OWNED_PARTS}
    words = set(re.findall(r"[a-z]{3,}", task.lower()))
    candidates = []
    truncated = False
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ignored and not Path(directory, d).is_symlink())
        for name in sorted(files):
            path = Path(directory, name)
            if name in ignored or name.endswith((".pyc", ".png", ".jpg", ".lock")) or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            score = sum(word in relative.lower() for word in words)
            candidates.append((-score, relative))
            if len(candidates) >= scan_limit:
                truncated = True
                break
        if truncated:
            break
    candidates.sort()
    return {
        "files": [name for _, name in candidates[:limit]],
        "truncated": truncated or len(candidates) > limit,
        "instruction": prompts.get("fragments/autoplanner/workspace-inventory.md"),
    }


def capture_command():
    """The command execution stages are given (autocode_stage_context.context_packet), shown to planning too."""
    import shlex
    import sys

    return shlex.join([sys.executable, str(Path(s.__file__).with_name("autocode.py")), "capture"])


BRIEF_OBSERVATION_REVIEW_RULE = prompts.get("fragments/autoplanner/brief-observation-review-rule.md")
BRIEF_ACCEPTANCE_CARRY_RULE = prompts.get("fragments/autoplanner/brief-acceptance-carry-rule.md")


RISK_OBSERVATION_REVIEW_RULE = prompts.get("fragments/autoplanner/risk-observation-review-rule.md")


def context(state: RunState, stage, state_path):
    if stage == "requirements_gather" and plan_chat.pending(state):
        return plan_chat.context(state)
    predecessor = None
    if stage in V2_STAGES:
        predecessor = artifacts.verify_predecessor(state, stage, Path(state_path).parent)
    report_recovery = planning_recovery_policy.active(state, stage)
    exchange: dict = copy.deepcopy(state.get("planning", {}))
    for entry in exchange.get("reports", {}).values():
        # Current contract is included once. Older full drafts stay retrievable
        # via the artifact path; every concern, response and decision remains in
        # the handoff because later review stages must account for every ID.
        if not report_recovery:
            entry["report"].pop("contract", None)
        # Trim verbose fields from older reports to keep the prompt bounded.
        report = entry.get("report") or {}
        for key in () if report_recovery else ("code_refs", "alternatives", "uncertainties", "summary"):
            if isinstance(report.get(key), list) and len(report[key]) > 5:
                report[key] = report[key][:5]
            elif isinstance(report.get(key), str) and len(report[key]) > 500:
                report[key] = report[key][:500] + "…"
    packet = {
        "task": state["task"],
        "workspace": state["workspace"],
        "state_file": str(state_path),
        "joint_planning": True,
        **({"spec_first": True} if stage == "requirements_gather" and state["settings"].get("spec_first") else {}),
        "execution_engine": engine_for(state["settings"], route_for(state, stage)),
        "stage": stage,
        "goal_contract": None if stage == "requirements_gather" else state.get("goal_contract"),
        "requirements_handoff": None if stage == "requirements_gather" else state.get("requirements_handoff"),
        "requirements_history": None
        if stage == "requirements_gather"
        else [
            {
                "output": entry.get("output"),
                "requirements": [
                    {"id": row["id"], "source_quote": row.get("source_quote", "")}
                    for row in (entry.get("report") or {}).get("requirements", [])
                ],
                "conflicts": (entry.get("report") or {}).get("conflicts", []),
            }
            for entry in state.get("requirements_history", [])
            if (entry.get("report") or {}).get("conflicts")
        ],
        "saved_answers": state.get("answers", {}),
        "brief_feedback": state.get("brief_feedback", []),
        "planning": exchange,
        "budget": prompts.get("fragments/autoplanner/context.md").format(review_call_limit(state) or "Unlimited"),
        "recovery_context": state.get("recovery_context"),
    }
    interpretations = goals.feedback_interpretation_context(state)
    if interpretations:
        packet["feedback_interpretations"] = interpretations
    if stage in ("requirements_gather", "requirements"):
        try:
            from .. import autocode_project_memory_lifecycle as project_memory
        except ImportError:
            import autocode_project_memory_lifecycle as project_memory
        packet["project_memory"] = project_memory.context(state)
    turn = requirement_cues.new_workflow_turn(state)
    if turn:
        packet["task"] = turn["say"]
        packet["previous_turn"] = {key: turn["previous"].get(key) for key in ("task", "workflow", "wrote")}
    if stage in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize"):
        packet["brief_declaration_inventory"] = brief_obligations.inventory(state)
        packet["risk_declaration_inventory"] = risk_obligations.inventory(state)
    uncited = exchange.pop("uncited_sentences", None)  # carried once, as uncited_source_sentences
    if uncited and stage in ("astra_challenge", "glm_revise", "astra_finalize"):
        packet["uncited_source_sentences"] = uncited
    sentences = source_sentences(state, stage)
    if sentences:
        packet["source_sentences"] = sentences
    if stage in CITATION_STAGES:
        # The model quotes these verbatim in criterion sources (#595); the fake
        # provider reads the same list to build its scripted citations. The full
        # brief stays only here when it already matches an inventory row (#1337).
        inventory = source_inventory(state)
        packet["source_inventory"] = inventory
        packet["task"] = brief_obligations.handoff_task(packet["task"], inventory)
    if predecessor:
        packet["predecessor_artifact"] = predecessor["artifact"]["path"]
        packet["predecessor_delta"] = predecessor["delta"]["path"]
    recovery_instruction = ""
    grants = [
        grant
        for grant in exchange.get("recovery_review_grants", [])
        if not grant.get("consumed") and grant.get("binding", {}).get("stage") == stage
    ]
    if report_recovery:
        try:
            from ..autocode_resolver_runtime import OPERATIONAL_INSTRUCTION
        except ImportError:
            from autocode_resolver_runtime import OPERATIONAL_INSTRUCTION
        packet["resolver_remediation"] = {
            "receipt_id": grants[0]["id"] if grants else None,
            "instruction": OPERATIONAL_INSTRUCTION,
            "report_only": True,
        }
        recovery_instruction = "\n" + OPERATIONAL_INSTRUCTION + "\n"
    if stage in ("astra_challenge", "astra_finalize") and (grants or packet["recovery_context"]):
        packet["workspace_inventory"] = workspace_inventory(state["workspace"], state["task"], limit=20)
    if stage == "requirements_gather":
        packet["requirement_coverage_checklist"] = [
            sentence for source in goals.scan_texts(state) for sentence in goals.cue_sentences(source)
        ]
    rows = trace_rows(state, stage)
    if rows:
        packet["requirement_trace_rows"] = rows
    earlier = previous_review(state) if stage == "astra_challenge" else None
    if earlier:
        packet["previous_review"] = earlier
    if state["settings"].get("figma_file"):
        packet["figma_file"] = state["settings"]["figma_file"]
    packet["user_events"] = state.get("user_events", [])
    try:
        from ..autocode_component_runtime import architecture_contract
    except ImportError:
        from autocode_component_runtime import architecture_contract
    runtime_contract = architecture_contract(state["task"])
    if runtime_contract:
        packet["architecture_runtime_contract"] = runtime_contract
    if state.get("design_constraint"):
        packet["approved_design"] = state["design_constraint"]
    diagnosis = bug_job.large_correction(state)
    if diagnosis:
        packet["bug_diagnosis"] = diagnosis
    findings = follow_up.review_findings(state)
    if findings:
        packet["review_findings"] = findings
    fallback = compact_planning.fallback_context(state)
    if fallback:  # the Plan Reviewer refused the runner's compact draft: the Planner drafts afresh (#2021)
        packet["compact_draft_fallback"] = fallback
    if stage == "requirements_gather":
        packet["previous_requirements_handoff"] = state.get("requirements_handoff")
        # Requirements names a follow-up's job (#1860): the earlier turn, as the recognizer was told it.
        earlier_turn = workflows.follow_up(state)
        if earlier_turn:
            packet["follow_up"] = earlier_turn
    if stage in ("requirements_gather", "astra_discovery"):
        packet["workspace_inventory"] = workspace_inventory(state["workspace"], state["task"])
    packet["regression_reference"] = regression_reference.context(state)
    try:
        from .. import autocode_design_manifest as design_manifest
        from .. import autocode_figma as figma
    except ImportError:
        import autocode_design_manifest as design_manifest
        import autocode_figma as figma
    figma_instruction = figma.instructions(state["settings"])
    manifest_context = design_manifest.planning_context(state["settings"], role=route_for(state, stage))
    if manifest_context:
        packet["design_manifest"] = manifest_context
        figma_instruction += design_manifest.INSTRUCTION
    author_rules, audit_context, audit_instruction = report_policy.scope_contract_rules(
        retention.model_schema(schema_for(state, stage), state, stage),
        {
            "decision_provenance": goals.DECISION_PROVENANCE,
            "contract_references": goals.CONTRACT_REFERENCES,
            "revision_conflicts": REVISION_CONFLICT_RULE,
            "example_criteria": DESIGN_DELIVERABLES_RULE if test_cases.design_only(state) else EXAMPLE_CRITERIA_RULE,
            "milestone_handoff": s.MILESTONE_POLICY,
        },
    )
    packet.update(audit_context)
    compact_final = stage in retention.FINAL and bool(retention.token(state))
    planning_policy = (
        ""
        if stage == "requirements_gather"
        else (
            author_rules["decision_provenance"]
            + author_rules["contract_references"]
            + ("" if compact_final else examples.RULE)
            + author_rules["milestone_handoff"]
            + EVIDENCE_FACTS
            + author_rules["revision_conflicts"]
            + ("" if compact_final or stage in ("astra_challenge", "plan_review") else CONTRACT_FIELDS_RULE)
        )
    )
    if stage != "requirements_gather":
        planning_policy += prompts.get("fragments/autoplanner/proof-reference-rule.md")
    progressive_rule = (
        progressive_policy(stage)
        if stage
        in (
            "astra_discovery",
            "glm_revise",
            "astra_challenge",
            "astra_finalize",
            "plan",
            "plan_revise",
            "plan_finalize",
        )
        else ""
    )
    if stage != "requirements_gather":
        packet["capture_command"] = capture_command()
    clarification_policy = ("" if stage == "astra_challenge" else QUESTION_POLICY) + (
        ASSUMPTION_POLICY if stage == "requirements_gather" else ""
    )
    if stage != "requirements_gather":
        packet["deferred_obligations"] = state.get("deferred_obligations", [])
        packet["clarification_episode"] = state.get("clarification_episode")
        clarification_policy += obligation_policy(stage)
    request = state.get("investigation_request")
    if request and request.get("stage") == stage:
        # Correctness must not depend on provider-session memory: the pass gets
        # everything it needs explicitly.
        packet["investigation_request"] = request
        clarification_policy += INVESTIGATION_POLICY
    design_rule = (APPROVED_DESIGN_RULE if state.get("design_constraint") else "") + (
        BUG_DIAGNOSIS_RULE if diagnosis else ""
    )
    design_rule += REVIEW_FINDINGS_RULE if findings else ""
    design_rule += compact_planning.fallback_rule(state)
    design_rule += task_authority.instruction(test_cases.current_diagnosis(state))
    if turn:
        design_rule += prompts.get("fragments/autoplanner/context-02.md")
    if stage == "requirements_gather":
        design_rule += SOURCE_REFS_RULE
    if stage != "requirements_gather":
        design_rule += author_rules["example_criteria"]
        # #498: the Go tests the user named are declared under those names; the runner refuses a prose alias.
        native = native_test_names.requested(state)
        design_rule += native_test_names.rule(native) if native else ""
        design_rule += EXAMPLE_CHECK_RULE if stage in ("astra_challenge", "astra_finalize") else ""
        design_rule += BRIEF_TRACE_RULE if stage in ("astra_challenge", "astra_finalize") else ""
        design_rule += OUT_OF_CHECKOUT_PROXY_RULE if stage in ("astra_challenge", "astra_finalize") else ""
        design_rule += NO_TIMING_RULE
    design_rule += acceptance_policy.COVERAGE
    if stage != "requirements_gather" and not test_cases.design_only(state):
        design_rule += acceptance_policy.DOMAIN
    if rows and stage in TRACE_STAGES:
        design_rule += REQUIREMENT_TRACE_RULE
    literals = brief_literals.literals(goals.scan_texts(state)) if stage in TRACE_STAGES else []
    design_rule += brief_literals.rule(literals) if literals else ""
    if stage in CITATION_STAGES:
        records = brief_obligations.sources(state)
        design_rule += source_trace.rule(records) if records else ""
    if stage != "requirements_gather":
        design_rule += BRIEF_ACCEPTANCE_CARRY_RULE
    if packet.get("source_sentences"):
        design_rule += SENTENCE_ID_RULE
    if packet.get("uncited_source_sentences"):
        design_rule += UNCITED_REVISE_RULE if stage == "glm_revise" else UNCITED_REVIEW_RULE
    if packet.get("brief_declaration_inventory"):
        design_rule += BRIEF_OBSERVATION_REVIEW_RULE
    if packet.get("risk_declaration_inventory"):
        design_rule += RISK_OBSERVATION_REVIEW_RULE
    design_rule += adaptive.prompt_rule(state, stage) + (REREVIEW_RULE if earlier else "")
    memory_rule = (
        prompts.get("fragments/autoplanner/project-memory.md")
        if stage in ("requirements_gather", "requirements")
        else ""
    )
    rejection = failures.recent_rejection(state, stage)
    rejection_rule = ""
    if rejection:
        packet["prior_rejection"] = {
            "attempts": rejection.get("count"),
            "last_error": str(rejection.get("last_error") or "")[:2000],
        }
        rejection_rule = PRIOR_REJECTION_RULE
    retain_rule = ""
    if stage in retention.AUTHORS and retention.token(state):
        packet["retained_criteria"] = retention.inventory(state)
        packet["retained_contract"] = retention.token(state)
        if stage in retention.FINAL:
            # The reference form takes no per-row references; its criteria delta is in the final rule.
            packet["retained_requirement_trace_available"] = retention.validated_trace(state) is not None
            retention.compact_final_packet(packet)
            retain_rule = retention.final_rule()
        else:
            retain_rule = retention.RULE + retention.delta_rule()
    prompt = (
        (retention.FINAL_PROMPT if compact_final else PROMPTS[stage])
        + audit_instruction
        + memory_rule
        + JOB_TYPE_POLICY
        + design_rule
        + recovery_instruction
        + rejection_rule
        + figma_instruction
        + planning_policy
        + clarification_policy
        + progressive_rule
        + s.COMMON
        + retain_rule
        + prompts.get("fragments/autoplanner/context-03.md")
        + json.dumps(packet, separators=(",", ":"))
    )
    return prompt, {
        "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
        "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000),
    }


def _model_report_schema(schema, state: RunState):
    """Protected contract provenance is retained by the runner, never generated by a model."""
    result = copy.deepcopy(schema)
    # assistant_reply only in a chat; job and design_document in every Requirements report (#1860), and
    # clarity with them while an adaptive run's Requirements names the job.
    fields = result.get("properties", {})
    dropped = [] if (state.get("settings") or {}).get("spec_first") else ["assistant_reply"]
    if "job" in fields and not adaptive.requirements_judge_clarity(state):
        dropped.append("clarity")
    for field in dropped:
        fields.pop(field, None)
        if field in result.get("required", []):
            result["required"].remove(field)
    for field in ("contract", "requirements"):
        body = result.get("properties", {}).get(field, {})
        for protected in ("brief_acceptance", "risk_acceptance", "source_trace"):
            body.get("properties", {}).pop(protected, None)
            if protected in body.get("required", []):
                body["required"].remove(protected)
    if not brief_obligations.inventory(state) and not ((state.get("goal_contract") or {}).get("body") or {}).get(
        brief_obligations.KEY
    ):
        # Strict model output schemas require every included property. Unrelated
        # tasks retain their existing report protocol; no empty feature fields.
        for field in ("brief_observations", "brief_observation_changes"):
            result.get("properties", {}).pop(field, None)
            if field in result.get("required", []):
                result["required"].remove(field)
    if not risk_obligations.inventory(state) and not ((state.get("goal_contract") or {}).get("body") or {}).get(
        risk_obligations.KEY
    ):
        for field in ("risk_observations", "risk_observation_changes"):
            result.get("properties", {}).pop(field, None)
            if field in result.get("required", []):
                result["required"].remove(field)
    return result


def prepare(state: RunState, stage, state_path, schema_dir):
    if progressive.revision_pending(state):
        transition = progressive.view(state)["transition"]
        schema = (
            obj(
                {
                    "summary": S,
                    "progressive_proposal": PROGRESSIVE_PROPOSAL,
                    "initial_task": goals.PLANNING_BODY_SCHEMA["properties"]["initial_task"],
                }
            )
            if transition["phase"] == "detail"
            else obj(
                {
                    "summary": S,
                    "accepted": {"type": "boolean"},
                    "product_changes": {"type": "boolean"},
                    "permission_changes": {"type": "boolean"},
                    "unresolved_product_decisions": {"type": "boolean"},
                }
            )
        )
        packet = {
            "goal_contract": state["goal_contract"],
            "progressive": progressive.context(state),
            "progressive_revision": copy.deepcopy(transition),
            "previous_plan": progressive.view(state)["plan"],
            "stage": stage,
            "task": state["task"],
            "workspace": state["workspace"],
            "current_task": state.get("current_task"),
            "saved_answers": state.get("answers", {}),
        }
        prompt = prompts.get("fragments/autoplanner/prepare.md") + json.dumps(packet, indent=2)
        role = role_for(state, stage)
        return ModelRequest(
            role,
            route_for(state, stage, role),
            prompt,
            {
                "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
                "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000),
            },
            schema,
            False,
        )
    if stage == RECOGNIZE:
        state["phase"] = "DISCOVERING"
        role = role_for(state, stage)
        prompt, metrics = workflows.prompt(
            state,
            workspace_inventory(state["workspace"], state["task"]),
            state["settings"].get("context_soft_tokens", 10000),
            engine_for(state["settings"], route_for(state, stage, role)),
        )
        return ModelRequest(role, route_for(state, stage, role), prompt, metrics, schema_for(state, stage), False)
    if stage not in STAGES + V2_STAGES:
        raise ValueError(f"Autoplanner cannot run {stage}")
    joint = is_planning(state, stage)
    state["phase"] = "PLANNING" if joint else "DISCOVERING"
    try:
        prompt, metrics = (
            context(state, stage, state_path) if joint else stage_context.context_packet(state, stage, state_path)
        )
    except ValueError as error:
        if stage in V2_STAGES:
            raise s.Paused("PAUSED_INVALID_PREDECESSOR", str(error)) from error
        raise
    if not joint:
        prompt = BRIEF_ACCEPTANCE_CARRY_RULE + prompt
        metrics = {**metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4}
    role = role_for(state, stage)
    schema = (
        schema_for(state, stage)
        if joint
        else design_plan.report_schema(goals.DISCOVERY_SCHEMA, (state.get("settings") or {}).get("design_manifest"))
    )
    return ModelRequest(
        role,
        route_for(state, stage, role),
        prompt,
        metrics,
        retention.model_schema(_model_report_schema(schema, state), state, stage),
        False,
    )


def schema_for(state: RunState, stage):
    """The report schema for a planning stage in this run (adaptive runs extend two of them)."""
    if stage == "requirements_gather" and plan_chat.pending(state):
        return plan_chat.SCHEMA
    if stage == RECOGNIZE:
        return adaptive.recognizer_schema(state, SCHEMAS[stage])
    return design_plan.report_schema(
        adaptive.report_schema(state, stage, SCHEMAS[stage], goals.PLANNING_BODY_SCHEMA),
        (state.get("settings") or {}).get("design_manifest"),
    )


def proxy_concerns(state: RunState, value):
    """One blocking concern per covered user-visible requirement a stand-in criterion discharges (#2068),
    appended to the review report before it is stored, so the person sees the question the plan cannot
    answer from this repository."""
    trace = retention.validated_trace(state)
    if not trace:
        return
    handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    body = (state.get("goal_contract") or {}).get("body") or {}
    existing = {str(row.get("id") or "") for row in value.setdefault("concerns", [])}
    # A proxy concern is raised once per requirement (#2068): the person's confirmation is the mechanical
    # exit, so a later round of the same contract is not re-blocked. The prompt rule still covers what
    # this check can no longer ask.
    raised = state["planning"].setdefault("proxy_concerns_raised", [])
    existing |= set(raised)
    for gap in trace_coverage.proxy_gaps(handoff.get("requirements") or [], trace, body):
        cid = "proxy-" + str(gap["requirement_id"])
        if cid in existing:
            continue
        proven = "; ".join(gap["criteria"]) or "no criterion cited"
        said = f"{proven} (the cited criteria)" if gap["criteria"] else proven
        seen = "`" + str(gap["literal"]) + "`"
        value["concerns"].append(
            {
                "id": cid,
                "concern": (
                    f"This proves {said}, the user sees {seen} (the literal), "
                    f"{seen} is rendered by code outside this checkout; confirm {proven} changes {seen}, "
                    "or ask the person"
                ),
                "evidence_refs": [str(gap["requirement_id"]), *gap["criteria"]],
                "requested_change": (
                    f"Trace {gap['requirement_id']} to a criterion that proves what the user sees for {seen}, "
                    "or confirm with the person that the cited criteria change it"
                ),
                "acceptance_test": (
                    f"A covered criterion for {gap['requirement_id']} names {seen} in its own text, "
                    "or a saved user answer confirms the cited criteria change it"
                ),
                "blocking": True,
            }
        )
        existing.add(cid)
        raised.append(cid)


def after_challenge(state: RunState, value, record):
    """Where the first review leads. In an adaptive run, a review with no blocking concern approves
    the Planner's draft as the final plan (autocode_adaptive_planning); otherwise the Planner revises."""
    compact = compact_planning.reviewing(state)
    if not adaptive.enabled(state):
        if compact:  # the runner's draft has no Planner to revise it: the full path plans afresh (#2021)
            compact_planning.fall_back(state, "the run does not plan adaptively")
            return
        state["next_stage"] = "glm_revise"
        return
    planning, contract = state["planning"], cast(dict, state["goal_contract"])
    if "adaptive" not in planning:
        planning["adaptive"] = {**adaptive.plan_size(contract["body"]), "approved_at": None, "challenges": 0}
        if planning.get("review_call_limit_origin") != "user_explicit":
            planning["review_call_limit"] = adaptive.review_limit(
                planning["adaptive"]["size"], review_call_limit(state)
            )
        planning["adaptive"]["review_limit"] = review_call_limit(state)
    planning["adaptive"]["challenges"] += 1
    if (
        adaptive.blocking(value["concerns"])
        or not adaptive.approvable(contract["body"])
        or progressive.view(state).get("candidate")
    ):
        if compact:
            # The compact draft is not revised: a blocking concern returns the run to the full path, where
            # the Planner drafts with this review in its packet (autocode_compact_planning.fall_back, #2021).
            blocking = adaptive.blocking(value["concerns"])
            compact_planning.fall_back(
                state,
                "the Plan Reviewer raised blocking concerns " + ", ".join(c["id"] for c in blocking)
                if blocking
                else "the Plan Reviewer's review could not approve the draft as written",
            )
            return
        # A progressive delegation is authorized by an accepted revision and final
        # independent review. A challenge cannot supply that approval evidence.
        state["next_stage"] = "glm_revise"
        escalate_planner(state, value)
        return
    try:
        from .. import autocode_goal_lifecycle as lifecycle
    except ImportError:
        import autocode_goal_lifecycle as lifecycle
    # The same path a final review takes: install the approved body and queue the user's approval.
    # The same path a final review takes: install the approved body and queue the user's approval. A refused
    # install is rolled back to the saved state, so no half-installed draft survives it.
    saved = copy.deepcopy(state)
    try:
        body = brief_obligations.reviewed_body(
            state,
            contract["body"],
            value.get("brief_observations") or [],
            record,
            changes=value.get("brief_observation_changes") or [],
            trace=value.get("source_trace"),
        )
        body = risk_obligations.reviewed_body(
            state,
            body,
            value.get("risk_observations") or [],
            record,
            changes=value.get("risk_observation_changes") or [],
        )
        lifecycle.install_draft(state, body, origin="adaptive_review_approval", record=record)
    except brief_acceptance.ObservationProposalError:
        # The Reviewer owns these proposals. Let the ordinary bounded report-repair path correct them;
        # sending this to the Planner repeats work without giving it authority to fix the review.
        raise
    except ValueError as error:
        # The draft cannot go to the user as it stands, for example a brief sentence no criterion
        # cites. The Planner owns the contract: rejecting this review sent it to report repair,
        # which cannot change the contract, and the run paused with no plan (#1630). Revise
        # instead, as every first review leads to outside adaptive planning.
        cast(dict, state).clear()
        state.update(saved)
        state["planning"]["adaptive"]["approval_refused"] = str(error)
        if compact:
            compact_planning.fall_back(state, f"the reviewed compact draft was refused: {error}")
            return
        state["next_stage"] = "glm_revise"
        return
    planning = state["planning"]
    planning["final_token"] = goals.token(state["goal_contract"])
    planning["adaptive"].update(
        approved_at=f"astra_challenge#{planning['adaptive']['challenges']}", final_stage="astra_challenge"
    )


def escalate_planner(state: RunState, value):
    """A revised draft the Plan Reviewer blocks again moves the Planner once to a stronger model for the next
    revision (autocode_planner_escalation). Only blocking concerns count: a draft sent back because it could not
    be approved as written, or because a progressive delegation needs a revision, is not the reviewer's verdict."""
    challenges = state["planning"]["adaptive"]["challenges"]
    blocking = adaptive.blocking(value["concerns"])
    if challenges < planner_escalation.CHALLENGE or not blocking:
        return None
    ids = ", ".join(str(concern["id"]) for concern in blocking)
    return planner_escalation.escalate(
        state,
        detail=f"Plan Reviewer challenge {challenges} still blocks the revised plan: {ids}",
        struggle_id=f"planning:{len(state.get('planning_history') or [])}",
    )


def rerun_requirements(state: RunState, value):
    """Whether the Planner sent feedback on the shown plan back to Requirements instead of revising the plan
    (adaptive planning). Its draft is discarded and the Requirements stage, which reads every saved feedback,
    runs next; the pipeline then continues in full, as it would without adaptive planning."""
    reason = adaptive.requirements_rerun(state, value)
    if reason:
        state.update(
            {
                "status": "RUNNING",
                "phase": "DISCOVERING",
                "next_stage": "requirements_gather",
                "discovery_summary": "Planner: " + reason,
            }
        )
    return bool(reason)


def after_revise(state: RunState):
    """After a revision: the final review, or in an adaptive run another first-style review while budget allows."""
    if not adaptive.enabled(state) or progressive.view(state).get("candidate"):
        return "astra_finalize"
    planning = state["planning"]
    return adaptive.after_revise(
        review_call_limit(state), planning["astra_calls"], (planning.get("adaptive") or {}).get("challenges", 0)
    )


def recognize(state: RunState, value, record):
    """Save the recognized kind of job; the run then continues with its first real stage."""
    workflows.apply(state, value, record)
    state["phase"] = "DISCOVERING"
