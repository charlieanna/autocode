"""Autoplanner owns requirements, draft plans and independent plan review."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import uuid

try:
    from .. import autocode_goals as goals, autocode_planning_artifacts as artifacts, autocode_support as s
    from .. import autocode_bug_job as bug_job, autocode_workflows as workflows
except ImportError:
    import autocode_goals as goals
    import autocode_planning_artifacts as artifacts
    import autocode_support as s
    import autocode_bug_job as bug_job
    import autocode_workflows as workflows

STAGES = ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
# A build that implements an approved design (autocode_design_check_job) skips requirements
# gathering; every planning stage gets this rule and the design's binding decisions.
APPROVED_DESIGN_RULE = """
APPROVED DESIGN: approved_design in the handoff data is a design the user has already approved, checked
against this repository with no conflicts. It is a constraint, not a suggestion: plan exactly what it
specifies (module and file layout, names, signatures, rules, rejected alternatives). Do not redesign it,
do not revisit its rejected alternatives, and do not ask the user about decisions it already makes; ask
only about something it genuinely leaves open. Trace each of its constraints to a milestone.
"""
# A reproduced bug the Investigator sized large (autocode_bug_job.large_correction) is
# planned from its diagnosis; requirements gathering is skipped.
BUG_DIAGNOSIS_RULE = """
BUG FIX: bug_diagnosis in the handoff data is the Investigator's diagnosis of a reproduced bug, saved in the
repository at its note_path. It is the requirements: plan the correction of its root_cause, not a feature.
Every plan must uphold its invariant as an acceptance criterion, with a regression test that fails on the
original code and passes after the fix, and must keep the project's existing tests passing. Its test_cases
are those regression tests in plain English: make each one an acceptance criterion quoting its given, when
and then, and require one test per case named test_<id>_<what it checks> (T1 -> test_t1_...); the runner
refuses the fix unless every case has such a test that fails on the original code and passes after it. Fix the cause,
not the symptom, and do not widen the change beyond what the root cause needs. Do not ask the user what the
fix should achieve; ask only about a genuine choice the diagnosis leaves open.
Cite the diagnosis in code_refs as exactly its note_path; explanations go in summaries, never inside a path.
"""
# Features get the bug-fix proof too: the plan states testable criteria as concrete
# examples marked "test:", and the runner proves each one at its milestone (autocode_test_cases).
EXAMPLE_CRITERIA_RULE = """
TESTS IN PLAIN ENGLISH: write every acceptance criterion a test can check as one concrete example a person can
check without reading code: "Given <the exact starting data or state>, when <the exact action or command>,
then <the exact result, with literal values>". No vague words such as "correctly" or "gracefully". Set its
verification_method to "test: test_<criterion id in lowercase>_<what it checks>" (C2 -> test_c2_...). The
Builder writes that test; the runner itself checks that it passes with the change and did not pass before the
run began, and refuses the milestone and completion otherwise. With several milestones, list each test
criterion under the milestone that delivers it: the runner checks a milestone's tests, and those of milestones
already accepted, at that milestone's checkpoint, so a test must not depend on a later milestone. Keep criteria
a test cannot check (documentation, visual design, performance under real load) with an ordinary
verification_method.
"""
# Planning is otherwise never told how execution captures test evidence, so plans invented
# scratch copies outside the workspace and reviewers blocked them for a "missing capture
# command" (bugfix-cent-drift, 2026-09-28: three planning rounds).
EVIDENCE_FACTS = """
TEST EVIDENCE (how execution works; plan within it, do not re-derive it): the runner gives every Builder
and Validator the capture_command shown in the handoff. It runs a command in the workspace and saves the
full output as evidence in the run's own directory under .autocode/, which the runner owns: evidence is
never a deliverable, never an affected path and needs no permission. A fail-first criterion is met in the
workspace itself: add the regression test, capture it failing against the unmodified code, make the fix,
capture it passing. Do not plan scratch copies outside the workspace, and do not treat capture as a
missing prerequisite or ask the user to authorize it. Running the project's tests also creates files
(__pycache__/, *.pyc, caches) and the runner keeps its own files under .autocode/: never cite these as
evidence, and any check of which files changed must ignore them.
"""
# The first stage of every new run: which kind of job this is (autocode_workflows).
# It runs read-only with the requirements route when there is one, else the Plan Reviewer's.
RECOGNIZE = workflows.STAGE
V2_STAGES = ("requirements", "plan", "plan_review", "plan_revise", "plan_finalize")
V2_STAGE_ROLES = {
    "requirements": "requirements",
    "plan": "glm",
    "plan_revise": "glm",
    "plan_review": "plan_reviewer",
    "plan_finalize": "plan_reviewer",
}
S, SS, obj = goals.STRING, goals.STRINGS, goals.obj
CONCERN = obj({"id": S, "concern": S, "evidence_refs": SS, "requested_change": S,
               "acceptance_test": S, "blocking": {"type": "boolean"}})
RESPONSE = obj({"concern_id": S, "response": S, "evidence_refs": SS,
                "change": S, "acceptance_test": S})
DECISION = obj({"concern_id": S, "decision": S, "rationale": S,
                "acceptance_test": S, "resolved": {"type": "boolean"}})
REQUIREMENT = obj({"id": S, "text": S, "source_quote": S})
CONFLICT = obj({"requirement_ids": SS, "description": S})
CONFLICT_RESOLUTION = obj({"requirement_ids": SS,
    "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]},
    "answer_id": S, "source_quote": S, "resolution": S})
CHANGE = obj({"item": S, "change": {"type": "string", "enum": ["removed", "reworded", "permission_changed"]},
              "basis": {"type": "string", "enum": ["user_answer", "user_feedback", "agent_proposed"]},
              "answer_id": S, "replacement": S})
TRACE = obj({"requirement_id": S, "disposition": {"type": "string", "enum": ["covered", "excluded", "superseded"]},
             "evidence": S})
# New reports use the structured form; this is also the generation schema, so
# the model needs a concrete item shape. A report produced before structured
# assumptions (a plain string item) is still accepted by apply_planning, which
# validates only the structured items; goals.normalize_assumption reads both.
ASSUMPTION = obj({"id": S, "text": S, "kind": goals.QUESTION["properties"]["kind"],
                  "category": goals.QUESTION["properties"]["category"],
                  "convention_ref": S, "rationale": S, "supports": SS})
ASSUMPTIONS = {"type": "array", "items": ASSUMPTION}
IGNORED_REQUIREMENT = obj({"requirement_id": S, "reason": S,
    "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]}, "event_id": S})
SCHEMAS = {
    "requirements_gather": obj({
        "summary": S, "intended_outcome": S, "required_behaviors": SS,
        "constraints": SS, "acceptance_tests": SS, "source_refs": SS,
        "proposed_assumptions": ASSUMPTIONS,
        "open_questions": {"type": "array", "maxItems": 3, "items": goals.QUESTION},
        "requirements": {"type": "array", "items": REQUIREMENT},
        "ignored_statements": SS,
        "conflicts": {"type": "array", "items": CONFLICT},
    }),
    "astra_discovery": obj({"contract": goals.BODY_SCHEMA, "summary": S,
                            "code_refs": SS, "alternatives": SS, "uncertainties": SS,
                            "contract_changes": {"type": "array", "items": CHANGE},
                            "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                            "requirement_trace": {"type": "array", "items": TRACE}}),
    "astra_challenge": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
    "glm_revise": obj({"contract": goals.BODY_SCHEMA, "summary": S, "code_refs": SS,
                       "responses": {"type": "array", "items": RESPONSE},
                       "contract_changes": {"type": "array", "items": CHANGE},
                       "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                       "requirement_trace": {"type": "array", "items": TRACE}}),
    "astra_finalize": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S,
                           "decisions": {"type": "array", "items": DECISION},
                           "contract_changes": {"type": "array", "items": CHANGE},
                           "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                           "requirement_trace": {"type": "array", "items": TRACE}}),
}
SCHEMAS.update({
    "requirements": obj({"requirements": goals.REQUIREMENTS_BODY_SCHEMA, "summary": S}),
    "plan": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S}),
    "plan_review": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
    "plan_revise": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S,
                         "responses": {"type": "array", "items": RESPONSE}}),
    "plan_finalize": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S,
                           "decisions": {"type": "array", "items": DECISION}}),
})
# Optional for old saved reports; new prompts require this whenever intent must change.
SCHEMAS["requirements_gather"]["properties"]["proposed_reframes"] = {
    "type": "array", "items": obj({"requirement_id": S, "proposal": S, "question_id": S})}
SCHEMAS[RECOGNIZE] = workflows.SCHEMA
# Optional; required only when a refreshed handoff drops a requirement the
# previous handoff had (goals.check_requirement_handoff enforces the citation).
SCHEMAS["requirements_gather"]["properties"]["ignored_requirements"] = {
    "type": "array", "items": IGNORED_REQUIREMENT}
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
REMEDIATION = obj({"obligation_id": S, "assumption_id": S, "approach": S, "evidence_refs": SS,
                   "covered_requirements": SS, "episode_id": S})
OBLIGATION_DECISION = obj({"obligation_id": S, "remediation_hash": S, "resolved": {"type": "boolean"},
                           "rationale": S, "evidence_refs": SS})
for _stage in ("astra_discovery", "glm_revise"):
    SCHEMAS[_stage]["properties"]["remediation_records"] = {"type": "array", "items": REMEDIATION}
for _stage in ("astra_challenge", "astra_finalize"):
    SCHEMAS[_stage]["properties"]["obligation_decisions"] = {"type": "array", "items": OBLIGATION_DECISION}


# The job type travels requirements -> contract -> approval. Every planning stage still runs;
# for a bug fix they plan and review a small, defect-shaped plan instead of a feature.
SCHEMAS["requirements_gather"]["properties"]["task_kind"] = goals.TASK_KIND
JOB_TYPE_POLICY = goals.JOB_TYPE_POLICY


def enabled(state):
    return bool(state.get("settings", {}).get("joint_planning"))


def is_planning(state, stage):
    stages = V2_STAGES if state.get("settings", {}).get("planning_flow") == "v2" else STAGES
    # Recognition is a read-only planning stage on every run, joint or not.
    return stage == RECOGNIZE or (enabled(state) and stage in stages)


def entry_stage(state):
    return "requirements" if state.get("settings", {}).get("planning_flow") == "v2" else "requirements_gather"


def next_after(state, stage):
    if state.get("settings", {}).get("planning_flow") == "v2":
        return dict(zip(V2_STAGES, V2_STAGES[1:])).get(stage)
    return {"requirements_gather": "astra_discovery", "astra_discovery": "astra_challenge",
            "astra_challenge": "glm_revise", "glm_revise": "astra_finalize"}.get(stage)


def role_for(state, stage):
    if stage == RECOGNIZE:
        return "requirements" if "requirements" in state.get("settings", {}).get("roles", {}) else "astra"
    if state.get("settings", {}).get("planning_flow") == "v2" and stage in V2_STAGE_ROLES:
        return V2_STAGE_ROLES[stage]
    if is_planning(state, stage) and stage == "requirements_gather":
        return "requirements"
    if is_planning(state, stage) and stage in ("astra_discovery", "glm_revise"):
        return "glm"
    return "astra" if stage.startswith("astra") else stage


# Workflow stages that run on a route of their own (their unit's prepare() creates it). run_role
# derives engine, effort and session from route_for, so without this they silently ran on the
# Plan Reviewer's route: its effort, and its saved session (context leaking between stages).
JOB_ROUTES = {"investigate_bug": "investigator", "review_design": "architect", "check_design": "architect",
              "answer_question": "analyst", "investigate_stuck": "stuck_investigator"}


def route_for(state, stage, role=None):
    """Return the saved model route for a semantic workflow role.

    Completion remains a Plan Reviewer-format decision stage, but it intentionally has
    its own model, reasoning level, and session so plan review and completion
    ownership can be tuned independently.
    """
    if stage in ("astra_resolve", "astra_diagnose"):
        return "resolver"
    if JOB_ROUTES.get(stage) in state.get("settings", {}).get("roles", {}):
        return JOB_ROUTES[stage]
    if stage == "requirements_gather":
        return "requirements"
    if stage == RECOGNIZE:
        return role_for(state, stage)
    if state.get("settings", {}).get("planning_flow") == "v2" and stage in V2_STAGE_ROLES:
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


def engine_for(settings, role):
    return settings.get("roles", {}).get(role, {}).get("engine", settings.get("engine", "codex"))


# Independent Plan Reviewer route (user 2026-09-26): never the Planner's model.
# No MiMo anywhere (user 2026-09-27): OpenAI GPT-6 Sol via the ChatGPT login.
# Astra is too expensive and only for the Resolver (user 2026-09-28).
PINNED_REVIEWER_MODEL = "openai/gpt-6-sol"


def start(state):
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.start_planning(state)


def review_call_limit(state):
    limit = state.get("planning", {}).get("review_call_limit",
                state.get("settings", {}).get("planning_review_call_limit", 2))
    if type(limit) is not int or (limit != 0 and limit < 2):
        raise ValueError("Planning review call limit must be 0 (unlimited) or an integer of at least 2")
    return limit


def set_review_call_limit(state, limit):
    """An explicit allowance, preserving usage and approval boundaries."""
    try:
        from .. import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    published = human.current(state)
    issued_pause = None
    if published and published['scope'] == 'operational_exhaustion':
        issued_pause = state['resolver']['human_escalations'][published['request_id']]['identity']['proposal']['origin'].get('pause_status')
    unlimited_checkpoint = (type(limit) is int and limit == 0
                            and state.get('status') in ('PAUSED_STAGE_ABANDONED', 'PAUSED_REQUESTED'))
    if (not enabled(state) or not state.get("planning")
            or (not unlimited_checkpoint and (
                (limit == 0 and state.get("status") != "PAUSED_PLANNING_BUDGET")
                or (limit != 0 and (state.get("status") != "PAUSED_PLANNING_BUDGET"
                                    and issued_pause != "PAUSED_PLANNING_BUDGET"))
                or state.get("next_stage") not in ("astra_challenge", "astra_finalize")))
            or any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts"))):
        raise ValueError("Planning allowance requires a reconciled PAUSED_PLANNING_BUDGET checkpoint")
    previous = review_call_limit(state)
    if type(limit) is not int or (limit != 0 and (limit < 2 or limit < previous or limit < state["planning"]["astra_calls"])):
        raise ValueError("Planning review call limit must be 0 (unlimited) or an integer no smaller than the current limit and usage")
    if limit == previous and state['planning'].get('review_call_limit_origin') == 'user_explicit':
        return
    state["planning"]["review_call_limit"] = limit
    state['planning']['review_call_limit_origin'] = 'user_explicit'
    if limit == 0:
        state['settings']['planning_review_call_limit'] = 0
    human.supersede_operational(state, 'Operator explicitly selected the planning review allowance')
    state.setdefault("user_events", []).append({
        "kind": "planning_budget_change", "actor": "user_cli", "at": s.now(),
        "previous_limit": previous, "limit": limit, "calls_used": state["planning"]["astra_calls"],
        "stage": state["next_stage"], "contract_token": goals.token(state["goal_contract"])})


def refund_unreported(state, planning):
    """Only a review that returned a report counts against the allowance (user decision, 2026-09-29).

    A call is charged at admission, before anyone knows how it ends. An attempt that timed out or whose
    provider failed returned no review, so its call is given back here, before the next admission. The
    repeated-failure limit, not this allowance, stops a review that keeps failing. planning["review_charges"]
    holds the charge IDs of this planning cycle's ordinarily admitted calls (a recovery grant is never
    refunded), so a restarted cycle never refunds an earlier cycle's attempts.
    """
    charges = planning.get("review_charges") or []
    for row in state.get("stages", []):
        if (row.get("planning_review_charge") in charges
                and (row.get("timed_out") or (type(row.get("exit_code")) is int and row["exit_code"] != 0))):
            charges.remove(row["planning_review_charge"])
            planning["astra_calls"] -= 1
            row["planning_review_refunded"] = True


def charge(state, stage, record=None, workspace=None):
    if stage not in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize"):
        return
    planning = state["planning"]
    refund_unreported(state, planning)
    limit = review_call_limit(state)
    if limit and planning["astra_calls"] >= limit:
        if planning.get('recovery_review_grants'):
            try:
                from .. import autocode_resolver_runtime as resolver
            except ImportError:
                import autocode_resolver_runtime as resolver
            grant = resolver.validate_operational_grant(state, stage, workspace)
            if (not record or not record.get('output') or record.get('stage') != stage
                    or planning.get('recovery_review_calls_used', 0) >= resolver.MAX_PLANNING_RECOVERY_GRANTS
                    or planning['astra_calls'] >= limit + resolver.MAX_PLANNING_RECOVERY_GRANTS):
                raise s.Paused('PAUSED_RESOLVER_OPERATIONAL', 'Planning recovery admission is exhausted or unbound')
            grant.update(consumed=True, consuming_output=record['output'],
                         consuming_iteration=record.get('iteration'), consumed_at=s.now())
            record['planning_recovery_grant'] = grant['id']
            record['resolver_receipt_id'] = grant['id']
            planning['recovery_review_calls_used'] = planning.get('recovery_review_calls_used', 0) + 1
            planning['astra_calls'] += 1
            return
        raise s.Paused("PAUSED_PLANNING_BUDGET", f"{planning['astra_calls']}/{limit} plan-review calls used. "
                       "AutoResolver could not authorize another safe operational call. "
                       "Retained requirements and review evidence are unchanged; no approval is implied. "
                       "User feedback is needed only if the plan or requirements must change.")
    planning["astra_calls"] += 1
    if record is not None:
        record["planning_review_charge"] = uuid.uuid4().hex
        planning.setdefault("review_charges", []).append(record["planning_review_charge"])


def _coverage(rows, concerns):
    # A row for something that is not a concern (a question ID, say) answers nothing and is not an
    # error; every concern still needs exactly one row.
    concern_ids = {c["id"] for c in concerns}
    ids = [row["concern_id"] for row in rows if row["concern_id"] in concern_ids]
    if len(ids) != len(set(ids)) or set(ids) != concern_ids:
        raise ValueError("Every plan-review concern needs exactly one response/decision using its ID")
    for row in rows:
        if any(isinstance(value, str) and not value.strip() for value in row.values()):
            raise ValueError("Planning responses and decisions must be substantive")


def apply(state, stage, value, record, *, run_dir=None):
    """Compatibility entry; planning transitions belong to Autopilot."""
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.apply_planning(state, stage, value, record, run_dir=run_dir)


PROMPTS = {
    "requirements_gather": """You are the Requirements Gatherer, in your own read-only session.
Inspect the user's idea and relevant workspace source. Return only a requirements handoff:
intended outcome, stated behaviors, constraints, acceptance tests, source references,
up to three genuinely blocking questions, and clearly labeled proposed assumptions.
Do not create a technical approach, milestone, dependency graph, or implementation plan.
Do not treat a proposed default as a user answer. Do not implement.
When previous_requirements_handoff exists, retain its still-relevant requirements and
unanswered questions with stable IDs. A scope correction does not answer unrelated
questions (for example where the real backend lives). Prioritize those blockers over
new optional choices; do not silently replace them when refreshing the handoff.
The approved contract and current Builder task are inherited obligations, not new user
statements. Do not create a new requirement by quoting their milestone objectives,
Builder instructions, or test descriptions. Keep those obligations in the approved
contract. New requirements must quote the original task or an exact saved user event;
use requirement_coverage_checklist for the statements that need fresh coverage.
Preserve the user's literal requested outcome, even if infeasible. Never translate an
absolute guarantee into a weaker measurable promise without asking whether the user
accepts that change. Keep the original in requirements/required_behaviors; put each
suggested replacement in proposed_reframes (requirement_id, proposal, question_id)
with an explicit acceptance question in open_questions. Otherwise use proposed_reframes=[].
Distinguish the desired outcome from implementation instructions. Preserve explicitly
requested technology (for example Redis and three workers); if it appears to be a
suggested solution to a performance goal, ask whether it is mandatory or negotiable.
Do not silently discard it or assume it is the only way to achieve the outcome.
A later explicit correction can supersede an earlier statement: cite the saved event
and ask only about what remains ambiguous. Do not ask the user to repeat a clear correction.
proposed_reframes is only for agent-proposed changes, never user-authored corrections.
A narrow correction leaves unrelated exclusions in force: adding named actions permits
those actions, not every possible control. Do not ask permission to expand beyond them.
Use workspace_inventory to locate relevant existing code, then READ 4-6 key files
before making claims about current behavior. Do not explore indefinitely — read
enough to understand the architecture, then produce your structured output.
A missing package.json or src/ directory does not mean no application exists.
source_refs must include the actual repository-relative files read (optional :line),
not only 'task'; do not claim inspected behavior from filenames alone. A truncated
inventory is not evidence of absence. Use source_refs=[] only for an empty workspace.
Return requirements: each has an id, the requirement text, and a source_quote copied
verbatim from the task or a saved user event. Put requirement-like sentences you are
not carrying (must, must not, never, only, required, exactly) in ignored_statements
with the reason. Put unresolved contradictions in conflicts with the requirement ids.
Do not label an explicit saved clarification or a historical/current distinction as
an unresolved conflict. Preserve the applicable requirements and their provenance.
The runner saves this report as a separate artifact for the Planner.
The requirement_coverage_checklist contains the exact task sentences checked by
the runner. Account for every entry in requirements using a verbatim source_quote,
or in ignored_statements with the exact statement and a substantive reason.
Include requirements from the rest of the task and saved user events as well.
""",
    "astra_discovery": """You are the Planner, in a session separate from the Requirements Gatherer.
For a new run, use requirements_handoff and its saved artifact as your input; do not silently
replace its stated requirements or convert its proposed assumptions into user decisions.
Carry unresolved requirements questions into open_blocking_questions unless saved answers
resolve them. Older saved runs may lack a requirements handoff; only then gather missing
requirements yourself.
Read 5-8 key source files to understand the architecture, then STOP exploring and return
your structured output (code_refs, alternatives, uncertainties, contract). Do not read
every file — the workspace_inventory lists candidates; pick the most relevant ones.
First assess readiness. If any blocking question remains, return a clarification-only
contract: preserve known requirements and questions, set technical_approach=[] and
milestones=[], and do not invent a product, architecture, files, task DAG or initial task.
Only after blocking questions are answered, originate the concrete technical approach,
milestones and acceptance tests. Proposed defaults are not answers.
Mocks may support tests, but cannot replace the real behavior requested by the user.
If the real integration interface or implementation is missing, inspect or ask for it;
do not invent a mock-only deliverable or label real functionality as an accepted limitation.
For every milestone, state depends_on as prerequisite milestone IDs or [] when it can
start independently. Base those edges on actual interfaces, shared files, sequencing
and validation needs. Do not turn milestones into parallel jobs or launch any work.
Declare affected_paths for each milestone, including its tests and shared files.
Autopilot dispatches the Builder scheduler using approved dependencies and disjoint path ownership.
Do not implement. You may challenge assumptions and propose better approaches.
""",
    "astra_challenge": """You are the independent Plan Reviewer, challenging the Planner's draft (first review stage).
Inspect additional source when needed. Check every dependency edge, missing prerequisite,
cycle and claimed independent milestone against source evidence and interface ownership.
Check affected_paths for every milestone; overlapping writes must not be called independent.
Identify missing requirements, unsupported assumptions, unnecessary complexity and weak tests.
Compare the original task and saved user events with the handoff and contract, not just
the contract with itself. Flag weakened guarantees, unaccepted reframes, missed existing
functionality and proposed solutions treated as settled choices. Require explicit user
acceptance for changes to the requested outcome; useful suggestions alone cannot resolve them.
If real functionality is demonstrated only by a mock, raise a blocking concern requiring
the actual integration plan or a user decision about scope. An 'unverified' assumption
does not authorize replacing real behavior with a prototype.
Give concise, numbered concerns, evidence references,
requested changes and acceptance tests. Do not manufacture objections or write a second essay.
""",
    "glm_revise": """You are the Planner, investigating the Plan Reviewer's concerns. Respond to EVERY concern by ID
with evidence_refs, reasoning, the concrete change (or evidence-backed pushback) and a test.
Revise the complete contract, including depends_on for every milestone, and identify what changed.
You are a planning partner, not merely
a coder: retain your approach where source evidence supports it. Never hide unresolved questions.
If a concern exposes an unknown real integration or a proposed reduction to mock-only
scope, ask a blocking question. Do not settle it by adding an agent_proposed assumption
that the requested real behavior will remain unverified. Testing mocks is not implementing
the real requirement. Preserve the user's outcome until they explicitly change it.
""",
    "astra_finalize": """You are the independent Plan Reviewer, making the final planning decision (final review stage).
Settle EVERY concern by ID using the Planner's evidence-backed responses and source inspection as needed.
Confirm that milestone dependencies are complete and acyclic, and that [] is used only
for genuinely independent work. Do not schedule or launch milestones.
Return the proposed final contract and concise decisions/rationales/tests. Include initial_task
in the contract: objective, affected_paths, kind (implement or validate), milestone_id,
requirements, acceptance_criteria IDs, validation_plan. Its milestone must have depends_on [].
Make it a substantial, coherent,
executable milestone including related changes, tests, local fixes and evidence.
If blocked with no safe first task, use kind=none and empty task strings/lists.
Unresolved decisions MUST appear in open_blocking_questions, never silently become assumptions.
There is no further debate round. The user must approve this exact plan before implementation.
""",
}
PROMPTS.update({
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
})


QUESTION_POLICY = """
QUESTION CLASSIFICATION. Every question object carries kind, category and delegable.
kind="discoverable" only when the answer is a fact in the workspace you have not read yet
(where something is configured, which interface exists). Prefer reading it now; the runner
never shows a discoverable question to the user. kind="decision" for a choice only the user
can make. category names what the answer changes: cost, quota, permission,
external_side_effect, requested_outcome, behavior, technical or other.
delegable=true only when proposed_default is a safe choice the user may accept wholesale;
always false for cost, quota, permission, external_side_effect and requested_outcome.
Where the report has machine_resolutions and access_blockers, use [] unless
investigation_request is present.
"""

ASSUMPTION_POLICY = """
ASSUMPTIONS. Each proposed_assumptions entry is {id, text, kind, category, convention_ref,
rationale, supports}. Give it a stable id (A1, A2, ...) and keep ids across refreshes.
Use kind="inferable" with a convention_ref (repository path:line, or a saved event id) and a
rationale that establish the convention. supports lists the requirement ids it underpins.
Never mark cost, quota, permission, external_side_effect or requested_outcome inferable; ask a
decision question instead. When previous_requirements_handoff exists and you drop one of its
requirements, list it in ignored_requirements as {requirement_id, reason, basis, event_id}
citing the saved user answer or feedback event that authorizes it; otherwise use [].
"""

INVESTIGATION_POLICY = """
INVESTIGATION PASS. investigation_request lists discoverable questions from your previous
report (prior_report), bound to handoff_hash. This is the only investigation pass in this
clarification episode. For each question, do exactly one of:
- read the workspace and add a machine_resolutions entry {question_id, resolution,
  source_refs (existing repository files you read, optional :line), handoff_hash}, and remove
  the question from your questions (only for category technical or other);
- keep it as a kind="decision" question when it is really the user's choice;
- if the source needed is missing or unreadable, keep it as a kind="decision" question and add
  an access_blockers entry {question_id, reason}.
Anything still discoverable after this pass is shown to the user as a decision. Otherwise
return the complete report as before.
"""


OBLIGATION_POLICY = """
REJECTED ASSUMPTIONS. deferred_obligations lists assumptions the user rejected; never rely on a
rejected assumption again, even reworded. An open obligation of kind human_decision must be asked
as a kind="decision" question whose id is the obligation id; the plan stays clarification-only
until the user answers it. For an open remediation obligation, the Planner may add a
remediation_records entry {obligation_id, assumption_id, approach, evidence_refs,
covered_requirements (exactly the obligation's supports, each covered in requirement_trace),
episode_id (clarification_episode.id)}. The Plan Reviewer must add one obligation_decisions entry
{obligation_id, remediation_hash, resolved, rationale, evidence_refs} for every pending_review
obligation, using its current remediation_hash; resolved=false in the first review needs a
blocking concern citing the obligation id. At final review, any obligation still unresolved is
asked as a decision question under its id, and initial_task.kind must be "none". Otherwise use
[] for remediation_records and obligation_decisions.
"""


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
        raise ValueError(f"{field} entry {ref} is a file the runner or a tool owns (.autocode/, .git/, "
                         "__pycache__/ and caches): cite source files, which outlive the run")
    return target


def workspace_inventory(workspace, task, limit=40, scan_limit=5000):
    """Bounded filesystem inventory; works in repositories and ordinary directories."""
    root = Path(workspace)
    ignored = {".git", ".autocode", ".venv", "venv", "node_modules", "__pycache__",
               ".next", "dist", "build", ".cache"}
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
    return {"files": [name for _, name in candidates[:limit]],
            "truncated": truncated or len(candidates) > limit,
            "instruction": "File names are navigation hints, not evidence of behavior. Read relevant files."}


def capture_command():
    """The command execution stages are given (autocode_support.context_packet), shown to planning too."""
    import shlex
    import sys
    return shlex.join([sys.executable, str(Path(s.__file__).with_name("autocode.py")), "capture"])


def context(state, stage, state_path):
    predecessor = None
    if stage in V2_STAGES:
        predecessor = artifacts.verify_predecessor(state, stage, Path(state_path).parent)
    exchange = copy.deepcopy(state.get("planning", {}))
    for entry in exchange.get("reports", {}).values():
        # Current contract is included once. Older full drafts stay retrievable
        # via the artifact path; every concern, response and decision remains in
        # the handoff because later review stages must account for every ID.
        entry["report"].pop("contract", None)
        # Trim verbose fields from older reports to keep the prompt bounded.
        report = entry.get("report") or {}
        for key in ("code_refs", "alternatives", "uncertainties", "summary"):
            if isinstance(report.get(key), list) and len(report[key]) > 5:
                report[key] = report[key][:5]
            elif isinstance(report.get(key), str) and len(report[key]) > 500:
                report[key] = report[key][:500] + "…"
    packet = {"task": state["task"], "workspace": state["workspace"], "state_file": str(state_path),
              "joint_planning": True, "execution_engine": engine_for(state["settings"], route_for(state, stage)),
              "stage": stage,
              "goal_contract": None if stage == "requirements_gather" else state.get("goal_contract"),
              "requirements_handoff": None if stage == "requirements_gather" else state.get("requirements_handoff"),
              "requirements_history": None if stage == "requirements_gather" else [
                  {"output": entry.get("output"),
                   "requirements": [{"id": row["id"], "source_quote": row.get("source_quote", "")}
                                    for row in (entry.get("report") or {}).get("requirements", [])],
                   "conflicts": (entry.get("report") or {}).get("conflicts", [])}
                  for entry in state.get("requirements_history", [])
                  if (entry.get("report") or {}).get("conflicts")],
              "saved_answers": state.get("answers", {}), "brief_feedback": state.get("brief_feedback", []),
               "planning": exchange,
               "budget": f"{review_call_limit(state) or 'Unlimited'} plan-review calls in this cycle, including failed attempts; "
                         "only an explicit operator action can extend the allowance; "
                         "separate one-use AutoResolver operational recovery grants do not reset this allowance",
                "recovery_context": state.get('recovery_context')}
    if predecessor:
        packet["predecessor_artifact"] = predecessor["artifact"]["path"]
        packet["predecessor_delta"] = predecessor["delta"]["path"]
    recovery_instruction = ''
    grants = [grant for grant in exchange.get('recovery_review_grants', [])
              if not grant.get('consumed') and grant.get('binding', {}).get('stage') == stage]
    recovery = packet['recovery_context'] or {}
    if grants or (stage in ('astra_challenge', 'astra_finalize') and recovery.get('stage') == stage):
        try:
            from ..autocode_resolver_runtime import OPERATIONAL_INSTRUCTION
        except ImportError:
            from autocode_resolver_runtime import OPERATIONAL_INSTRUCTION
        packet['resolver_remediation'] = {'receipt_id': grants[0]['id'] if grants else None,
                                           'instruction': OPERATIONAL_INSTRUCTION}
        recovery_instruction = '\n' + OPERATIONAL_INSTRUCTION + '\n'
    if stage in ('astra_challenge', 'astra_finalize') and (grants or packet['recovery_context']):
        packet['workspace_inventory'] = workspace_inventory(state['workspace'], state['task'], limit=20)
    if stage == "requirements_gather":
        packet["requirement_coverage_checklist"] = [
            sentence for source in goals.source_texts(state)
            for sentence in goals.cue_sentences(source)
        ]
    if state["settings"].get("figma_file"):
        packet["figma_file"] = state["settings"]["figma_file"]
    packet['user_events'] = state.get('user_events', [])
    if state.get('design_constraint'):
        packet['approved_design'] = state['design_constraint']
    diagnosis = bug_job.large_correction(state)
    if diagnosis:
        packet['bug_diagnosis'] = diagnosis
    if stage == "requirements_gather":
        packet['previous_requirements_handoff'] = state.get('requirements_handoff')
    if stage in ("requirements_gather", "astra_discovery"):
        packet['workspace_inventory'] = workspace_inventory(state['workspace'], state['task'])
    try:
        from .. import autocode_figma as figma
    except ImportError:
        import autocode_figma as figma
    figma_instruction = figma.instructions(state["settings"])
    planning_policy = "" if stage == "requirements_gather" else (
        goals.DECISION_PROVENANCE + goals.CONTRACT_REFERENCES + s.MILESTONE_POLICY + EVIDENCE_FACTS)
    if stage != "requirements_gather":
        packet["capture_command"] = capture_command()
    clarification_policy = ("" if stage == "astra_challenge" else QUESTION_POLICY) + (
        ASSUMPTION_POLICY if stage == "requirements_gather" else "")
    if stage != "requirements_gather":
        packet["deferred_obligations"] = state.get("deferred_obligations", [])
        packet["clarification_episode"] = state.get("clarification_episode")
        clarification_policy += OBLIGATION_POLICY
    request = state.get("investigation_request")
    if request and request.get("stage") == stage:
        # Correctness must not depend on provider-session memory: the pass gets
        # everything it needs explicitly.
        packet["investigation_request"] = request
        clarification_policy += INVESTIGATION_POLICY
    design_rule = (APPROVED_DESIGN_RULE if state.get('design_constraint') else "") + (BUG_DIAGNOSIS_RULE if diagnosis else "")
    if stage != "requirements_gather":
        design_rule += EXAMPLE_CRITERIA_RULE
    prompt = (PROMPTS[stage] + JOB_TYPE_POLICY + design_rule + recovery_instruction + figma_instruction + planning_policy + clarification_policy + s.COMMON
              + "\nWork read-only; return the report, the runner saves it.\nCURRENT HANDOFF DATA\n"
              + json.dumps(packet, indent=2))
    return prompt, {"estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
                    "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000)}


def prepare(state, stage, state_path, schema_dir):
    from .common import ModelRequest
    if stage == RECOGNIZE:
        state["phase"] = "DISCOVERING"
        role = role_for(state, stage)
        prompt, metrics = workflows.prompt(state, workspace_inventory(state["workspace"], state["task"]),
                                          state["settings"].get("context_soft_tokens", 10000),
                                          engine_for(state["settings"], route_for(state, stage, role)))
        return ModelRequest(role, route_for(state, stage, role), prompt, metrics, workflows.SCHEMA, False)
    if stage not in STAGES + V2_STAGES:
        raise ValueError(f"Autoplanner cannot run {stage}")
    joint = is_planning(state, stage)
    state["phase"] = "PLANNING" if joint else "DISCOVERING"
    try:
        prompt, metrics = context(state, stage, state_path) if joint else s.context_packet(state, stage, state_path)
    except ValueError as error:
        if stage in V2_STAGES:
            raise s.Paused("PAUSED_INVALID_PREDECESSOR", str(error)) from error
        raise
    role = role_for(state, stage)
    return ModelRequest(role, route_for(state, stage, role), prompt, metrics,
                        SCHEMAS[stage] if joint else goals.DISCOVERY_SCHEMA, False)


def recognize(state, value, record):
    """Save the recognized kind of job; the run then continues with its first real stage."""
    workflows.apply(state, value, record)
    state["phase"] = "DISCOVERING"


def apply_result(state, stage, value, record):
    """Compatibility entry; Autopilot consumes the planner result."""
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.apply_planning_result(state, stage, value, record)
