"""The compact planning path for a reproduced, localized bug fix (#2021, the first #1867 checkpoint).

A bug-fix run plans like this: Requirements, the Investigator (``investigate_bug``), the Planner's draft
(``astra_discovery``, a paid call), the Plan Reviewer (``astra_challenge``), the person's approval of the
displayed plan, then the Builder, Tester and Completion Reviewer. On the compact path the runner writes the
draft itself from two bound records it already holds, the Investigator's reproduced diagnosis
(``state["investigation"]``) and the Requirements record (``requirements_handoff``), and the Plan Reviewer
reviews that draft. Nothing after the review changes: a clean review goes to the person's exact-token
approval through the adaptive early approval (units.autoplanner.after_challenge), a blocking concern sends
the run to the full path (the Planner's draft, with the concern in its packet: fall_back), and the Builder,
the regression proof, the Tester and the Completion Reviewer run as on every path. The draft is never
approved by a policy: autocode_workflows.POLICY_ORIGINS does not know its origin.

The path is taken only when every trusted signal admits it (decide): the new run enables compact eligibility
(by default with joint planning, disabled with ``--no-compact-bugfix-planning``), it plans adaptively, Requirements named the bug-fix job, the
diagnosis is reproduced by a probe the runner ran, the Investigator sized the fix small, it names a root
cause, an invariant and regression cases, its files are few (PATH_BOUND) and none matches the project's
``sensitive-paths`` (autocode_sensitive_paths; shipped defaults when the project declares none),
as does a draft the contract validators refuse. A model's word (``fix_size``) can only keep the full path,
never grant the compact one. The decision, its reasons and the hashes it rests on are recorded once under
``state["workflow"]["stage_policy"]`` (autocode_stage_policy) and shown by the status view as ``stage_policy``;
the record grants nothing.

Domain layer: imports the diagnosis helpers, the contract lifecycle and the planning cycle; nothing
imports it from below, and no controller or client reads anything but view().
"""

from __future__ import annotations

import argparse
import copy
import re
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_brief_obligations as obligations
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_planning_cycle as planning_cycle
    from . import autocode_prompts as prompts
    from . import autocode_run_status as run_status
    from . import autocode_scope_question as scope_question
    from . import autocode_sensitive_paths as sensitive_paths
    from . import autocode_source_trace as source_trace
    from . import autocode_stage_policy as stage_policy
    from . import autocode_test_cases as test_cases
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_brief_obligations as obligations
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_planning_cycle as planning_cycle
    import autocode_prompts as prompts
    import autocode_run_status as run_status
    import autocode_scope_question as scope_question
    import autocode_sensitive_paths as sensitive_paths
    import autocode_source_trace as source_trace
    import autocode_stage_policy as stage_policy
    import autocode_test_cases as test_cases
    import autocode_workflows as workflows

FLAG = "--compact-bugfix-planning"
SETTING = "compact_bugfix_planning"
ORIGIN = "bugfix_compact_draft"
# The Investigator's affected and test paths together, at most; the diagnosis note is not counted.
PATH_BOUND = 4
FALLBACK_RULE = prompts.get("fragments/autoplanner/compact-fallback-rule.md")


def add_argument(parser) -> None:
    parser.add_argument(
        FLAG,
        action=argparse.BooleanOptionalAction,
        default=None,
        help="New runs: for a bug the Investigator reproduces, sizes small and localizes to files the project's "
        "sensitive-paths do not name, the runner writes the plan draft from the diagnosis and the Requirements "
        "record instead of the Planner's paid draft call; the Plan Reviewer reviews it and you approve it as "
        "usual (docs/adaptive-planning.md#compact-planning-for-bug-fixes). On by default with joint planning",
    )


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def new_run_setting(requested, joint: bool) -> bool:
    """New joint runs default to compact eligibility; an explicit opt-in requires joint planning."""
    if requested and not joint:
        raise ValueError(f"{FLAG} needs joint planning")
    return joint if requested is None else bool(requested)


def resume_refused(saved_settings: dict, requested) -> bool:
    """Whether the flag on a resumed run would change it: the choice is made when the run starts."""
    if requested is None:
        return False
    return bool(requested) != bool(saved_settings.get(SETTING))


def reviewing(state: RunState) -> bool:
    """Whether the draft under review is the runner's compact draft."""
    return (state.get("goal_contract") or {}).get("origin") == ORIGIN


def decide(state: RunState) -> dict:
    """The path this run takes and why: {"path": "compact" | "full", "reasons": [...], "basis": {...}}.

    Every reason names a signal that kept the full path; an empty list admits the compact one."""
    settings = state.get("settings") or {}
    workflow = state.get("workflow") or {}
    found = test_cases.current_diagnosis(state)
    handoff = state.get("requirements_handoff") or {}
    reasons = []
    if found.get("questions") or state.get("pending_questions") or scope_question.question(state):
        reasons.append("the diagnosis still needs a human decision")
    if not enabled(state):
        reasons.append(f"the run did not opt in ({FLAG})")
    if not adaptive.enabled(state):
        reasons.append("the run does not plan adaptively, so no first review can approve a draft")
    if workflow.get("kind") != "bugfix":
        reasons.append("the job is not a bug fix")
    elif workflow.get("named_by") != workflows.REQUIREMENTS_STAGE:
        reasons.append("Requirements did not name the job (a pinned --workflow has no Requirements record)")
    if not (handoff.get("report") or {}).get("intended_outcome"):
        reasons.append("there is no Requirements record to plan from")
    # The compiler binds diagnosis cases, not arbitrary requested test identifiers. Let the Planner
    # bind those explicitly instead of paying a compact review that can only reject the omission.
    if any(
        re.search(r"\b(?:test_[A-Za-z0-9_]+|Test[A-Z][A-Za-z0-9_]*)(?![\w]|\.py\b)", row["text"])
        for row in source_trace.quotable(obligations.sources(state))
    ):
        reasons.append("requested test names need explicit plan bindings")
    if found.get("outcome") != "reproduced":
        reasons.append("the Investigator did not reproduce the bug")
    else:
        if not found.get("probe_result"):
            reasons.append("the reproduction was not shown by a probe the runner ran")
        if found.get("fix_size") != "small":
            reasons.append(f"the Investigator sized the fix {found.get('fix_size') or 'unknown'!s}, not small")
        if not str(found.get("root_cause") or "").strip() or not str(found.get("invariant") or "").strip():
            reasons.append("the diagnosis names no root cause or no invariant")
        if not found.get("test_cases"):
            reasons.append("the diagnosis has no regression case to prove the fix by")
        paths = _paths(found)
        if not found.get("affected_paths"):
            reasons.append("the diagnosis names no affected path")
        elif len(paths) > PATH_BOUND:
            reasons.append(f"the fix touches {len(paths)} paths, more than {PATH_BOUND}")
        patterns = settings.get(sensitive_paths.SETTING)
        if patterns is None:
            reasons.append(f"the project declares no {sensitive_paths.KEY}, so every path counts as sensitive")
        else:
            sensitive = sensitive_paths.matched([*paths, found.get("note_path") or ""], patterns)
            if sensitive:
                reasons.append("the fix touches sensitive paths: " + ", ".join(sensitive))
    return {
        "path": "full" if reasons else "compact",
        "reasons": reasons,
        "basis": {
            "diagnosis_output_hash": found.get("output_hash"),
            "diagnosis_source_revision": found.get("source_revision"),
            "requirements_output": handoff.get("output"),
        },
    }


def _paths(found: dict) -> list[str]:
    return list(dict.fromkeys([*(found.get("affected_paths") or []), *(found.get("test_paths") or [])]))


def test_name(case: dict, taken=()) -> str:
    """The regression test the Builder writes for a diagnosis case: test_<id>_<words of its then clause>, the
    form the Planner is told to use (units/autoplanner BUG_DIAGNOSIS_RULE), made distinct from ``taken``."""
    words = re.findall(r"[a-z0-9]+", str(case.get("then") or case.get("text") or "").lower())[:6] or ["holds"]
    base = f"test_{str(case['id']).lower()}_" + "_".join(words)
    name, number = base, 2
    while name in taken:
        name, number = f"{base}_{number}", number + 1
    return name


def draft(state: RunState) -> dict:
    """The compact contract: one milestone that fixes the diagnosed root cause, with the invariant and every
    regression case as acceptance criteria, carrying every Requirements row as a required behaviour."""
    found = test_cases.current_diagnosis(state)
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    owned = list(dict.fromkeys([*_paths(found), found["note_path"]]))
    objective = "Fix the root cause: " + found["root_cause"]
    sources = [
        {"source_id": row["id"], "quote": row["text"]} for row in source_trace.quotable(obligations.sources(state))
    ]
    source_texts = [row["quote"] for row in sources]
    command = ((state.get("settings") or {}).get("regression") or {}).get("test_command")
    validation = [
        "Run the new regression tests against the original code: each restore case must fail",
        "Run them after the fix: every case must pass",
        command or "Run the project's existing test suite: it must pass",
    ]
    naming: list[str] = []
    names: list[str] = []
    cases: list[dict] = []
    for number, case in enumerate(found.get("test_cases") or [], start=2):
        preserve = case.get("kind") == "preserve"
        name = test_name(case, names)
        names.append(name)
        cases.append(
            {
                "id": f"C{number}",
                "criterion": test_cases.case_text(case),
                "verification_method": (test_cases.GUARD_MARK if preserve else test_cases.MARK) + " " + name,
                "human_review": False,
                "sources": copy.deepcopy(sources),
            }
        )
        naming.append(
            f"Write an additional proof test named {name} for {test_cases.case_text(case)} that "
            + (
                "passes on the original code and after the fix"
                if preserve
                else "fails on the original code and passes after the fix"
            )
            + "; also deliver the requested test names and preserve every existing test"
        )
    # C1 carries the invariant with the brief's file names, so a reviewer can see where the fix and its proof live,
    # and names the case tests that uphold it; the cases follow (a live review asked for both, #2021).
    criteria = [
        {
            "id": "C1",
            "criterion": found["invariant"]
            + f" (the fix in {', '.join(found['affected_paths'])}, proven by regression tests in "
            + ", ".join(found["test_paths"])
            + ")",
            "verification_method": "The regression tests "
            + ", ".join(names)
            + " (each restore case fails on the original code and passes after the fix), plus the existing test suite",
            "human_review": False,
            "sources": copy.deepcopy(sources),
        },
        *cases,
    ]
    ids = [row["id"] for row in criteria]
    requirements = [row for row in report.get("requirements") or [] if isinstance(row, dict)]
    behaviors = list(dict.fromkeys([found["invariant"], *(str(row.get("text") or "").strip() for row in requirements)]))
    quoted = [str(row.get("source_quote") or "").strip() for row in requirements]
    if command:
        criteria[0]["verification_method"] += ": " + command
    return {
        "intended_outcome": str(report.get("intended_outcome") or "").strip()
        or "The reported misbehavior no longer happens: " + found["observed"],
        "intended_user": "The person who reported the bug",
        "task_kind": "bugfix",
        "deliverables": owned,
        "required_behaviors": [text for text in behaviors if text],
        "important_failure_cases": [found["observed"]],
        "scope_exclusions": ["Changes unrelated to the diagnosed root cause"],
        "constraints": [
            "Change only " + ", ".join(owned),
            "Keep every existing test",
            f"Record the files you changed in the `changed` list of {found['note_path']}",
            *(f"As requested: {quote}" for quote in dict.fromkeys(quoted) if quote),
            *source_texts,
        ],
        "permission_boundaries": ["Edit only " + ", ".join(owned)],
        "accepted_assumptions": [
            {
                "text": f"The diagnosis in {found['note_path']} is correct: {found['root_cause']}",
                "basis": "agent_proposed",
                "answer_id": "",
            }
        ],
        "delegated_decisions": [],
        "acceptance_criteria": criteria,
        "open_blocking_questions": [],
        "end_to_end_flow": ["Reproduce: " + found["reproduction"], objective, *validation],
        # Not the Investigator's fix_plan verbatim: its test names would differ from the ones the criteria bind.
        "technical_approach": [
            objective,
            "Prove each diagnosis case by its named regression test: " + ", ".join(names),
            "Keep every existing test passing",
        ],
        "milestones": [
            {"id": "M1", "objective": objective, "acceptance_criteria": ids, "depends_on": [], "affected_paths": owned}
        ],
        "initial_task": {
            "objective": objective,
            "affected_paths": owned,
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": [
                *[text for text in behaviors if text],
                *source_texts,
                "Add the regression tests in "
                + ", ".join(found["test_paths"])
                + "; each restore case fails on the original code and passes after the fix",
                *naming,
            ],
            "acceptance_criteria": ids,
            "validation_plan": validation,
        },
    }


def trace_report(state: RunState, body: dict) -> dict:
    """The requirement trace the runner's draft carries: every Requirements row is covered by the behaviour
    that quotes it, so autocode_goals.check_requirement_trace holds the draft to the same rule as a Planner's."""
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    return {
        "requirement_trace": [
            {"requirement_id": row["id"], "disposition": "covered", "evidence": str(row.get("text") or "").strip()}
            for row in report.get("requirements") or []
            if isinstance(row, dict) and row.get("id")
        ],
        "conflict_resolutions": [],
    }


def start(state: RunState, workspace) -> bool:
    """After the Investigator's report is applied: take the compact path when every signal admits it.

    Installs the runner's draft as an unauthored draft (autocode_goal_lifecycle.UNAUTHORED_DRAFTS) and begins
    a planning cycle at the Plan Reviewer; a draft the contract validators refuse leaves the state as it was
    and the run on the full path, with the refusal recorded. Returns whether the compact path was taken."""
    verdict = decide(state)
    if verdict["path"] != "compact":
        stage_policy.record(state, **verdict, decision="included", reason="; ".join(verdict["reasons"]))
        return False
    body = draft(state)
    saved = copy.deepcopy(state)
    try:
        goals.check_requirement_trace(state, trace_report(state, body), body)
        lifecycle.install_draft(state, body, origin=ORIGIN, queue_human=False)
        lifecycle.validate_body(state, body, ready=True, origin=ORIGIN)
    except ValueError as error:
        cast(dict, state).clear()
        state.update(saved)
        verdict = {**verdict, "path": "full", "reasons": [f"the compact draft was refused: {error}"]}
        stage_policy.record(state, **verdict, decision="included", reason=verdict["reasons"][0])
        return False
    planning_cycle.start(state)
    stage_policy.record(
        state,
        **verdict,
        decision="omitted",
        reason="The runner built the draft from the reproduced diagnosis and the Requirements record; the Plan "
        "Reviewer reviews it and the person approves the displayed plan",
    )
    return True


def fallback_context(state: RunState) -> dict | None:
    """What the Planner's packet carries after a refused compact draft: the reason and the blocking concern IDs."""
    entry = stage_policy.current(state)
    if not entry or not entry.get("fallback") or not reviewing(state):
        return None
    concerns = ((state.get("planning") or {}).get("reports") or {}).get("astra_challenge", {}).get("report", {})
    return {
        "reason": entry["fallback"]["reason"],
        "blocking_concerns": [c["id"] for c in adaptive.blocking(concerns.get("concerns") or [])],
    }


def fallback_rule(state: RunState) -> str:
    """The Planner's instruction after a refused compact draft (prompt fragment), or ""."""
    return FALLBACK_RULE if fallback_context(state) else ""


def fall_back(state: RunState, reason: str) -> None:
    """The Plan Reviewer could not approve the compact draft: continue on the full path, at the Planner's
    draft, with the review on record for it (autocode_stage_policy.fall_back). Never a second compact draft."""
    # The compact draft's review was the runner's extra call: the full path keeps the review allowance it would
    # have had without it (a live fallback otherwise left the final review no call: PAUSED_PLANNING_BUDGET).
    planning = state.get("planning")
    refunded = False
    if isinstance(planning, dict) and type(planning.get("astra_calls")) is int and planning["astra_calls"] > 0:
        planning["astra_calls"] -= 1
        refunded = True
    stage_policy.fall_back(state, stage="astra_challenge", reason=reason, review_call_refunded=refunded)
    run_status.running(state, "PLANNING", next_stage=workflows.planner_stage(state))
