"""The bug-fix workflow's first stage: investigate a reported misbehavior before anyone changes code.

A run recognized as ``bugfix`` (autocode_workflows) starts with ``STAGE``: the
Investigator reproduces the report in a scratch copy of its own, finds the root
cause, and returns a diagnosis. The runner then:

- rejects the report if the stage changed anything in the workspace outside
  ``docs/bugs/`` (investigating is read-only; the before/after snapshot is the evidence),
- writes the diagnosis note (``note_path``, under ``docs/bugs/``) from the
  validated report, before any fix exists,
- waits for the reporter when an unreproduced report has unanswered questions,
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
report if it does not exit 0. A bug no command can show here (a live registry, a
race, a device) says why in ``untestable`` instead; the regression proof at the
end still applies.

Pure module: prompt, schema, transition, rendering; the unit passes in the
function that runs the probe. Imports nothing from the runner. State key
written: ``investigation`` (the report, its note path, output, output_hash,
source_revision and probe_result). Human publication reads its pins; answer
routing and the next Investigator handoff read its questions and prior diagnosis.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from . import autocode_bug_questions as bug_questions
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_util as util
    from . import autocode_workflows as workflows
    from .autocode_test_cases import (  # noqa: F401 (used by callers)
        case_test_name,
        case_text,
        diagnosis_cases,
        match_cases,
        run_probes,
    )
except ImportError:
    import autocode_bug_questions as bug_questions
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_util as util
    import autocode_workflows as workflows
    from autocode_test_cases import case_test_name, case_text, diagnosis_cases, match_cases, run_probes  # noqa: F401

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
        "tests_run": TEXTS,
        "plan_approval_requested": {"type": "boolean"},
        # probe: a shell command, run from the repository root, that exits 0 exactly when the bug is
        # present; untestable: why no command can show it here. A reproduced bug has exactly one.
        "probe": TEXT,
        "untestable": TEXT,
    },
}

PROMPT = """You are the Investigator: an engineer handed a bug report. Before anyone changes code, you find out
what is actually happening. You do not fix anything and you do not edit the repository.

What to do:
1. Restate what the reporter observed (observed).
2. Try to reproduce it in a fresh scratch copy under .autocode/investigation/<unique-name>/ inside the
   current workspace. Do not create scratch copies outside the workspace or use /tmp or mktemp's default
   location. Exclude .autocode/ and .git/ when copying source and dependencies, and do not follow symlinks
   outside the workspace. Write scratch source, test output and caches only in this new scratch directory;
   never modify existing runner state or evidence. Run bounded checks in the foreground, without nohup
   or detached processes: the command or scenario from the report, the existing tests, a small script
   or test of your own. Do not edit application source in the original workspace. The runner excludes
   .autocode/ scratch artifacts from its source comparison and rejects changes outside docs/bugs/.
   Record exactly what you ran and what happened (reproduction, tests_run).
3. If it reproduces (outcome reproduced): find the ROOT cause, not the place the symptom shows up.
   - root_cause: why it happens, in terms of the code's logic.
   - affected_paths: the source files that must change (not tests).
   - test_paths: where the regression test belongs: an existing test file, or the test directory.
   - invariant: the rule a correct fix must uphold (for example "one logical renew produces at most one
     mutation"), stated so a test can check it.
   - test_cases: the regression tests the fix must pass, in plain English, so a person can check them
     without reading code. One case per behavior the fix must restore, starting with the case you
     reproduced. Each has an id (T1, T2, ...), given (the exact starting data or state), when (the exact
     call or command) and then (the exact expected result, with literal values: "returns 1", "prints
     'Hello, Ada'", "exits 2"). No vague words such as "correctly" or "gracefully". The Builder writes one
     test per case named test_<id>_<what it checks> (for example test_t1_new_year_week_is_one_row), and
     the runner checks that a restore case's test fails on the original code because of the bug and passes
     after the fix, while a preserve case's test passes on the original code and after the fix. So each
     case's when uses only calls, commands and inputs that exist before the fix (never a hook, variable or
     helper the fix would add: a test using one cannot even build on the original code), driving the real
     failure path, and its then is the behavior (a result, an error, saved state), never only a log line
     or message. A case
     may carry kind (restore by default, or preserve): restore is behavior the fix restores; preserve is
     behavior that already worked and must keep working (for example "an exact multiple still gives the
     same page count") — its test must pass on the original code and after the fix, and a preserve case
     whose test fails on the original code is mis-tagged and fails the proof. Use preserve sparingly:
     only for a guard worth its own named test.
     Each case checks the invariant as exactly as the invariant states it: when the rule is exact (whole
     cents, at most one mutation), the case compares exactly, never "to 2 decimal places" or "differs by
     less than 0.001", which accept the very drift the rule forbids. At least one case uses the scale the
     report describes (several lines, realistic values), not only the smallest example.
   - fix_size: small when the cause is obvious and the fix is one bounded change in one or two files;
     large otherwise. Say large whenever the fix needs design choices or touches several modules.
   - fix_plan: the steps of the fix, and the regression test that fails before it and passes after it.
   - probe: a shell command, run from the repository root, that exits 0 exactly when the bug is present:
     it asserts today's WRONG result (for example: python3 -c "from pager import page_count; assert
     page_count(5, 2) == 2"). The runner runs it in a scratch copy of the code as it is and rejects a
     reproduced outcome whose probe does not exit 0, so only claim what you have run. When no command
     can show the bug here (it needs a live registry, a race, a device), leave probe "" and say why in
     untestable; otherwise untestable is "".
   - plan_approval_requested: true when the request asks to see, review or approve the plan or the fix
     before code changes; the fix is then planned and put to the user whatever its size. Otherwise false.
4. If it does NOT reproduce (outcome not_reproduced): say so plainly. Do not invent a cause and do not
   propose a "defensive" change to code that works. reproduction says what you tried; conclusion says
   what the code actually does and why the report may differ (old version, different input, upstream data);
   questions lists what you need from the reporter. Unanswered questions leave the bug unresolved:
   the run waits for answers and returns to you with saved_answers and prior_investigation.
   Read both before investigating again; use the saved answers and do not repeat answered questions.
   A failed tool or unavailable environment is not evidence that the code works; explain the blocker
   and ask for the missing access or reproduction context. fix_size is none; fix_plan, affected_paths,
   test_paths and test_cases are empty; probe and untestable are "".
5. conclusion: two or three sentences a person can act on.
6. note_path: where the runner saves your diagnosis. Use the path the request names if it names one under
   docs/bugs/, otherwise docs/bugs/<short-kebab-name>.json.

Return JSON only, matching the schema the runner gives you. The runner writes the note; you do not.
"""


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "notes_directory": NOTES_PREFIX,
        "test_command": ((state.get("settings") or {}).get("regression") or {}).get("test_command"),
        "workspace_inventory": inventory or {},
        # Present because every provider reads them; nothing is planned yet.
        "goal_contract": None,
        "current_task": None,
        "saved_answers": state.get("answers", {}),
        "prior_investigation": state.get("investigation"),
    }


def prompt(
    state: dict,
    inventory: dict | None = None,
    soft_budget_tokens: int = 10000,
    engine: str | None = None,
    *,
    scratch_workspace: str | None = None,
    python_executable: str | None = None,
) -> tuple[str, dict]:
    instruction = PROMPT
    data = packet(state, inventory, engine)
    if scratch_workspace is not None:
        start = instruction.index("2. Try to reproduce")
        end = instruction.index("3. If it reproduces")
        instruction = (
            instruction[:start]
            + """2. Use the runner-prepared investigation_workspace in CURRENT HANDOFF DATA. It already contains a
   complete copy of eligible application source. Git copies include tracked and ordinary untracked inputs,
   excluding ignored credentials, dependencies and outputs. Do not rebuild the copy, copy individual
   source files into it, or substitute an incomplete directory. Reproduce the reported behavior there.
   Keep scratch tests, output and caches in that directory. Do not edit application source in the
   original workspace, create scratch outside the workspace, or modify existing runner state or
   evidence. Do not use /tmp or mktemp's default location. Run bounded checks in the foreground without
   nohup or detached processes, preserve the real command exit status, and allow enough time for cold
   compilation. If setup fails, report that failure; it is not evidence that the bug was reproduced.
   Python virtualenvs are reused rather than copied. When investigation_python is provided, use it for
   Python commands from investigation_workspace, with PYTHONDONTWRITEBYTECODE=1. Do not install packages into or
   modify that environment; keep application imports and scratch writes in the investigation_workspace.
   The submitted probe runs again from the root of a clean source copy. Temporary files and installed
   packages under .autocode/investigation are not copied, and absolute workspace paths are redirected
   into that source copy. Reuse investigation_python for dependencies and keep application imports
   relative to the replay root; do not make the probe depend on temporary scratch packages or tests.
   Make the submitted probe self-contained: if reproduction needs a temporary test or fixture, the
   probe must create it with a relative path in its replay tree before running it. During investigation,
   create those files only under investigation_workspace; never add them to the original workspace.
   Record exactly what you ran and what happened (reproduction, tests_run).
"""
            + instruction[end:]
        )
        data["investigation_workspace"] = str(scratch_workspace)
        if python_executable is not None:
            data["investigation_python"] = python_executable
    text = instruction + "\nCURRENT HANDOFF DATA\n" + json.dumps(data, indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def check(value: dict, changed_files) -> None:
    """Reject an investigation that wrote into the repository or does not say what it found."""
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


def diagnosis_artifact(state: dict, workspace) -> dict | None:
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
        if report != {key: found.get(key) for key in SCHEMA["properties"]}:
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


def apply(state: dict, value: dict, record: dict, workspace, run_probe=None) -> None:
    """``run_probe(command)`` runs the probe in a scratch copy (the unit passes autocode_verify.scratch_run);
    without it a probed reproduction is rejected rather than trusted."""
    check(value, record.get("changed_files"))
    if (
        value["outcome"] == "not_reproduced"
        and value["questions"]
        and (state.get("goal_contract") or state.get("current_task"))
    ):
        raise ValueError("Investigator questions require initial diagnosis before a build contract or task")
    repeated = bug_questions.repeated_answers(state, value["questions"])
    if value["outcome"] == "not_reproduced" and repeated:
        raise ValueError(
            "The Investigator must use saved user answers instead of repeating answered questions: "
            + "; ".join(repeated)
        )
    probe = value.get("probe", "").strip()
    shown = (
        run_probes(
            [{"id": "the reported bug", "example": value["observed"] or value["reproduction"], "probe": probe}],
            run_probe or (lambda command: {"error": "no probe runner was given"}),
            what="reproduction claim",
            key="id",
        )
        if probe
        else []
    )
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
    if value["outcome"] == "not_reproduced":
        if value["questions"]:
            state.pop("completed_at", None)
            state.update(
                status="WAITING_FOR_USER",
                phase="INVESTIGATING",
                next_stage=STAGE,
                pending_questions=bug_questions.questions(state),
            )
            return
        state.update(
            status="TASK_COMPLETE", phase="COMPLETE", next_stage=None, completed_at=dt.datetime.now(dt.UTC).isoformat()
        )
        return
    # A large fix carries the diagnosis alongside the original human task and skips
    # requirements gathering. Plan review and the user's approval still apply.
    state.update(status="RUNNING", phase="PLANNING", next_stage=workflows.planner_stage(state))


def large_correction(state: dict) -> dict | None:
    """The diagnosis the Planner plans a fix from (a large one, or any the user asked to approve), or None."""
    found = state.get("investigation") or {}
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
            )
        },
    }


def test_cases(state: dict) -> list[dict]:
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
SMALL_FIX_POLICY = (
    "A reproduced bug the Investigator sized small becomes one Builder task built from the "
    "diagnosis and runs without plan approval; an independent Validator and the Completion "
    "Owner must still accept it, with a regression test that fails before the fix."
)


def small_correction(state: dict) -> bool:
    found = state.get("investigation") or {}
    return (
        SMALL_CORRECTION_ENABLED
        and found.get("outcome") == "reproduced"
        and found.get("fix_size") == "small"
        and not found.get("plan_approval_requested")
    )


def correction_contract(state: dict) -> dict:
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
VALIDATOR_INVARIANT = """
BUG INVARIANT: this change fixes a reproduced bug. Its diagnosis states the rule a correct fix must uphold:
{invariant}
The Builder's tests check a few examples of it. Check the rule itself, exactly as stated: capture your own check
that exercises it on inputs the tests do not use (for example many generated inputs, larger or multi-part ones)
and compares exactly, with no tolerance the rule does not allow. A violation is a FAIL, with the input that shows
it as evidence, even when every test passes.
"""


# A live bugfix-cent-drift run (Claude models, 2026-09-30): the request said "Before you change anything, write
# the root cause to docs/bugs/cent-drift.json". The runner had written it from the diagnosis and the plan kept it
# unchanged, but the Builder followed the request, overwrote the note with a shell heredoc, and the run paused.
BUILDER_NOTE = """
DIAGNOSIS NOTE: {note} is already written: the runner saved it from the investigation before this task began.
Any step of the request that asks to write the root cause or diagnosis there is done. It is not one of your files:
do not create, rewrite or edit it (not with a shell command either), and do not list it in your changed files.
"""


def builder_note(state: dict) -> str:
    """For a reproduced bug whose note the current task does not own: the note is done, leave it; "" otherwise."""
    found = state.get("investigation") or {}
    note = str(found.get("note_path") or "").strip()
    owned = (state.get("current_task") or {}).get("affected_paths") or []
    if found.get("outcome") != "reproduced" or not note or note in owned:
        return ""
    return BUILDER_NOTE.format(note=note)


def validator_note(state: dict) -> str:
    """The invariant the Validator must check directly, for a reproduced bug; "" otherwise."""
    found = state.get("investigation") or {}
    if found.get("outcome") != "reproduced" or not str(found.get("invariant") or "").strip():
        return ""
    return VALIDATOR_INVARIANT.format(invariant=found["invariant"].strip())


def owns(state: dict) -> bool:
    """The run ended at the investigation (the bug did not reproduce)."""
    return (
        state.get("status") in ("TASK_COMPLETE", "COMPLETE")
        and workflows.kind(state) == "bugfix"
        and (state.get("investigation") or {}).get("outcome") == "not_reproduced"
        and not (state.get("investigation") or {}).get("questions")
    )


def render(state: dict) -> str:
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
