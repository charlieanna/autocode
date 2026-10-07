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
    from .. import autocode_stage_context as stage_context, autocode_acceptance_policy as acceptance_policy
    from .. import autocode_bug_job as bug_job, autocode_workflows as workflows, autocode_test_cases as test_cases
    from .. import autocode_follow_up as follow_up, autocode_adaptive_planning as adaptive, autocode_draft_examples as examples
    from .. import autocode_progressive_state as progressive, autocode_brief_literals as brief_literals
    from .. import autocode_design_plan as design_plan, autocode_brief_obligations as brief_obligations, autocode_risk_obligations as risk_obligations
except ImportError:
    import autocode_acceptance_policy as acceptance_policy
    import autocode_test_cases as test_cases
    import autocode_goals as goals
    import autocode_planning_artifacts as artifacts
    import autocode_support as s
    import autocode_stage_context as stage_context
    import autocode_bug_job as bug_job
    import autocode_follow_up as follow_up
    import autocode_workflows as workflows
    import autocode_adaptive_planning as adaptive
    import autocode_draft_examples as examples
    import autocode_progressive_state as progressive
    import autocode_brief_literals as brief_literals
    import autocode_design_plan as design_plan
    import autocode_brief_obligations as brief_obligations, autocode_risk_obligations as risk_obligations

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
Every plan must uphold its invariant as an acceptance criterion, checked as exactly as the invariant states it
(never "to 2 decimal places" or "within 0.001" when the rule is exact), with a regression test that fails on the
original code and passes after the fix, and must keep the project's existing tests passing. Its test_cases
are those regression tests in plain English: make each one an acceptance criterion quoting its given, when
and then, and require one test per case named test_<id>_<what it checks> (T1 -> test_t1_...); the runner
refuses the fix unless every restore case (the default kind) has such a test that fails on the original code
because of the bug and passes after the fix. A preserve case describes behavior that already works: its test
must pass on the original code and after the fix. Keep each case's kind when planning its verification. Fix the cause,
not the symptom, and do not widen the change beyond what the root cause needs. Do not ask the user what the
fix should achieve; ask only about a genuine choice the diagnosis leaves open.
Cite the diagnosis in code_refs as exactly its note_path; explanations go in summaries, never inside a path.
"""
# A follow-up that acts on an earlier review in the same run (autocode_follow_up) is planned
# from the review's findings; requirements gathering is skipped.
REVIEW_FINDINGS_RULE = """
REVIEW FOLLOW-UP: review_findings in the handoff data are the findings of a review the user asked for earlier
in this conversation, saved in the repository at report_path. The user's request (task) now asks to act on
them, and they are the requirements: plan a fix for each blocking finding, with an acceptance criterion and a
regression test that fails on the reviewed change and passes after the fix. When the reviewed change is a patch
file (change_patch) that is not applied yet, the plan applies it first and fixes the findings on top of it, so
the change the user asked to land keeps everything else it does. Behavior the reviewed change already has and
must keep (what its own tests cover, what no finding says is broken) is a "guard:" criterion, never "test:": it
cannot fail on the reviewed change. The runner proves each regression test itself,
against the base code with change_patch applied (the change the review judged): do not add a criterion or task
to capture the tests failing without the fixes. Leave the advisory findings as they are
unless the request asks for them. Do not ask the user what the findings mean; ask only about a genuine choice
they leave open. Cite the review in code_refs as exactly its report_path.
"""
# Features get the bug-fix proof too: the plan states testable criteria as concrete
# examples marked "test:", and the runner proves each one at its milestone (autocode_test_cases).
# A test: criterion must be new behavior: a live parallel-diamond plan made "the contract package is
# importable" one, which passes before the change (a namespace package), so the milestone could never
# be proven and the run stalled on it (2026-09-29).
# Tests must check behavior, not the repository's file listing: a live port-policy-go plan turned
# "deliver these four files" into a test that failed once its checker built policy.bin (2026-09-29).
EXAMPLE_CRITERIA_RULE = """
TESTS IN PLAIN ENGLISH: write every acceptance criterion a test can check as one concrete example a person can
check without reading code: "Given <the exact starting data or state>, when <the exact action or command>,
then <the exact result, with literal values>". No vague words such as "correctly" or "gracefully". Work each
literal result out from the criterion's own rule (count the items, do the arithmetic), never estimate it. Set its
verification_method to "test: <exact supported test name>" when the request already names the test.
Preserve that name, including a native Go name; do not substitute a criterion-ID alias. Otherwise use
"test: test_<criterion id in lowercase>_<what it checks>" (C2 -> test_c2_...). The Builder writes that test; the runner itself checks that it passes with the change and did not pass before the
run began, and refuses the milestone and completion otherwise. With several milestones, list each test
criterion under the milestone that delivers it: the runner checks a milestone's tests, and those of milestones
already accepted, at that milestone's checkpoint, so a test must not depend on a later milestone. Keep criteria
a test cannot check (documentation, visual design, performance under real load) with an ordinary
verification_method.
A test checks what the program does, never which files the repository contains. Do not write a test that lists
the repository or working directory and asserts which files exist, or that no other file exists: whoever runs the
tests (a build, the runner's own checks, CI, a reviewer) adds files there, so such a test fails on correct code.
Which files are delivered, and that no build output is left behind, is checked by the Validator reading the
repository: give that criterion an ordinary verification_method, not "test:".
A "test:" criterion describes behavior that does not exist before the run, so its test fails (or cannot run) on
the code as it is. Something that already holds, or holds as soon as a directory exists, is not: in Python 3 a
package directory imports without __init__.py, so "the package is importable" passes before the change and the
runner can never prove it. Make the "test:" criteria the behavior the new code adds or fixes (a function's
result, a command's output, a refused input).
A command that must fail (a usage error, a refused input) is checked inside the criterion's test; if a
verification_method does name such a command, end it with "and assert exit N" right after the commands (N/M with
one status per command for several), because the runner replays every command a method names as a check that
must exit 0 unless that declaration says otherwise.
Behavior that already works and must keep working (the change must not break it) is a guard: write it as the
same kind of example, with verification_method "guard: <exact supported test name>" when the request
already names the test; otherwise use "guard: test_<criterion id in lowercase>_<what it checks>".
The runner checks that its test passes both before and after the change. Coverage of behavior the product already
implements — a named scenario, an existing rule, tests added with no product change — is a guard for every
such criterion. A test: criterion cannot be proven by a test-only diff. A guard needs a real behavior to check;
something trivially true (a package that imports, a file that exists) gets an ordinary verification_method.
For independent parallel milestones, use distinct milestone-specific criterion IDs as well as disjoint
affected_paths: the scheduler serializes milestones that share criterion IDs. Scope each criterion to its
own milestone; put cross-component integration checks in a dependent milestone. Do not weaken coverage or
rename protected criteria in an existing contract without the required user-backed change.
TEST COMMAND PREREQUISITES: include every missing package marker required by your validation command in
affected_paths before approval. `python3 -m unittest discover -s tests -t .` needs tests/__init__.py;
assign that file explicitly (or tests/) when it does not exist. Never leave the Builder to expand scope.
ERROR PATHS: inject failures after staged or transactional work begins; verify the public error contract,
unchanged persistent state and complete cleanup across the relevant underlying failure modes.
""" + test_cases.NAMED_PROOF_NOTE
# Two live ladder runs (Claude models, 2026-09-30) approved an example that contradicted its own rule: "2024-02-28
# to 2024-03-01 is 4 dates", and an entry with a TTL of 2**63 still present at time 1e300. Both plan reviews passed
# it, the Builder bent its test to fit, and the run stopped for a person after the build.
EXAMPLE_CHECK_RULE = """
CHECK EVERY WORKED EXAMPLE: recompute the literal result of each acceptance criterion's example from its own rule
and the request: count the items in a range, do the arithmetic, apply the stated expiry, ordering or rounding rule
to the example's inputs. An example whose stated result does not follow is a blocking concern naming the
correct result: no implementation can satisfy both the rule and the example.
"""
# A live greenfield run (2026-10-01, docs/bugs/2026-10-01-reliability-live-cases.md) transcribed the brief's
# literal "ID TEXT [open|done]" into examples without the brackets; the Builder, the tests, the Validator and
# the completion gate then all honestly served the corrupted criteria and the run completed falsely. Every
# other handoff has an independent check; the brief-to-criteria transcription had none. The runner now
# rejects a draft that drops a backticked brief literal (autocode_brief_literals); this rule asks the
# reviewer whether the examples agree with it, which no mechanical check can decide.
BRIEF_TRACE_RULE = """
CHECK EVERY EXAMPLE AGAINST THE BRIEF: re-read the user's brief and re-derive each worked example's literal
result from the brief's own words, not from the criterion next to it. Every literal the brief states — an
exact output format, a field name, an exit code, a file name, an error name — must appear verbatim in at
least one example. An example whose literal drops, normalizes or rewrites what the brief states is a
blocking concern quoting the brief's sentence and the example's deviation: the plan review approves
criteria against the brief, and whatever literal the criteria carry will be built, tested, validated and
completed exactly as written. An example consistent with its own rule but not with the brief is still wrong.
"""
# A live cent-drift plan (2026-09-30) required a 175,712-cart enumeration to "finish in under about 10 seconds". The
# Builder asserted elapsed time, the test took 10.39 s on a loaded machine, and the run stopped after two retries
# with correct billing code: no code change could make the criterion hold.
NO_TIMING_RULE = """
NO TIMING CRITERIA: no acceptance criterion, verification method or test may depend on elapsed time or machine
speed ("finishes in under 10 seconds", a timing assertion, a benchmark threshold). It passes on an idle machine and
fails on a loaded one, and the Builder cannot fix that by fixing code. Bound the work instead: state the size of an
enumeration and keep it to a few thousand cases that run in seconds. A plan reviewer raises a blocking concern for one.
"""
# A design job delivers documents only (autocode_test_cases.design_only), so it gets this instead of the
# example-criteria rule, which made a live design run plan every criterion as a test and add tests/.
DESIGN_DELIVERABLES_RULE = """
DESIGN DELIVERABLES: this job delivers a design, not code. Deliver exactly the files the request names and
nothing else: no application code, no test files, no scripts. Every milestone's affected_paths and the
initial_task's affected_paths list only those files (or their directory). Never mark a verification_method
"test:" or "guard:". Verify each criterion by what the Validator can check directly in the delivered files: read them,
and run read-only commands against them (for example python3 -c that loads a JSON file and checks a field),
without adding any file to the repository.
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
CONTRACT DELTA: contract_changes describes only changes from the current goal_contract revision in this
handoff, not cumulative history. A permission already incorporated into that revision is not a new change:
retain its approved text, cite the saved authorization in the summary, and omit it from contract_changes.
If no protected item changes against the current revision, return contract_changes=[].
Use exact protected item identities: an acceptance criterion ID such as AC1, or the previous verbatim
protected list/permission string. Do not use field labels such as "AC1 verification_method",
"AC8 (new criterion added)", "technical_approach", "M1" or "initial_task" as contract_changes.item.
Allowed draft proof corrections, new criteria and implementation proposal edits need no delta;
return [] for them. This does not authorize changing existing behavior, permissions or approved proofs.
SOURCE CITATIONS: code_refs contains existing repository source paths, optionally :line, never a runner
state file, .autocode/ artifact, cache, or explanatory sentence. state_file is context to read, not source
to cite. Read the workspace_inventory candidates; a citation repair changes citations, not requirements.
SETTLED REQUIREMENTS: preserve literal inputs and outputs from the task, approved design and saved answers.
Create examples that match those literals. The DRAFT EXAMPLE CORRECTIONS rule is the sole exception for
numeric stdout in model-written drafts; other protected criterion changes need a saved user basis and delta.
Verification changes follow the narrow draft-proof policy. Gather remaining decisions before drafting;
do not reopen answered questions or invent extra clarification cycles for report wording.
Keep existing test names and assertions. A planned case needs a separate new test if matching its id
would otherwise require renaming an existing test; a guard must keep the original coverage as well.
"""
REVISION_CONFLICT_RULE = """
PROTECTED REVISION CONFLICTS: outside the narrow allowed draft proof/example corrections, a
reviewer's requested behavior change still needs a saved user answer or feedback event, even in
an unapproved model-written draft. Without that basis, retain the protected text and add a blocking decision
to contract.open_blocking_questions, explaining the conflict and proposed correction in the response/summary.
Set contract.initial_task.kind=none while blocked; do not claim the conflict is resolved or the plan ready.
Do not use agent_proposed or original_request as authorization for a protected revision, or invent a user event.
Allowed proof-only corrections do not require a new question.
"""
RESPONSE_EVIDENCE_RULE = """
Each responses[].evidence_refs must be nonempty and cite evidence actually investigated for that response.
Top-level code_refs does not satisfy this per-response requirement. Use existing source paths you read,
explain their relevance in response, and never invent citations or cite not-yet-created implementation files.
"""
# A live review-then-fix plan (2026-09-29) marked "the diff touches only the two fixes" for human
# review although its own verification method was "Validator reads git diff"; the run then
# stopped for an approval nobody needed.
HUMAN_REVIEW_NOTE = ("true only when nothing the Validator can run or read settles the criterion: a visual, "
                     "audible or subjective judgement that needs a person. A criterion checked from the diff, "
                     "tests, command receipts or files is false. Every true criterion stops the run for the "
                     "user's approval before it can complete.")


CONTRACT_FIELDS_RULE = """
CONTRACT LISTS: when open_blocking_questions is empty, the runner refuses a contract whose deliverables,
required_behaviors or permission_boundaries is an empty list, and the report is sent back for repair. Give each at
least one entry: deliverables are the files or artifacts produced; required_behaviors is what the finished work must
do; permission_boundaries is what it may and may not touch (for example "Edit only pager/ and tests/; no network; no
writes outside the workspace"). important_failure_cases, scope_exclusions and constraints may be empty when there is
nothing to say: do not invent entries. While open_blocking_questions is non-empty, empty lists are allowed.
HUMAN REVIEW: an acceptance criterion's human_review is """ + HUMAN_REVIEW_NOTE + "\n"
# Planner reports were sent back for repair with "Planner dropped requirements with no trace" in
# several live runs (Claude models, 2026-09-29): the report's requirement_trace was [] although the
# handoff listed R1..Rn, buried in requirements_handoff. The stages that must trace them get the
# IDs as a short list (requirement_trace_rows) and this rule; the runner's check is unchanged.
REQUIREMENT_TRACE_RULE = """
REQUIREMENT TRACE: requirement_trace_rows in the handoff data lists every requirement from the requirements
handoff, and any feedback on a plan the user was shown that no Requirements report has read yet (its
requirement_id is the feedback event ID). requirement_trace must contain exactly one row for each of those
requirement_id values, no more and no fewer; an empty requirement_trace is refused. disposition is covered,
excluded or superseded. For covered, evidence is an acceptance criterion ID of this contract (for example "AC3",
or "AC3 checks this"), or a required_behaviors entry copied exactly; a paraphrase is refused. For excluded,
evidence is a scope_exclusions entry copied exactly and backed by a saved user answer; for superseded, it cites
the saved answer or feedback event ID. While the draft has open_blocking_questions and no criteria yet, a covered
row may say what it waits on (for example "pending Q1"); the next draft, after the answer, must cite criteria.
"""
TRACE_STAGES = ("astra_discovery", "glm_revise", "astra_finalize")


def traces_coverage(contract):
    """Whether a planner report's covered trace rows must cite this contract's criteria or behaviors.

    Not while the draft still has open_blocking_questions: such a draft may have no acceptance criteria yet
    (a clarification-only draft), so "covered" can only mean pending the answer. Every live plan that opened
    with a question paid a report repair for that (feature-refund-window, 2026-09-29). The trace must still
    list every requirement exactly once, exclusions and supersessions still need a saved user event, the
    draft cannot be approved, and the next draft, written after the answer, is checked in full.
    """
    return not (contract or {}).get("open_blocking_questions")


def trace_rows(state, stage):
    """The requirements a stage's requirement_trace must cover, one row each; [] when there are none."""
    if stage not in (*TRACE_STAGES, "astra_challenge"):
        return []
    handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    # Feedback on a shown plan that no Requirements report has read yet is traced like a requirement.
    return [{"requirement_id": row["id"], "requirement": row.get("text", ""),
             "source_quote": row.get("source_quote", "")}
            for row in (handoff.get("requirements") or []) + adaptive.feedback_requirements(state)
            if isinstance(row, dict) and row.get("id")]


# A late question (the final review returned an unresolved concern to the user, usually permission to change
# a protected criterion) restarts planning after the answer, and the next review saw only the new draft:
# 11 of 31 answer-driven re-drafts in live runs (2026-09-28..10-01) reviewed a whole plan again from scratch.
REREVIEW_RULE = """
RE-REVIEW AFTER THE USER'S ANSWERS. Your previous review of this plan ended with questions to the user.
previous_review holds your earlier concerns, your final decisions on them, and each question with the user's
answer. Check that this draft applies those answers exactly as given. A concern you resolved before stays
resolved unless an answer or this draft reopens it; do not raise it again. Mark a concern blocking only for what
the answers changed or for what is still wrong in this draft.
"""


def previous_review(state):
    """The last planning cycle's review when it ended in questions the user has since answered, so the next
    review checks the answers instead of reviewing the plan from scratch; None otherwise."""
    history = state.get("planning_history") or []
    reports = ((history[-1] if history else None) or {}).get("reports") or {}
    last = next((reports[stage]["report"] for stage in ("astra_finalize", "glm_revise")
                 if (reports.get(stage) or {}).get("report")), {})
    questions = (last.get("contract") or {}).get("open_blocking_questions") or []
    answers = state.get("answers") or {}
    if not questions or any(question.get("id") not in answers for question in questions):
        return None
    concerns = ((reports.get("astra_challenge") or {}).get("report") or {}).get("concerns") or []
    return {"concerns": [{key: row.get(key) for key in ("id", "concern", "blocking")} for row in concerns],
            "decisions": [{key: row.get(key) for key in ("concern_id", "decision", "resolved")}
                          for row in last.get("decisions") or []],
            "answered_questions": [{"id": question["id"], "question": question.get("question", ""),
                                    "answer": answers[question["id"]].get("text", "")} for question in questions]}


def fill_trace_id(state, stage, value):
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
    if (len(set(ids)) != len(ids) or len(trace) != len(ids)
            or any(not isinstance(rid, str) or rid not in ids for rid in assigned)
            or len(set(assigned)) != len(assigned)):
        return value
    untraced = [rid for rid in ids if rid not in assigned]
    if len(unnamed) != 1 or len(untraced) != 1:
        return value
    trace = [dict(row) for row in trace]
    trace[unnamed[0]]["requirement_id"] = untraced[0]
    return {**value, "requirement_trace": trace}


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
# Only a draft example correction carries a receipt. Generation schemas require every field, so a change
# that is not one says null; as a plain object field GLM 5.3's null failed every report declaring a
# contract change (2026-10-02). Readers treat anything but an object as no correction.
CHANGE["properties"]["example_correction"] = {**examples.RECEIPT_SCHEMA, "type": ["object", "null"]}
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
# Only independent reviewers may propose executable observations; the runner seals their provenance.
BRIEF_OBSERVATION_CHANGE = obj({"previous_hash": S, "declaration_id": S, "source_event_id": S})
for _stage in ("astra_challenge", "astra_finalize", "plan_finalize"):
    SCHEMAS[_stage]["properties"]["brief_observations"] = brief_obligations.PROPOSALS_SCHEMA
    SCHEMAS[_stage]["properties"]["risk_observations"] = risk_obligations.PROPOSALS_SCHEMA
    SCHEMAS[_stage]["properties"]["risk_observation_changes"] = {"type": "array", "items": BRIEF_OBSERVATION_CHANGE}
    SCHEMAS[_stage]["properties"]["brief_observation_changes"] = {
        "type": "array", "items": BRIEF_OBSERVATION_CHANGE}

# Live planning reports were rejected, each costing a report repair, for a contract whose deliverables,
# required_behaviors or permission_boundaries was an empty list (VALIDATION.md 2026-09-26; two Claude-model
# trials, 2026-09-29). The field was present, so requiring it changes nothing, and a hard minItems would refuse a
# draft that still has open_blocking_questions, where empty lists are legitimate. The model is told what each
# list is for, in the schema it is given and in CONTRACT_FIELDS_RULE. Descriptions do not affect validation.
CONTRACT_FIELD_NOTES = {
    "deliverables": "The files or artifacts the work produces. At least one unless open_blocking_questions is non-empty.",
    "required_behaviors": "What the finished work must do. At least one unless open_blocking_questions is non-empty.",
    "permission_boundaries": "What the work may and may not touch, for example: Edit only pager/ and tests/; no "
                             "network; no writes outside the workspace. At least one unless open_blocking_questions "
                             "is non-empty.",
    "important_failure_cases": "Failure cases that matter. May be empty; do not invent entries.",
    "scope_exclusions": "Work that is explicitly out of scope. May be empty; do not invent entries.",
    "constraints": "Constraints the user or the project imposes. May be empty; do not invent entries.",
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
# Optional progressive proposal for goals that only succeed as several useful
# end-to-end slices. The runner validates it, generates the plan-card disclosure
# from it and seals it at ordinary approval; a report without one keeps the
# ordinary path. Old saved reports remain valid.
PROGRESSIVE_CHECK = obj({"id": S, "method": S,
                         "relation": {"type": "string", "enum": ["contributes_to", "fully_verify"]},
                         "criterion_ids": SS})
PROGRESSIVE_SLICE = obj({"id": S, "intended_result": S, "criterion_ids": SS, "paths": SS, "depends_on": SS,
                         "checks": {"type": "array", "items": PROGRESSIVE_CHECK},
                         "tentative": {"type": "boolean"}})
PROGRESSIVE_PROPOSAL = obj({"version": {"type": "integer"}, "needed_because": S, "shared_decisions": SS,
                            "outstanding_criteria": SS, "done_slices": SS,
                            "slices": {"type": "array", "items": PROGRESSIVE_SLICE}})
for _stage in ("astra_discovery", "glm_revise", "astra_finalize", "plan", "plan_revise", "plan_finalize"):
    SCHEMAS[_stage]["properties"]["progressive_proposal"] = PROGRESSIVE_PROPOSAL


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
# Astra is too expensive and only for the Resolver (user 2026-09-28).
PINNED_REVIEWER_MODEL = "openai/gpt-6-sol"


def start(state):
    """Begin (or restart) planning: archive the previous planning record and route to plan review."""
    try:
        from .. import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    if state.get("planning"):
        state.setdefault("planning_history", []).append(copy.deepcopy(state["planning"]))
    state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
    saved_review_limit = state.get('settings', {}).get('planning_review_call_limit')
    if type(saved_review_limit) is int and saved_review_limit == 0:
        state['planning'].update(review_call_limit=0, review_call_limit_origin='user_explicit')
    state.pop(human.PRIVATE, None)
    state.pop(human.PUBLIC, None)
    state.pop("user_request", None)
    next_stage = "plan_review" if state.get("settings", {}).get("planning_flow") == "v2" else "astra_challenge"
    state.update(status="RUNNING", phase="PLANNING", next_stage=next_stage, pending_questions=[])


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
    progressive.set_explicit_limits(state, review_calls=limit)
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
    if progressive.charge_review(state, stage, record):
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
""" + RESPONSE_EVIDENCE_RULE,
    "astra_finalize": """You are the independent Plan Reviewer, making the final planning decision (final review stage).
Settle EVERY concern by ID using the Planner's evidence-backed responses and source inspection as needed.
Confirm that milestone dependencies are complete and acyclic, and that [] is used only
for genuinely independent work. Do not schedule or launch milestones.
Return the proposed final contract and concise decisions/rationales/tests. Include contract.initial_task
inside contract, never at the report root: objective, affected_paths, kind (implement or validate), milestone_id,
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


def obligation_policy(stage):
    """Request only obligation fields this planning stage can return."""
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


PROGRESSIVE_POLICY = """
When revising a previously approved progressive product goal, retain prior check obligations by
default. A removal requires an exact visible scope_exclusions string:
'Progressive check retirement: ' + canonical JSON {check_id,check_hash,removes}, with sorted keys
and separators (',',':'). check_hash is the old check definition identity; removes must name an
exact old required behavior or criterion text removed from the revised product. Never retire an
unrelated check, infer removal from changed IDs, or claim a model approval. Ordinary independent
review and explicit user approval of the new goal token are required before retirement takes effect.
The runner mirrors the exact scope_exclusions retirement declaration into visible constraints
before independent review and user approval; retain that exact line, never a conflicting mirror.
PROGRESSIVE PLANNING (optional). When the goal only succeeds as several genuinely useful end-to-end
slices, propose a progressive plan instead of one long build: progressive_proposal
{version: 1, needed_because, shared_decisions, outstanding_criteria, done_slices, slices}. The report
schema always includes progressive_proposal; when the goal does not need progressive planning return
its empty form (version 0, empty strings and lists, no slices), which means no proposal. Propose
it only when those slices and their boundaries can be stated from the requirements and repository
evidence; never for a small or tightly coupled task, and never to paper over an ambiguous outcome
(clarify that instead). The product outcome stays fixed: slices deliver it progressively.
For a version 1 proposal, contract.milestones must contain exactly one whole-product milestone,
not one milestone per slice and not an extra final-checkpoint milestone. That milestone has
depends_on=[], all product acceptance criterion IDs, and affected_paths covering every slice's paths.
Delivery order belongs in progressive_proposal.slices and their depends_on edges, not extra milestones.
contract.initial_task executes only slices[0], using the whole-product milestone's ID but only the
first slice's objective, paths, criteria and checks. One product milestone does not mean one long task.
Every acceptance criterion ID must be planned on at least one slice or listed in outstanding_criteria;
a revision may split, reorder or replace future slices but may never drop a criterion from that map.
slices[0] is the first slice: the main user journey across the essential layers, with an observable
useful result, bounded writable paths (paths), the product criteria it touches (criterion_ids) and
nonempty checks and tentative: false. All later slices, including any whole-product verification
slice, are marked tentative: true: not dispatchable until a reviewed slice
revision promotes them. Investigation or setup work may be tasks inside a slice, but is never
reported as delivery.
A check is {id, method, relation, criterion_ids}: relation is contributes_to (the slice demonstrates
part of the criterion; the criterion stays open) or fully_verify (this proof can establish the
criterion). In every slice, each check's criterion_ids must be a subset of that slice's criterion_ids;
a full-suite command does not authorize references to criteria absent from the slice.
method must contain an explicit supported command the runner can replay at the
checkpoint, for example `python -m pytest tests/test_journey.py -q`; prose that merely describes
verification is refused, and the command must use repository source or fixtures, never run/session
state. The commands need not pass before the slice is built.
initial_task.validation_plan must contain plain executable commands copied from slices[0].checks,
not prose describing test preparation, implementation or inspection. For example, use
"python3 -m unittest test_journey.Skeleton" as an entry, not "Add tests and capture ...".
Every initial-task command must be covered by a reviewed first-slice check; future-slice commands
are not authorized. A prose-only validation entry is refused before approval can activate a slice.
The runner generates the plan-card disclosure from your proposal into constraints and
technical_approach (the delegation, its limits and the slice sequence). Never write lines starting
"Progressive delegation:", "Progressive slice:" or "Product criteria explicitly outstanding:";
mismatched hand-written disclosure is refused. Initial approval delegates continuation within the
agreed outcome, constraints and permissions; product changes, new permissions and unresolved product
decisions still return to the user, and every slice still gets independent plan review and
verification.
"""


def repair_rules(stage, schema):
    """Repeat planning semantics only for fields allowed by the saved repair schema."""
    fields = schema.get("properties", {})
    if stage not in TRACE_STAGES or "contract" not in fields:
        return ""
    return (REVISION_CONFLICT_RULE
            + (RESPONSE_EVIDENCE_RULE if "responses" in fields else "")
            + (PROGRESSIVE_POLICY if "progressive_proposal" in fields else ""))


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
    """The command execution stages are given (autocode_stage_context.context_packet), shown to planning too."""
    import shlex
    import sys
    return shlex.join([sys.executable, str(Path(s.__file__).with_name("autocode.py")), "capture"])


BRIEF_OBSERVATION_REVIEW_RULE = """
BRIEF DECLARATIONS. brief_declaration_inventory comes from the user's authenticated brief.
Check every supported declaration against the plan. When this report schema includes
brief_observations, cover every supported declaration with an observation: declaration_id,
criterion_ids from the contract, steps with argv, observe_step, and bindings with placeholder,
step and argument. Propose commands that exercise the declared program and match its literal arguments and output;
do not weaken an expectation or invent an observation the brief does not support. Reuse the
existing observation for an unchanged declaration. Changes require brief_observation_changes
entries naming previous_hash, declaration_id and the authenticated source_event_id that
changes that declaration. A concern-only review reports any coverage gap as a blocking
concern; the final reviewer supplies the complete observations before user approval.
"""
BRIEF_ACCEPTANCE_CARRY_RULE = """
brief_acceptance in a contract is runner-owned. Omit it from model reports; the runner
carries the existing value unchanged. Never create or edit its source, expectation,
observation, or reviewer provenance fields. Planner
stages cannot author brief_observations or brief_observation_changes. The independent Plan
Reviewer proposes observations in those report fields; the runner seals their binding.
"""


RISK_OBSERVATION_REVIEW_RULE = """
LIFECYCLE DECLARATIONS. risk_declaration_inventory derives explicit durability and
fencing promises from authenticated human source and initial public modules.
When the schema includes risk_observations, cover each supported declaration with
{declaration_id, criterion_ids, module}. Select an allowed original public module;
never invent expected values, API aliases, scripts or protocol schedules. Missing
source facts are blocking concerns, not permission to assume a stronger contract.
Reuse unchanged observations. A genuine later human amendment needs an exact
risk_observation_changes entry with previous_hash, declaration_id, source_event_id.
The runner executes a fixed bounded process-lifecycle protocol in clean replay;
ordinary unit tests or callback exceptions cannot replace actual process death.
risk_acceptance is runner-owned: omit it from model contracts, never create/edit it.
"""


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
    if stage in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize"):
        packet["brief_declaration_inventory"] = brief_obligations.inventory(state)
        packet["risk_declaration_inventory"] = risk_obligations.inventory(state)
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
            sentence for source in goals.scan_texts(state)
            for sentence in goals.cue_sentences(source)
        ]
    rows = trace_rows(state, stage)
    if rows:
        packet["requirement_trace_rows"] = rows
    earlier = previous_review(state) if stage == "astra_challenge" else None
    if earlier:
        packet["previous_review"] = earlier
    if state["settings"].get("figma_file"):
        packet["figma_file"] = state["settings"]["figma_file"]
    packet['user_events'] = state.get('user_events', [])
    if state.get('design_constraint'):
        packet['approved_design'] = state['design_constraint']
    diagnosis = bug_job.large_correction(state)
    if diagnosis:
        packet['bug_diagnosis'] = diagnosis
    findings = follow_up.review_findings(state)
    if findings:
        packet['review_findings'] = findings
    if stage == "requirements_gather":
        packet['previous_requirements_handoff'] = state.get('requirements_handoff')
    if stage in ("requirements_gather", "astra_discovery"):
        packet['workspace_inventory'] = workspace_inventory(state['workspace'], state['task'])
    try:
        from .. import autocode_figma as figma, autocode_design_manifest as design_manifest
    except ImportError:
        import autocode_figma as figma, autocode_design_manifest as design_manifest
    figma_instruction = figma.instructions(state["settings"])
    manifest_context = design_manifest.context(state["settings"])
    if manifest_context:
        packet["design_manifest"] = manifest_context
        figma_instruction += design_manifest.INSTRUCTION
    planning_policy = "" if stage == "requirements_gather" else (
        goals.DECISION_PROVENANCE + goals.CONTRACT_REFERENCES + examples.RULE + s.MILESTONE_POLICY + EVIDENCE_FACTS + REVISION_CONFLICT_RULE
        + ("" if stage in ("astra_challenge", "plan_review") else CONTRACT_FIELDS_RULE))
    progressive_policy = PROGRESSIVE_POLICY if stage in ("astra_discovery", "glm_revise", "astra_challenge",
                                                         "astra_finalize") else ""
    if stage != "requirements_gather":
        packet["capture_command"] = capture_command()
    clarification_policy = ("" if stage == "astra_challenge" else QUESTION_POLICY) + (
        ASSUMPTION_POLICY if stage == "requirements_gather" else "")
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
    design_rule = (APPROVED_DESIGN_RULE if state.get('design_constraint') else "") + (BUG_DIAGNOSIS_RULE if diagnosis else "")
    design_rule += REVIEW_FINDINGS_RULE if findings else ""
    if stage != "requirements_gather":
        design_rule += DESIGN_DELIVERABLES_RULE if test_cases.design_only(state) else EXAMPLE_CRITERIA_RULE
        design_rule += EXAMPLE_CHECK_RULE if stage in ("astra_challenge", "astra_finalize") else ""
        design_rule += BRIEF_TRACE_RULE if stage in ("astra_challenge", "astra_finalize") else ""
        design_rule += NO_TIMING_RULE
    design_rule += acceptance_policy.COVERAGE
    if stage != "requirements_gather" and not test_cases.design_only(state):
        design_rule += acceptance_policy.DOMAIN
    if rows and stage in TRACE_STAGES:
        design_rule += REQUIREMENT_TRACE_RULE
    literals = brief_literals.literals(goals.scan_texts(state)) if stage in TRACE_STAGES else []
    design_rule += brief_literals.rule(literals) if literals else ""
    if stage != "requirements_gather":
        design_rule += BRIEF_ACCEPTANCE_CARRY_RULE
    if packet.get("brief_declaration_inventory"):
        design_rule += BRIEF_OBSERVATION_REVIEW_RULE
    if packet.get("risk_declaration_inventory"):
        design_rule += RISK_OBSERVATION_REVIEW_RULE
    design_rule += adaptive.prompt_rule(state, stage) + (REREVIEW_RULE if earlier else "")
    prompt = (PROMPTS[stage] + JOB_TYPE_POLICY + design_rule + recovery_instruction + figma_instruction + planning_policy + clarification_policy + progressive_policy + s.COMMON
              + "\nWork read-only; return the report, the runner saves it.\nCURRENT HANDOFF DATA\n"
              + json.dumps(packet, indent=2))
    return prompt, {"estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
                    "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000)}


def _model_report_schema(schema, state):
    """Protected contract provenance is retained by the runner, never generated by a model."""
    result = copy.deepcopy(schema)
    for field in ("contract", "requirements"):
        body = result.get("properties", {}).get(field, {})
        for protected in ("brief_acceptance", "risk_acceptance"):
            body.get("properties", {}).pop(protected, None)
            if protected in body.get("required", []):
                body["required"].remove(protected)
    if not brief_obligations.inventory(state) and not (
            (state.get('goal_contract') or {}).get('body') or {}).get(brief_obligations.KEY):
        # Strict model output schemas require every included property. Unrelated
        # tasks retain their existing report protocol; no empty feature fields.
        for field in ('brief_observations', 'brief_observation_changes'):
            result.get('properties', {}).pop(field, None)
            if field in result.get('required', []):
                result['required'].remove(field)
    if not risk_obligations.inventory(state) and not (
            (state.get('goal_contract') or {}).get('body') or {}).get(risk_obligations.KEY):
        for field in ('risk_observations', 'risk_observation_changes'):
            result.get('properties', {}).pop(field, None)
            if field in result.get('required', []):
                result['required'].remove(field)
    return result


def prepare(state, stage, state_path, schema_dir):
    from .common import ModelRequest
    if progressive.revision_pending(state):
        transition = progressive.view(state)["transition"]
        schema = (obj({"summary": S, "progressive_proposal": PROGRESSIVE_PROPOSAL,
                       "initial_task": goals.PLANNING_BODY_SCHEMA["properties"]["initial_task"]})
                  if transition["phase"] == "detail" else obj({"summary": S, "accepted": {"type": "boolean"},
                      "product_changes": {"type": "boolean"}, "permission_changes": {"type": "boolean"},
                      "unresolved_product_decisions": {"type": "boolean"}}))
        packet = {"goal_contract": state["goal_contract"], "progressive": progressive.context(state),
                  "progressive_revision": copy.deepcopy(transition), "previous_plan": progressive.view(state)["plan"],
                  "stage": stage, "task": state["task"], "workspace": state["workspace"],
                  "current_task": state.get("current_task"), "saved_answers": state.get("answers", {})}
        prompt = ("Detail/review the next useful slice within the unchanged approved product contract. "
                  "Do not implement or replace the contract. Retain done_slices and cumulative obligations. "
                  "The Planner returns a concrete first slice plus its initial_task; the independent Reviewer "
                  "must inspect the exact persisted candidate and accept only in-bounds technical changes. "
                  "Product/permission changes or unresolved product decisions cannot be automatically activated.\nCURRENT HANDOFF DATA\n"
                  + json.dumps(packet, indent=2))
        role = role_for(state, stage)
        return ModelRequest(role, route_for(state, stage, role), prompt,
            {"estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
             "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000)}, schema, False)
    if stage == RECOGNIZE:
        state["phase"] = "DISCOVERING"
        role = role_for(state, stage)
        prompt, metrics = workflows.prompt(state, workspace_inventory(state["workspace"], state["task"]),
                                          state["settings"].get("context_soft_tokens", 10000),
                                          engine_for(state["settings"], route_for(state, stage, role)))
        return ModelRequest(role, route_for(state, stage, role), prompt, metrics, schema_for(state, stage), False)
    if stage not in STAGES + V2_STAGES:
        raise ValueError(f"Autoplanner cannot run {stage}")
    joint = is_planning(state, stage)
    state["phase"] = "PLANNING" if joint else "DISCOVERING"
    try:
        prompt, metrics = context(state, stage, state_path) if joint else stage_context.context_packet(state, stage, state_path)
    except ValueError as error:
        if stage in V2_STAGES:
            raise s.Paused("PAUSED_INVALID_PREDECESSOR", str(error)) from error
        raise
    if not joint:
        prompt = BRIEF_ACCEPTANCE_CARRY_RULE + prompt
        metrics = {**metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4}
    role = role_for(state, stage)
    schema = (schema_for(state, stage) if joint else design_plan.report_schema(
        goals.DISCOVERY_SCHEMA, (state.get("settings") or {}).get("design_manifest")))
    return ModelRequest(role, route_for(state, stage, role), prompt, metrics,
                        _model_report_schema(schema, state), False)


def schema_for(state, stage):
    """The report schema for a planning stage in this run (adaptive runs extend two of them)."""
    if stage == RECOGNIZE:
        return adaptive.recognizer_schema(state, SCHEMAS[stage])
    return design_plan.report_schema(adaptive.report_schema(state, stage, SCHEMAS[stage], goals.PLANNING_BODY_SCHEMA),
                                     (state.get("settings") or {}).get("design_manifest"))


def after_challenge(state, value, record):
    """Where the first review leads. In an adaptive run, a review with no blocking concern approves
    the Planner's draft as the final plan (autocode_adaptive_planning); otherwise the Planner revises."""
    if not adaptive.enabled(state):
        state["next_stage"] = "glm_revise"
        return
    planning, contract = state["planning"], state["goal_contract"]
    if "adaptive" not in planning:
        planning["adaptive"] = {**adaptive.plan_size(contract["body"]), "approved_at": None, "challenges": 0}
        if planning.get("review_call_limit_origin") != "user_explicit":
            planning["review_call_limit"] = adaptive.review_limit(planning["adaptive"]["size"], review_call_limit(state))
        planning["adaptive"]["review_limit"] = review_call_limit(state)
    planning["adaptive"]["challenges"] += 1
    if (adaptive.blocking(value["concerns"]) or not adaptive.approvable(contract["body"])
            or progressive.view(state).get("candidate")):
        # A progressive delegation is authorized by an accepted revision and final
        # independent review. A challenge cannot supply that approval evidence.
        state["next_stage"] = "glm_revise"
        return
    try:
        from .. import autocode_goal_lifecycle as lifecycle
    except ImportError:
        import autocode_goal_lifecycle as lifecycle
    # The same path a final review takes: install the approved body and queue the user's approval.
    body = brief_obligations.reviewed_body(state, contract["body"],
        value.get("brief_observations") or [], record, changes=value.get("brief_observation_changes") or [])
    body = risk_obligations.reviewed_body(state, body, value.get("risk_observations") or [],
        record, changes=value.get("risk_observation_changes") or [])
    lifecycle.install_draft(state, body, origin="adaptive_review_approval", record=record)
    planning["final_token"] = goals.token(state["goal_contract"])
    planning["adaptive"].update(approved_at=f"astra_challenge#{planning['adaptive']['challenges']}",
                                final_stage="astra_challenge")


def rerun_requirements(state, value):
    """Whether the Planner sent feedback on the shown plan back to Requirements instead of revising the plan
    (adaptive planning). Its draft is discarded and the Requirements stage, which reads every saved feedback,
    runs next; the pipeline then continues in full, as it would without adaptive planning."""
    reason = adaptive.requirements_rerun(state, value)
    if reason:
        state.update(status="RUNNING", phase="DISCOVERING", next_stage="requirements_gather",
                     discovery_summary="Planner: " + reason)
    return bool(reason)


def after_revise(state):
    """After a revision: the final review, or in an adaptive run another first-style review while budget allows."""
    if not adaptive.enabled(state) or progressive.view(state).get("candidate"):
        return "astra_finalize"
    planning = state["planning"]
    return adaptive.after_revise(review_call_limit(state), planning["astra_calls"],
                                 (planning.get("adaptive") or {}).get("challenges", 0))


def recognize(state, value, record):
    """Save the recognized kind of job; the run then continues with its first real stage."""
    workflows.apply(state, value, record)
    state["phase"] = "DISCOVERING"
