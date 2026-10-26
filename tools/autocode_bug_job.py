"""The bug-fix workflow's first stage: investigate a reported misbehavior before anyone changes code.

A run recognized as ``bugfix`` (autocode_workflows) starts with ``STAGE``: the
Investigator reproduces the report in a scratch copy of its own, finds the root
cause, and returns a diagnosis. The runner then:

- rejects the report if the stage changed anything in the workspace outside
  ``docs/bugs/`` (investigating is read-only; the before/after snapshot is the evidence),
- in a run of one package (--package), whose handoff names it (``package_scope``), rejects a
  reproduced bug whose affected or test paths are outside the package (#711),
- writes the diagnosis note (``note_path``, under ``docs/bugs/``) from the
  validated report, before any fix exists,
- waits for the reporter when any diagnosis has unanswered questions or grounded external work,
  ends a negative diagnosis with no questions, or hands a reproduced bug to the build
  pipeline: it goes to the Planner with the diagnosis as its brief
  (``large_correction``), skipping requirements gathering but keeping plan review
  and the user's approval. A short path that turns a small fix into one Builder
  task at once (``small_correction``) exists but is off (``SMALL_CORRECTION_ENABLED``).

A reproduced bug comes with ``test_cases``: the regression tests in plain English
(Given / When / Then with exact values), one per behavior the fix must restore. A
person reads these instead of test code. The Builder writes one test per case,
named ``test_<id>_...``, and the runner's regression proof (autocode_regression)
checks, with no model, that every case has a test that fails on the original
code and passes after the fix (autocode_test_cases.match_cases).

"Reproduced" is checked, not trusted: a reproduced bug carries a ``probe``, a
command that exits 0 exactly when the bug is present on today's code. The runner
runs it in a scratch copy (autocode_test_cases.run_probes) and rejects the
report if it does not exit 0. Its ``counterexample`` is the one edit that removes
the bug: the runner makes it in another scratch copy and the probe must fail
there, so a probe that passes whatever the code says is not a reproduction
(#1769). A bug no command can show here (a live registry, a
race, a device) says why in ``untestable`` instead; the regression proof at the
end still applies.

Pure module: prompt, schema, transition, rendering; the unit passes in the
function that runs the probe. Imports nothing from the runner. State key
written: ``investigation`` (the report, its note path, output, output_hash,
source_revision and probe_result). Human publication reads its pins; answer
routing and the next Investigator handoff read its questions and prior diagnosis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from . import autocode_bug_questions as bug_questions
    from . import autocode_investigation_progress as investigation_progress
    from . import autocode_investigation_retry as investigation_retry
    from . import autocode_package_scope as package_scope
    from . import autocode_requirement_cues as requirement_cues
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_util as util
    from . import autocode_workflows as workflows
    from . import autocode_workspace_scope as workspace_scope
    from .autocode_test_cases import (  # noqa: F401 (used by callers)
        COUNTEREXAMPLE,
        case_test_name,
        case_text,
        current_diagnosis,
        diagnosis_cases,
        match_cases,
        run_probes,
    )
except ImportError:
    import autocode_bug_questions as bug_questions
    import autocode_investigation_progress as investigation_progress
    import autocode_investigation_retry as investigation_retry
    import autocode_package_scope as package_scope
    import autocode_requirement_cues as requirement_cues
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_util as util
    import autocode_workflows as workflows
    import autocode_workspace_scope as workspace_scope
    from autocode_test_cases import (  # noqa: F401
        COUNTEREXAMPLE,
        case_test_name,
        case_text,
        current_diagnosis,
        diagnosis_cases,
        match_cases,
        run_probes,
    )

STAGE = workflows.INVESTIGATE_STAGE
(NOTES_PREFIX,) = stage_access.job_writes(STAGE)
OUTCOMES = ("reproduced", "not_reproduced")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
CASE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "given", "when", "then"],
    "properties": {
        "id": TEXT,
        "given": TEXT,
        "when": TEXT,
        "then": TEXT,
        # restore (default): behavior the fix restores. preserve: behavior that
        # already worked and must keep working (its test passes before and after).
        "kind": {"type": "string", "enum": ["restore", "preserve"]},
    },
}
CASE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "note_path",
        "observed",
        "reproduction",
        "root_cause",
        "affected_paths",
        "test_paths",
        "invariant",
        "test_cases",
        "conclusion",
        "fix_size",
        "fix_plan",
        "questions",
        "tests_run",
        "plan_approval_requested",
        "probe",
        "untestable",
    ],
    "properties": {
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "note_path": TEXT,
        "observed": TEXT,
        "reproduction": TEXT,
        "root_cause": TEXT,
        "affected_paths": TEXTS,
        "test_paths": TEXTS,
        "invariant": TEXT,
        "test_cases": {"type": "array", "items": CASE},
        "conclusion": TEXT,
        "fix_size": {"type": "string", "enum": ["small", "large", "none"]},
        "fix_plan": TEXTS,
        "questions": TEXTS,
        # Optional on saved reports; strict generation asks for the current scope.
        "workspace_scope": workspace_scope.SCHEMA,
        "tests_run": TEXTS,
        "plan_approval_requested": {"type": "boolean"},
        # probe: a shell command, run from the repository root, that exits 0 exactly when the bug is
        # present; untestable: why no command can show it here. A reproduced bug has exactly one.
        "probe": TEXT,
        # counterexample: the edit to one file that removes the bug, where the probe must fail (#1769).
        # Not in required, so reports saved before it still load.
        "counterexample": COUNTEREXAMPLE,
        "untestable": TEXT,
    },
}

PROMPT = prompts.get("bug-investigation.md")


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    data = {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        # The model must finish its evidence and report inside the saved window;
        # this is context, never permission to change or bypass that limit.
        "configured_stage_timeout_seconds": ((state.get("settings") or {}).get("limits") or {}).get(
            "stage_timeout_seconds"
        ),
        "notes_directory": NOTES_PREFIX,
        # The runner stops the stage after this many reading/searching calls with no reproduction
        # attempt (autocode_investigation_progress); 0 means the check is off.
        "exploration_budget": ((state.get("settings") or {}).get("limits") or {}).get(
            "investigation_exploration_budget", investigation_progress.DEFAULT_BUDGET
        ),
        # The configured acceptance command belongs to post-fix validation. Sending
        # it as an unqualified command here caused reproduced bugs to spend the
        # remaining investigation window rerunning broad consumer suites (#1902).
        # prepare() still uses the saved command to select the dependency Python.
        "workspace_inventory": inventory or {},
        # Present because every provider reads them; nothing is planned yet.
        "goal_contract": None,
        "current_task": None,
        "saved_answers": state.get("answers", {}),
        "prior_investigation": state.get("investigation"),
    }
    if retry := investigation_retry.context(state):
        data["prior_failed_attempt"] = retry
    return data


def prompt(
    state: RunState,
    inventory: dict | None = None,
    soft_budget_tokens: int = 10000,
    engine: str | None = None,
    *,
    scratch_workspace: str | None = None,
    python_executable: str | None = None,
    package_context: dict | None = None,
) -> tuple[str, dict]:
    """``package_context`` is what a package run tells the Investigator about its package
    (autocode_package_writes.context), handed over as ``package_scope``."""
    instruction = PROMPT
    data = packet(state, inventory, engine)
    if package_context:
        data["package_scope"] = package_context
    if scratch_workspace is not None:
        start = instruction.index("2. Try to reproduce")
        end = instruction.index("3. If it reproduces")
        instruction = instruction[:start] + prompts.get("fragments/bug-job/prompt.md") + instruction[end:]
        data["investigation_workspace"] = str(scratch_workspace)
        if python_executable is not None:
            data["investigation_python"] = python_executable
    text = instruction + "\nCURRENT HANDOFF DATA\n" + json.dumps(data, indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files, package: str | None = None) -> None:
    """Reject an investigation that wrote into the repository or does not say what it found, or, in a
    run of one ``package`` (--package), a reproduced bug whose affected or test paths are outside it."""
    stray = stage_access.stray(STAGE, changed_files)
    if stray:
        raise stray_writes.StrayWrites(
            "An investigation must not change the repository; this attempt changed: " + ", ".join(stray), stray
        )
    note = value["note_path"]
    if not note.startswith(NOTES_PREFIX) or not note.endswith(".json") or ".." in Path(note).parts:
        raise ValueError(f"note_path must be a .json file under {NOTES_PREFIX}: {note!r}")
    if not value["reproduction"].strip():
        raise ValueError("An investigation must say what it ran to reproduce the report")
    questions = [question.strip() for question in value["questions"]]
    if any(not question for question in questions):
        raise ValueError("Investigator questions must be nonempty")
    if len(questions) != len(set(questions)):
        raise ValueError("Investigator questions must be distinct after trimming surrounding whitespace")
    if value["outcome"] == "reproduced":
        missing = [field for field in ("root_cause", "affected_paths", "test_paths", "invariant") if not value[field]]
        if missing or value["fix_size"] == "none":
            raise ValueError(
                f"A reproduced bug needs a root cause, affected and test paths, an invariant and a fix size: {missing}"
            )
        unsafe = [path for path in value["affected_paths"] + value["test_paths"] if not safe_path(path)]
        if unsafe:
            raise ValueError(f"Paths must be relative paths inside the repository: {unsafe}")
        elsewhere = (
            package_scope.named_outside(package, value["affected_paths"] + value["test_paths"]) if package else []
        )
        if elsewhere:
            raise ValueError(
                f"This run works in the package {package}/ (--package): affected_paths and test_paths must "
                f"name files inside it, not {', '.join(elsewhere)}. A fix outside the package needs a run "
                "without --package: if the fix does need such a file, keep it named, and the run pauses rather "
                "than plan the fix."
            )
        check_cases(value.get("test_cases") or [])
        probe, untestable = value.get("probe", "").strip(), value.get("untestable", "").strip()
        if bool(probe) == bool(untestable):
            raise ValueError(
                "A reproduced bug needs exactly one of probe (a command that exits 0 exactly when "
                "the bug is present) or untestable (why no command can show it here)"
            )
    elif (
        value["affected_paths"]
        or value["test_paths"]
        or value["fix_plan"]
        or value.get("test_cases")
        or value["fix_size"] != "none"
        or value.get("probe", "").strip()
        or value.get("untestable", "").strip()
    ):
        raise ValueError("A report that did not reproduce must not propose a fix")


def check_cases(cases: list) -> None:
    """A reproduced bug needs at least one English test case, each complete and uniquely named."""
    if not cases:
        raise ValueError(
            "A reproduced bug needs test_cases: the regression tests in plain English "
            "(id, given, when, then), starting with the case you reproduced"
        )
    ids = [case["id"] for case in cases]
    bad = [case_id for case_id in ids if not CASE_ID.fullmatch(case_id)]
    if bad:
        raise ValueError(f"A test case id is a short name such as T1 (letters, digits, underscores): {bad}")
    duplicates = sorted({key for key in (i.lower() for i in ids) if [j.lower() for j in ids].count(key) > 1})
    if duplicates:
        raise ValueError(f"Test case ids must be unique: {duplicates}")
    empty = [case["id"] for case in cases if not all(case[key].strip() for key in ("given", "when", "then"))]
    if empty:
        raise ValueError(f"Every test case needs given, when and then: {empty}")
    bad_kinds = sorted({case.get("kind", "restore") for case in cases} - {"restore", "preserve"})
    if bad_kinds:
        raise ValueError(f"A test case kind is restore or preserve: {bad_kinds}")


def safe_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(path.strip()) and not Path(path).is_absolute() and ".." not in parts and ".git" not in parts


def note(value: dict) -> dict:
    """The diagnosis as saved in the repository. ``changed`` is always empty: nothing is fixed yet."""
    return {
        "reproduced": value["outcome"] == "reproduced",
        "observed": value["observed"],
        "reproduction": value["reproduction"],
        "root_cause": value["root_cause"],
        "affected_paths": value["affected_paths"],
        "test_paths": value["test_paths"],
        "invariant": value["invariant"],
        "test_cases": list(value.get("test_cases") or []),
        "conclusion": value["conclusion"],
        "fix_size": value["fix_size"],
        "fix_plan": value["fix_plan"],
        "questions": value["questions"],
        "tests_run": value["tests_run"],
        "changed": [],
        "probe": value.get("probe", ""),
        "untestable": value.get("untestable", ""),
    }


def diagnosis_artifact(state: RunState, workspace) -> dict | None:
    """Identify only the unchanged runner note reconstructed from its pinned, applied report."""
    found = state.get("investigation") or {}
    if found.get("outcome") != "reproduced" or not found.get("output_hash"):
        return None
    try:
        output = Path(found["output"])
        if output.is_symlink():
            return None
        raw = output.read_bytes()
        if hashlib.sha256(raw).hexdigest() != found["output_hash"]:
            return None
        report = json.loads(raw)
        if report != {key: found.get(key) for key in SCHEMA["properties"] if key in found or key in report}:
            return None
        check(report, [])
        root = Path(workspace).resolve()
        target = root / report["note_path"]
        if any(path.is_symlink() for path in (target, *target.parents) if path != root and root in path.parents):
            return None
        probe = report.get("probe", "").strip()
        if probe and not found.get("probe_result"):
            return None
        # The note was written from the normalized in-memory report, whose object key order
        # (and the sorted order a reloaded state.json carries) is not the raw file's order,
        # so only the note's parsed content has to equal the reconstruction, never its bytes.
        if target.stat().st_mode & 0o111:
            return None
        note_bytes = target.read_bytes()
        if json.loads(note_bytes) != {**note(report), "proven_by": probe}:
            return None
        return {
            "path": report["note_path"],
            "sha256": hashlib.sha256(note_bytes).hexdigest(),
            "kind": "runner_written_diagnosis",
            "output": str(output),
            "output_hash": found["output_hash"],
        }
    except (OSError, ValueError, TypeError, KeyError):
        return None


def apply(state: RunState, value: dict, record: dict, workspace, run_probe=None, run_counterexample=None) -> None:
    """``run_probe(command)`` runs the probe in a scratch copy (the unit passes autocode_verify.scratch_run)
    and ``run_counterexample(command, counterexample)`` runs it with the counterexample made, which
    removes the bug (autocode_counterexample.runner); without them a probed reproduction is rejected
    rather than trusted."""
    # The diagnosis note itself stays under docs/bugs/ at the repository root, inside or outside a package.
    check(value, record.get("changed_files"), package_scope.of(state.get("settings")))
    repeated = bug_questions.repeated_answers(state, value["questions"])
    if repeated:
        raise ValueError(
            "The Investigator must use saved user answers instead of repeating answered questions: "
            + "; ".join(repeated)
        )
    probe = value.get("probe", "").strip()
    shown = (
        run_probes(
            [
                {
                    "id": "the reported bug",
                    "example": value["observed"] or value["reproduction"],
                    "probe": probe,
                    "counterexample": value.get("counterexample"),
                }
            ],
            run_probe or (lambda command: {"error": "no probe runner was given"}),
            what="reproduction claim",
            key="id",
            run_counterexample=run_counterexample
            or (lambda command, edit: {"error": "no counterexample runner was given"}),
        )
        if probe
        else []
    )
    scope, dropped = workspace_scope.validated(
        value.get("workspace_scope"),
        sources=requirement_cues.source_texts(state),
        source_refs=[part.get("evidence") for part in (value.get("workspace_scope") or {}).get("external_parts", [])],
        workspace=workspace,
    )
    # Keep the exact accepted report for diagnosis-artifact identity. Only the
    # runner-owned scope record contains normalized, grounded obligations.
    target = Path(workspace) / value["note_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**note(value), "proven_by": probe if shown else ""}, indent=2) + "\n")
    output = record.get("output")
    state["investigation"] = {
        **value,
        "output": output,
        "output_hash": util.file_hash(Path(output)) if output and Path(output).is_file() else None,
        "source_revision": record.get("source_revision"),
        "probe_result": shown[0] if shown else None,
    }
    workspace_scope.record(state, scope, record, stage=STAGE, dropped=dropped)
    if bug_questions.has_questions(state):
        state.pop("completed_at", None)
        state.update(
            {
                "status": "WAITING_FOR_USER",
                "phase": "INVESTIGATING",
                "next_stage": STAGE,
                "pending_questions": bug_questions.questions(state),
            }
        )
        return
    if value["outcome"] == "not_reproduced":
        state.update(
            {
                "status": "TASK_COMPLETE",
                "phase": "COMPLETE",
                "next_stage": None,
                "completed_at": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
        return
    # A large fix carries the diagnosis alongside the original human task and skips
    # requirements gathering. Plan review and the user's approval still apply.
    state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": workflows.planner_stage(state)})


def large_correction(state: RunState) -> dict | None:
    """The diagnosis the Planner plans a fix from (a large one, or any the user asked to approve), or None."""
    found = current_diagnosis(state)
    if found.get("outcome") != "reproduced" or small_correction(state):
        return None
    return {
        "note_path": found["note_path"],
        "test_cases": list(found.get("test_cases") or []),
        **{
            key: found[key]
            for key in (
                "observed",
                "reproduction",
                "root_cause",
                "affected_paths",
                "test_paths",
                "invariant",
                "fix_plan",
                "questions",
                "conclusion",
            )
        },
    }


def test_cases(state: RunState) -> list[dict]:
    """The reproduced bug's English test cases, or [] (bugs planned without an investigation, older runs)."""
    return diagnosis_cases(state)


# A small, reproduced bug skips requirements gathering and plan review: the runner turns
# the diagnosis into a one-task contract and approves it under this policy, which the
# user agreed to on 2026-09-27. An independent Validator and the Completion Owner still
# judge the fix, and the regression test must fail on the original code.
#
# Off since 2026-09-29, by the user's decision: every job takes the full path (plan
# review and the user's approval) until that one path is dependable; the short path
# comes back after that. While off, a small fix is planned like a large one.
SMALL_CORRECTION_ENABLED = False
ORIGIN = "bugfix_small_correction"
SMALL_FIX_POLICY = prompts.get("fragments/bug-job/small-fix-policy.md")


def small_correction(state: RunState) -> bool:
    found = state.get("investigation") or {}
    return (
        SMALL_CORRECTION_ENABLED
        and found.get("outcome") == "reproduced"
        and found.get("fix_size") == "small"
        and not found.get("plan_approval_requested")
    )


def correction_contract(state: RunState) -> dict:
    """A one-milestone, one-criterion build contract derived from the saved diagnosis."""
    found = state["investigation"]
    owned = list(dict.fromkeys(found["affected_paths"] + found["test_paths"] + [found["note_path"]]))
    objective = "Fix the root cause: " + found["root_cause"]
    validation = [
        "Run the new regression test against the original code: it must fail",
        "Run it after the fix: it must pass",
        "Run the project's existing test suite: it must pass",
    ]
    criteria = [
        {
            "id": "C1",
            "criterion": found["invariant"],
            "verification_method": "A regression test that fails on the original code and "
            "passes after the fix, plus the existing test suite",
            "human_review": False,
        }
    ]
    naming = []
    for number, case in enumerate(test_cases(state), start=2):
        comparison = (
            "passes on the original code and after the fix"
            if case.get("kind") == "preserve"
            else "fails on the original code and passes after the fix"
        )
        criteria.append(
            {
                "id": f"C{number}",
                "criterion": case_text(case),
                "verification_method": f"The runner checks that a test named {case_test_name(case['id'])} "
                + comparison,
                "human_review": False,
            }
        )
        naming.append(
            f"Write test case {case_text(case)} as a test named {case_test_name(case['id'])} that {comparison}"
        )
    ids = [row["id"] for row in criteria]
    return {
        "intended_outcome": "The reported misbehavior no longer happens: " + found["observed"],
        "intended_user": "The person who reported the bug",
        # The runner then proves the regression test itself (autocode_regression).
        "task_kind": "bugfix",
        "deliverables": owned,
        "required_behaviors": [found["invariant"]],
        "important_failure_cases": [found["observed"]],
        "scope_exclusions": ["Changes unrelated to the diagnosed root cause"],
        "constraints": [
            "Change only " + ", ".join(owned),
            "Keep every existing test",
            f"Record the files you changed in the `changed` list of {found['note_path']}",
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
        "technical_approach": list(found["fix_plan"]) or [objective],
        "milestones": [
            {"id": "M1", "objective": objective, "acceptance_criteria": ids, "depends_on": [], "affected_paths": owned}
        ],
        "initial_task": {
            "objective": objective,
            "affected_paths": owned,
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": [
                found["invariant"],
                "Add a regression test in "
                + ", ".join(found["test_paths"])
                + " that fails on the original code and passes after the fix",
                *naming,
            ],
            "acceptance_criteria": ids,
            "validation_plan": validation,
        },
    }


# A live bugfix-cent-drift run (Claude models, 2026-09-29) was accepted with refund_line returning
# 156.45999999999998: every test case compared "to 2 decimal places" or within 0.001, the Validator
# only ran the suite, and nothing checked the diagnosis's rule ("every amount is a whole number of cents").
VALIDATOR_INVARIANT = prompts.get("fragments/bug-job/validator-invariant.md")


# A live bugfix-cent-drift run (Claude models, 2026-09-30): the request said "Before you change anything, write
# the root cause to docs/bugs/cent-drift.json". The runner had written it from the diagnosis and the plan kept it
# unchanged, but the Builder followed the request, overwrote the note with a shell heredoc, and the run paused.
BUILDER_NOTE = prompts.get("fragments/bug-job/builder-note.md")


def builder_note(state: RunState) -> str:
    """For a reproduced bug whose note the current task does not own: the note is done, leave it; "" otherwise."""
    found = state.get("investigation") or {}
    note = str(found.get("note_path") or "").strip()
    owned = (state.get("current_task") or {}).get("affected_paths") or []
    if found.get("outcome") != "reproduced" or not note or note in owned:
        return ""
    return BUILDER_NOTE.format(note=note)


def validator_note(state: RunState) -> str:
    """The current turn's reproduced invariant; historical diagnoses stay evidence, not new work."""
    found = current_diagnosis(state)
    if found.get("outcome") != "reproduced" or not str(found.get("invariant") or "").strip():
        return ""
    return VALIDATOR_INVARIANT.format(invariant=found["invariant"].strip())


def owns(state: RunState) -> bool:
    """The run ended at the investigation (the bug did not reproduce)."""
    return (
        state.get("status") in ("TASK_COMPLETE", "COMPLETE")
        and workflows.kind(state) == "bugfix"
        and (state.get("investigation") or {}).get("outcome") == "not_reproduced"
        and not (state.get("investigation") or {}).get("questions")
    )


def render(state: RunState) -> str:
    found = state.get("investigation") or {}
    lines = [
        "NOT REPRODUCED — no code was changed",
        "Workspace: " + str(state.get("workspace")),
        "Diagnosis: " + str(Path(state.get("workspace", "")) / found.get("note_path", NOTES_PREFIX)),
        "",
        "What was tried: " + found.get("reproduction", ""),
        "Conclusion: " + found.get("conclusion", ""),
    ]
    lines += ["Question for the reporter: " + question for question in found.get("questions") or []]
    if found.get("output"):
        lines.append("Investigator report: " + str(found["output"]))
    return "\n".join(lines)
