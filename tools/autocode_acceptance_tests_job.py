"""The Tester writes the approved acceptance tests before the Builder starts (spec-first runs, #1200).

autocode_acceptance_tests routes a build here when its due ``test:``/``guard:`` cases have no tests yet.
The Tester, on the Validator's model with a session of its own, writes one test per case, named as the
case says, and changes nothing but test files. The runner then:

- rejects the report if the attempt changed anything that is not a test file (the stray-write rule),
- runs the reported test files on the code as it is (a scratch copy) and requires, per case, that a
  ``test:`` case's test does not pass there (a failure, or a file that cannot load because the product
  lacks what it imports, counts when the file defines the named test) and that a ``guard:`` case's
  test passes; anything else rejects the report, so the Tester's retry gets the runner's evidence,
- archives the files and records the receipt, and returns the run to the build it was routed from.

A Tester that finds a ``test:`` case already working (``already_passing``) or impossible to test
(``cannot_write``) pauses the run with PAUSED_ACCEPTANCE_TESTS: the approved plan and the code
disagree, and only the user (``--feedback``) can say which is right.

Pure: prompt, schema and the transition; the unit passes in the function that runs the tests.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts

import json
import shutil
from pathlib import Path

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_run_status as run_status
    from . import autocode_stray_writes as stray_writes
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from .autocode_verify import is_test_path
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_run_status as run_status
    import autocode_stray_writes as stray_writes
    import autocode_test_cases as test_cases
    import autocode_util as util
    from autocode_verify import is_test_path

STAGE = acceptance.STAGE
PAUSE = "PAUSED_ACCEPTANCE_TESTS"
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


def _case(case: dict) -> dict:
    return {
        "id": case["id"],
        "criterion": case.get("text", ""),
        "test_name": case.get("test_name") or test_cases.case_test_name(case["id"]),
        "kind": "guard: must pass on today's code" if case.get("kind") == "preserve" else "test: must fail today",
    }


def packet(state: dict, inventory: dict | None = None, engine: str | None = None) -> dict:
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "cases": [_case(case) for case in acceptance.due(state)],
        "goal_contract": state.get("goal_contract"),
        "current_task": state.get("current_task"),
        "saved_answers": state.get("answers") or {},
        "workspace_inventory": inventory or {},
    }


def prompt(
    state: dict, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def repair_context(state: dict) -> dict:
    """A report-only repair sees the due cases, so it can move one to already_passing (jobs.repair_context)."""
    return {"cases": packet(state)["cases"]}


def owns(state: dict) -> bool:
    return False  # the run continues to the Builder; it never ends here


def render(state: dict) -> str:
    return ""


def _check_rows(value: dict, cases: list[dict]) -> None:
    due = {case["id"] for case in cases}
    named = [row["case_id"] for key in ("tests", "already_passing", "cannot_write") for row in value[key]]
    unknown = sorted(set(named) - due)
    if unknown:
        raise ValueError(f"Report only the due cases {sorted(due)}; unknown: {unknown}")
    if len(named) != len(set(named)) or set(named) != due:
        raise ValueError(
            f"Account for every due case exactly once, in tests, already_passing or cannot_write: {sorted(due)}"
        )


def _blocked(state: dict, value: dict) -> bool:
    rows = [f"{row['case_id']} already works: {row['evidence']}" for row in value["already_passing"]]
    rows += [f"{row['case_id']} cannot be tested: {row['reason']}" for row in value["cannot_write"]]
    if not rows:
        return False
    run_status.pause(
        state,
        PAUSE,
        "The Tester found the approved plan and the code disagree, before any building: "
        + "; ".join(rows)
        + ". --feedback revises the plan.",
        next_stage=STAGE,
    )
    return True


def _verdicts(cases: list[dict], value: dict, workspace, run: dict) -> dict[str, str]:
    """Per case, how the runner saw its test on today's code; raises ValueError naming what is wrong."""
    if run.get("error"):
        raise ValueError(f"The runner could not run the acceptance tests on today's code: {run['error']}")
    results = run.get("results")
    if results is None:
        raise ValueError("The acceptance tests reported no per-test results; write standard tests for the project")
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
    unloadable = bool(results.get("collection_errors"))
    verdicts, wrong = {}, []
    for case in named:
        source = (Path(workspace) / rows[case["id"]]["file"]).read_text(errors="replace")
        if case.get("kind") == "preserve":
            verdicts[case["id"]] = "passes" if passed[case["id"]] else ""
            if not passed[case["id"]]:
                wrong.append(f"guard {case['id']} ({case['test_name']}) does not pass on today's code")
        elif passed[case["id"]]:
            wrong.append(f"{case['id']} ({case['test_name']}) already passes on today's code")
        elif failed[case["id"]]:
            verdicts[case["id"]] = "fails"
        elif unloadable and test_cases.named_in(case["test_name"], source):
            verdicts[case["id"]] = "cannot load"
        else:
            wrong.append(f"{case['id']}: no test named {case['test_name']} ran in {rows[case['id']]['file']}")
    if wrong:
        raise ValueError(
            "The runner ran the acceptance tests on today's code: "
            + "; ".join(wrong)
            + ". A test: case must fail before the Builder starts and a guard: case must pass. If the behavior "
            "already exists, report it in already_passing instead of weakening the test."
        )
    return verdicts


def apply(state: dict, value: dict, record: dict, workspace, run_tests=None, evidence_dir=None) -> None:
    """Accept the Tester's files only when the runner sees each case behave as approved on today's code."""
    stray = sorted(str(path) for path in record.get("changed_files") or [] if not is_test_path(str(path)))
    if stray:
        raise stray_writes.StrayWrites(
            "The Tester may change only test files; this attempt changed: " + ", ".join(stray), stray
        )
    cases = acceptance.due(state)
    _check_rows(value, cases)
    if _blocked(state, value):
        return
    files = sorted({row["file"] for row in value["tests"]})
    bad = [path for path in files if not is_test_path(path) or not (Path(workspace) / path).is_file()]
    if bad:
        raise ValueError(f"Each test file must exist in the workspace and be a test file: {bad}")
    run = (run_tests or (lambda tests: {"error": "no test runner was given"}))(files)
    verdicts = _verdicts(cases, value, workspace, run)
    archive = Path(evidence_dir or Path(record.get("output") or workspace).parent) / "acceptance-tests"
    archive.mkdir(parents=True, exist_ok=True)
    kept = {}
    for path in files:
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
    run_status.running(state, "EXECUTING", next_stage=acceptance.resume_stage(state))
