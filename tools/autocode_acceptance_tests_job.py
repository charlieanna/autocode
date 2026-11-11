"""The Tester writes the approved acceptance tests before the Builder starts (spec-first runs, #1200).

autocode_acceptance_tests routes a build here when its due ``test:``/``guard:`` cases have no tests yet.
The Tester, on the Validator's model with a session of its own, writes one test per case, named as the
case says, and changes only test files and validated Go loading metadata. The runner then:

- rejects non-test changes except additive already-selected Go prerequisites (the stray-write rule),
- runs the reported test files on the code as it is (a scratch copy) and requires, per case, that a
  ``test:`` case's test does not pass there (a failure, or a file that cannot load because the product
  lacks what it imports, counts when the file defines the named test) and that a ``guard:`` case's
  test passes; anything else rejects the report, so the Tester's retry gets the runner's evidence,
- archives the files and records the receipt, and returns the run to the build it was routed from.

A ``test:`` case the Tester reports already working (``already_passing``, its test still delivered) and
the runner sees pass goes back to plan review once, relabelled ``guard:`` in a runner-built draft (ORIGIN);
the user approves the corrected plan. That holds for a bug fix too: its plan can add ``test:`` cases of
its own beside the Investigator's reproduction (autocode_acceptance_tests._cases), and one of those that
already works is the same disagreement as in a build (#1868). A second disagreement on that case, a case no
test can show (``cannot_write``) and a bug's reproduction that already passes pause the run
(PAUSED_ACCEPTANCE_TESTS): only the user (``--feedback``) can say which is right.

A fault in the tests themselves (a guard that does not pass on today's code, often because its file cannot
load there, or a test the runner's command did not run) is not a question for the user: the runner sends the
tests back to the Tester with its evidence (``runner_feedback``), up to MAX_REWRITES times per plan and batch.

Pure: prompt, schema and the transition; the unit passes in the function that runs the tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts

import copy
import json
import shutil
from pathlib import Path

try:
    from . import autocode_acceptance_provenance as provenance
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_go_test_prerequisites as go_prerequisites
    from . import autocode_handoff_dedup as handoff
    from . import autocode_run_status as run_status
    from . import autocode_stray_writes as stray_writes
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from .autocode_report_repair_queue import ExecutionEvidenceRequired
    from .autocode_verify import is_test_path, run_command
except ImportError:
    import autocode_acceptance_provenance as provenance
    import autocode_acceptance_tests as acceptance
    import autocode_go_test_prerequisites as go_prerequisites
    import autocode_handoff_dedup as handoff
    import autocode_run_status as run_status
    import autocode_stray_writes as stray_writes
    import autocode_test_cases as test_cases
    import autocode_util as util
    from autocode_report_repair_queue import ExecutionEvidenceRequired
    from autocode_verify import is_test_path, run_command

STAGE = acceptance.STAGE
PAUSE = "PAUSED_ACCEPTANCE_TESTS"
# The runner-built draft that relabels cases the runner saw already pass (autocode_goal_lifecycle.UNAUTHORED_DRAFTS).
ORIGIN = "acceptance_tests_correction"
S = {"type": "string"}


def _rows(properties: dict) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        },
    }


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "tests", "already_passing", "cannot_write", "tests_run"],
    "properties": {
        "summary": S,
        # One row per due case: the test that proves it and the file it lives in.
        "tests": _rows({"case_id": S, "test_name": S, "file": S}),
        # A test: case whose behavior the code already has, with what showed it.
        "already_passing": _rows({"case_id": S, "evidence": S}),
        # A case no automated test can show, and why.
        "cannot_write": _rows({"case_id": S, "reason": S}),
        "tests_run": {"type": "array", "items": S},
    },
}
PROMPT = prompts.get("acceptance-tests.md")
REPAIR_RULES = prompts.get("fragments/acceptance-tests/repair-rules.md")
# Fresh Tester attempts the runner grants itself, per plan and batch of cases, when it ran the Tester's tests
# and saw a fault in the tests themselves. Only once they are spent does the run stop.
MAX_REWRITES = 2


class TestsNeedRewriting(ExecutionEvidenceRequired):
    """The runner ran the Tester's tests on today's code and the fault is in the tests: a guard that does not
    pass there, or a test the command did not run. The Tester can fix that, so the run gives it a fresh
    attempt with this evidence instead of asking the person (live run, 2026-11-11: a test module that imported
    the new function at its top could not load on today's code, so the guard in it never ran, and the run
    asked the person how to fix the tests)."""


def _case(case: dict, inherited=False) -> dict:
    return {
        "id": case["id"],
        "criterion": test_cases.case_text(case),
        "test_name": case.get("test_name") or test_cases.case_test_name(case["id"]),
        "kind": (
            "test: already delivered with authenticated fail-first provenance; retain its existing test, which may pass today; "
            "fresh original-reference fail-first proof remains required"
            if inherited
            else "guard: must pass on today's code"
            if case.get("kind") == "preserve"
            else "test: must fail today"
        ),
    }


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    inherited = provenance.inherited_cases(state, workspace=state.get("workspace"))
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "cases": [_case(case, case["id"] in inherited) for case in acceptance.job_cases(state)],
        "goal_contract": state.get("goal_contract"),
        "current_task": state.get("current_task"),
        "saved_answers": state.get("answers") or {},
        "workspace_inventory": inventory or {},
        # Why the runner rejected this plan's earlier attempts at these cases, oldest first.
        "runner_feedback": [row["feedback"] for row in _rewrites(state)],
    }


def _rewrites(state: RunState) -> list[dict]:
    """The fresh attempts the runner granted itself for the current plan and batch of cases."""
    record = state.get(acceptance.KEY) or {}
    contract = (state.get("goal_contract") or {}).get("hash")
    cases = sorted(record.get("pending_case_ids") or [])
    return [
        row
        for row in record.get("rewrites") or []
        if row.get("contract_hash") == contract and sorted(row.get("case_ids") or []) == cases
    ]


def _rewrite(state: RunState, error: TestsNeedRewriting, record: dict) -> bool:
    """Send the tests back to the Tester with the runner's evidence; False once MAX_REWRITES are spent."""
    if len(_rewrites(state)) >= MAX_REWRITES:
        return False
    saved = state.setdefault(acceptance.KEY, {"receipts": {}})
    saved.setdefault("rewrites", []).append(
        {
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "case_ids": sorted(saved.get("pending_case_ids") or []),
            "feedback": str(error),
            "output": record.get("output"),
        }
    )
    run_status.running(state, "EXECUTING", next_stage=STAGE)
    return True


def prompt(
    state: RunState, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = (
        PROMPT
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(handoff.deduplicate(packet(state, inventory, engine)), indent=2)
    )
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def repair_context(state: RunState) -> dict:
    """A report-only repair sees the due cases, so it can move one to already_passing (jobs.repair_context)."""
    return {"cases": packet(state)["cases"]}


def owns(state: RunState) -> bool:
    return False  # the run continues to the Builder; it never ends here


def render(state: RunState) -> str:
    return ""


def _check_rows(value: dict, cases: list[dict], *, shared_names=()) -> None:
    due = {case["id"] for case in cases}
    named = [row["case_id"] for key in ("tests", "cannot_write") for row in value[key]]
    unknown = sorted((set(named) | {row["case_id"] for row in value["already_passing"]}) - due)
    if unknown:
        raise ValueError(f"Report only the due cases {sorted(due)}; unknown: {unknown}")
    if len(named) != len(set(named)) or set(named) != due:
        raise ValueError(f"Account for every due case exactly once, in tests or cannot_write: {sorted(due)}")
    untested = sorted({row["case_id"] for row in value["already_passing"]} - {row["case_id"] for row in value["tests"]})
    if untested:
        raise ValueError(
            f"A case reported in already_passing still needs its test in tests, so the runner can see it pass: {untested}"
        )
    split = [
        name for name in shared_names if len({row["file"] for row in value["tests"] if row["test_name"] == name}) > 1
    ]
    if split:
        raise ValueError(f"Shared case bindings must report the same function and file, not copies: {sorted(split)}")


def _pause(state: RunState, rows: list[str]) -> None:
    run_status.pause(
        state,
        PAUSE,
        "The Tester found the approved plan and the code disagree, before any building: "
        + "; ".join(rows)
        + ". --feedback revises the plan.",
        next_stage=STAGE,
    )


def reproduction_ids(state: RunState) -> set[str]:
    """The ids of the Investigator's reproduction cases: a reproduced bug's ``restore`` cases, not its guards.

    They live in the diagnosis, not the contract, so the runner cannot relabel one; a plan's own cases
    never share their ids (autocode_acceptance_tests._cases)."""
    return {case["id"] for case in test_cases.diagnosis_cases(state) if case.get("kind", "restore") != "preserve"}


def corrected_body(state: RunState, names: dict[str, str]) -> dict:
    """The approved plan with each case in ``names`` (id -> test name) relabelled from test: to guard:."""
    body = copy.deepcopy((state.get("goal_contract") or {}).get("body") or {})
    for row in body.get("acceptance_criteria") or []:
        if row.get("id") in names and test_cases.mark(row.get("verification_method")) == test_cases.MARK:
            row["verification_method"] = test_cases.GUARD_MARK + " " + names[row["id"]]
    return body


def _output_excerpt(run: dict, *, keep: int = 3, limit: int = 400) -> str:
    """A short, bounded tail of the failed command's own output (#3091); receipts carry it in ``tail``."""
    kept = [line for line in str(run.get("tail") or "").splitlines() if line.strip()]
    return "\n".join(kept[-keep:])[-limit:]


def _verdicts(state: RunState, cases: list[dict], value: dict, workspace, run: dict, *, inherited=()) -> dict[str, str]:
    """Per case, how the runner saw its test on today's code; raises ValueError naming what is wrong."""
    if run.get("error"):
        raise ExecutionEvidenceRequired(
            f"The runner could not run the acceptance tests on today's code: {run['error']}"
        )
    results = run.get("results")
    if results is None:
        if run.get("exit_code") not in (0, None):
            # A failed command that printed no events is a command failure, not missing tests (#3091).
            excerpt = _output_excerpt(run)
            detail = f"; its output ends with:\n{excerpt}" if excerpt else ""
            raise ExecutionEvidenceRequired(
                f"The acceptance tests reported no per-test results and the test command exited "
                f"{run['exit_code']}{detail}"
            )
        raise ExecutionEvidenceRequired(
            "The acceptance tests reported no per-test results; write standard tests for the project"
        )
    rows = {row["case_id"]: row for row in value["tests"]}
    # The final regression proof finds each test by the approved name, else by the case id
    # (test_ac1_...): a test named otherwise would pass here and never count there.
    misnamed = [
        f"{case['id']}: {rows[case['id']]['test_name']}"
        for case in cases
        if case.get("test_name") != rows[case["id"]]["test_name"]
        and not (
            not case.get("test_name")
            and test_cases.match_cases([{"id": case["id"]}], [rows[case["id"]]["test_name"]])[case["id"]]
        )
    ]
    if misnamed:
        raise ValueError(f"Name each test as its case says (the approved name, else test_<case id>_...): {misnamed}")
    named = [{**case, "test_name": case.get("test_name") or rows[case["id"]]["test_name"]} for case in cases]
    passed = test_cases.match_cases(named, results.get("passed") or [])
    failed = test_cases.match_cases(named, results.get("failed") or [])
    # A bug's reproduction must run and fail; a file that cannot load is no reproduction (autocode_proof_seam).
    unloadable = bool(results.get("collection_errors")) and not test_cases.bugfix(state)
    verdicts, wrong = {}, []
    needs_execution = False
    already = {row["case_id"] for row in value["already_passing"]}
    for case in named:
        source = (Path(workspace) / rows[case["id"]]["file"]).read_text(errors="replace")
        if case.get("kind") == "preserve":
            verdicts[case["id"]] = "passes" if passed[case["id"]] else ""
            if not passed[case["id"]]:
                needs_execution = True
                wrong.append(_guard_not_passing(case, rows[case["id"]]["file"], run, results))
        elif case["id"] in inherited and passed[case["id"]]:
            verdicts[case["id"]] = "already passes"
        elif case["id"] in already:
            verdicts[case["id"]] = "already passes" if passed[case["id"]] else ""
            if not passed[case["id"]]:
                wrong.append(f"{case['id']} was reported already working, but {case['test_name']} does not pass today")
        elif passed[case["id"]]:
            wrong.append(f"{case['id']} ({case['test_name']}) already passes on today's code")
        elif failed[case["id"]]:
            verdicts[case["id"]] = "fails"
        elif unloadable and test_cases.named_in(case["test_name"], source):
            verdicts[case["id"]] = "cannot load"
        else:
            needs_execution = True
            wrong.append(_not_run(case, rows[case["id"]]["file"], source, run, results))
    if wrong:
        error = TestsNeedRewriting if needs_execution else ValueError
        raise error(
            "The runner ran the acceptance tests on today's code: "
            + "; ".join(wrong)
            + ". A test: case must fail before the Builder starts and a guard: case must pass. If the behavior "
            "already exists, keep its test and also report the case in already_passing; do not weaken the test."
        )
    return verdicts


def _guard_not_passing(case: dict, file: str, run: dict, results: dict) -> str:
    """Why a guard's test did not pass on today's code; a file that cannot load there is named as the cause."""
    wrong = f"guard {case['id']} ({case['test_name']}) does not pass on today's code"
    stem = Path(file).stem
    unloadable = [name for name in results.get("collection_errors") or [] if file in name or stem in name]
    if not unloadable:
        return wrong
    errors = [line.strip() for line in str(run.get("tail") or "").splitlines() if "Error" in line]
    cause = f" ({errors[-1][:200]})" if errors else ""
    return (
        f"{wrong}: it never ran, because {file} cannot load on today's code{cause}. A file that holds a "
        "guard must load before the Builder starts: import what the product does not have yet inside the new "
        "tests that use it, not at the top of the file, or put the new tests in a file of their own"
    )


def _not_run(case: dict, file: str, source: str, run: dict, results: dict) -> str:
    """Why the runner saw no result for a case's test, so the Tester's retry can fix the test (#1567).

    A file can define the test in a form the runner's command does not collect (a bare pytest function
    under ``python -m unittest``): every retry then sees the same "no test ran" until the Tester gives up."""
    missing = f"{case['id']}: no test named {case['test_name']} ran in {file}"
    if not test_cases.named_in(case["test_name"], source):
        return missing
    ran = sum(len(results.get(key) or []) for key in ("passed", "failed", "skipped"))
    command = run.get("command") or "the project's test command"
    hint = (
        " (python -m unittest collects only test_ methods of a unittest.TestCase subclass)"
        if "unittest" in command
        else ""
    )
    return (
        f"{missing}: the file defines it, but the runner's command `{command}` ran {ran} test(s) and did not "
        f"collect it; write the test so that command runs it{hint}"
    )


def apply(
    state: RunState, value: dict, record: dict, workspace, run_tests=None, evidence_dir=None, send_back=None
) -> None:
    """Accept the Tester's files only when the runner sees each case behave as approved on today's code.

    ``send_back(body, changes)`` installs a runner-corrected draft and restarts plan review (the unit passes
    it); a case the runner saw already pass is sent back once, relabelled guard:, else the run pauses."""
    stray = sorted(str(path) for path in record.get("changed_files") or [] if not is_test_path(str(path)))
    prerequisites = {}
    if stray and all(Path(path).name in ("go.mod", "go.sum") for path in stray):
        try:
            prerequisites = go_prerequisites.from_attempt(
                workspace,
                record,
                stray,
                Path(evidence_dir or Path(record.get("output") or workspace).parent) / "go-test-prerequisites",
                run_command,
            )["files"]
        except (OSError, ValueError, KeyError) as error:
            raise stray_writes.StrayWrites("Unverified Go test prerequisites: " + str(error), stray) from error
        stray = [path for path in stray if path not in prerequisites]
    if stray:
        raise stray_writes.StrayWrites(
            "The Tester may change only test files; this attempt changed: " + ", ".join(stray), stray
        )
    cases = acceptance.job_cases(state)
    _check_rows(value, cases, shared_names=set(acceptance.shared_test_names(state).values()))
    if value["cannot_write"]:
        _pause(state, [f"{row['case_id']} cannot be tested: {row['reason']}" for row in value["cannot_write"]])
        return
    files = sorted({row["file"] for row in value["tests"]})
    bad = [path for path in files if not is_test_path(path) or not (Path(workspace) / path).is_file()]
    if bad:
        raise ValueError(f"Each test file must exist in the workspace and be a test file: {bad}")
    run = (run_tests or (lambda tests: {"error": "no test runner was given"}))(files)
    inherited = provenance.inherited_cases(state, reports=value["tests"], workspace=workspace)
    try:
        verdicts = _verdicts(state, cases, value, workspace, run, inherited=inherited)
    except ExecutionEvidenceRequired as error:
        if isinstance(error, TestsNeedRewriting) and _rewrite(state, error, record):
            return
        if "no test command runs these files" in str(error):
            # The run's suite cannot name these files: a binding gap, not a fault in the tests (#2609).
            remedy = (
                "The tests are not at fault: the run's test suite cannot run them. Resume with a saved "
                "--test-command that runs them, and the runner executes a fresh proof with it."
            )
        else:
            remedy = (
                "Resume explicitly to correct the tests and execute a fresh proof, or use --feedback to revise "
                "the plan. A report-only repair cannot change test files or rerun proof."
            )
        run_status.pause(
            state,
            PAUSE,
            f"The runner could not establish the approved pre-build proof: {error}. {remedy}",
            next_stage=STAGE,
        )
        return
    already = {row["case_id"]: row for row in value["already_passing"] if row["case_id"] not in inherited}
    if already:
        names = {row["case_id"]: row["test_name"] for row in value["tests"] if row["case_id"] in already}
        # A bug's reproduction that already passes disagrees with its diagnosis: only the user can settle that.
        # The plan's own test: cases are contract rows the runner can relabel, in a bug fix as in a build.
        reproduced = reproduction_ids(state) & set(already)
        if send_back is None or reproduced or acceptance.sent_back(state) & set(already):
            _pause(
                state,
                [
                    f"{cid} already works: {row['evidence']}"
                    + (
                        " (the bug's reproduction passes, so the diagnosis and the code disagree)"
                        if cid in reproduced
                        else ""
                    )
                    for cid, row in sorted(already.items())
                ],
            )
            return
        changes = [
            {
                "item": cid,
                "change": "reworded",
                "basis": "runner_proof",
                "answer_id": "",
                "replacement": f"guard: {names[cid]} (the runner saw it pass on today's code: {already[cid]['evidence']})",
            }
            for cid in sorted(already)
        ]
        acceptance.record_send_back(state, sorted(already), record.get("output"))
        send_back(corrected_body(state, names), changes)
        return
    archive = Path(evidence_dir or Path(record.get("output") or workspace).parent) / "acceptance-tests"
    archive.mkdir(parents=True, exist_ok=True)
    kept = {}
    for path in sorted(set(files) | prerequisites.keys()):
        digest = util.file_hash(Path(workspace) / path)
        copy = archive / digest
        shutil.copyfile(Path(workspace) / path, copy)
        kept[path] = {"sha256": digest, "archive": str(copy)}
    acceptance.record_receipt(
        state,
        cases,
        {
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "case_ids": [case["id"] for case in cases],
            "files": kept,
            "results": verdicts,
            "command": run.get("command", ""),
            "output": record.get("output"),
            "source_revision": record.get("source_revision"),
        },
    )
    if acceptance.is_blind(state):
        for path in files:
            target = Path(workspace) / path
            if target.is_file():
                target.unlink()
            parent = target.parent
            while parent != Path(workspace) and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    run_status.running(state, "EXECUTING", next_stage=acceptance.resume_stage(state))
