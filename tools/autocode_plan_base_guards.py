"""Check each guard's example against the original code before the person approves the plan.

A ``guard:`` criterion says that behavior already works today and names the test that shows it. The
regression proof checks that claim only after the Builder has run: the named test must pass on the original
code and with the change. When the claim is wrong the Builder cannot fix it, and the run spends a whole
repair loop finding that out. One way a guard is wrong can be seen on the original code before approval,
without a model: it names a test the original code already has, and that test fails there. The behavior does
not work yet, so the criterion is a ``test:``, not a guard. The tests are looked up in the original commit's
test files and run on an original tree, by the runner the proof uses, and read through the proof's own name
matching (``autocode_test_cases.match_cases``).

A guard naming a test the original code does not define is left alone: the Builder may write that test, and
it passes on the original code when the behavior already works.

``defect`` turns what it finds into one ``plan_defect`` question naming those criteria, so
``autocode_plan_defects`` has the Planner correct the plan before approval, as a tool the boundary refuses
is corrected (autocode_plan_tool_admission). The question carries what was observed, which the recorded
correction keeps as the decision's evidence. It never passes or fails anything itself: where it cannot
observe (no original commit, no recognized runner, a run that did not finish, a setup error) it says
nothing, and the regression proof decides as before.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_run_base as run_base
    from . import autocode_test_cases as test_cases
    from . import autocode_test_environment as test_environment
    from . import autocode_test_setup as test_setup
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify as verify
except ImportError:
    import autocode_run_base as run_base
    import autocode_test_cases as test_cases
    import autocode_test_environment as test_environment
    import autocode_test_setup as test_setup
    import autocode_verification_schedule as schedule
    import autocode_verify as verify

QUESTION_ID = "plan-guard-base"
# A plan-time look at a few test files, not the proof: a slow suite is left to the proof.
TIMEOUT = 300
MAX_FILE_BYTES = 2_000_000
ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError, UnicodeError)
# One answer per original commit, guards and files: install_draft runs on every draft.
_ANSWERS: dict[tuple, list[dict]] = {}


def _defines(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def guards(body) -> list[dict]:
    """The plan's guards that name one exact test (not a command, not a title)."""
    return [case for case in test_cases.plan_cases(body) if case.get("kind") == "preserve" and case.get("test_name")]


def original_test_files(workspace, base) -> dict[str, str]:
    """Each test file of the original commit, by path, with its text."""
    listed = verify._read_git(workspace, "ls-tree", "-r", "-z", "--name-only", base, check=True)
    files = {}
    for path in (part for part in listed.split("\0") if part):
        if not verify.is_test_path(path):
            continue
        size = verify._read_git(workspace, "cat-file", "-s", f"{base}:{path}", check=False).strip()
        if not size.isdigit() or int(size) > MAX_FILE_BYTES:
            continue
        files[path] = verify._read_git(workspace, "show", f"{base}:{path}", check=False)
    return files


def _run(state: RunState, workspace: Path, base: str, files: list[str]) -> dict | None:
    """Per-test results of ``files`` on the original commit, as the regression proof runs them, or None."""
    framework = verify.detect_framework(workspace, python=test_environment.run_python(state, workspace))
    if framework is None:
        return None
    command = framework.targeted(files)
    if not command:
        return None
    # The original tree is disposable; the run's output and its command-supervision receipts are not
    # (autocode_command_supervision keeps admission and final records beside retained output, and its keeper
    # finishes them after the command returns), so they stay in a directory of their own.
    evidence = Path(tempfile.mkdtemp(prefix="autocode-plan-guards-"))
    scratch = Path(tempfile.mkdtemp(prefix="autocode-plan-guards-tree-"))
    tree = scratch / "tree"
    try:
        dependencies = state.get("project_workspace") or str(workspace)
        verify.make_tree(workspace, base, tree, workspace, {}, dependencies_from=dependencies)
        for root in dict.fromkeys((str(workspace.resolve()), str(workspace))):
            command = command.replace(root, str(tree))
        receipt = verify.run_suite(framework, command, tree, evidence, "plan-guards", timeout=TIMEOUT)
    finally:
        if tree.exists():
            verify.remove_tree(workspace, tree)
        shutil.rmtree(scratch, ignore_errors=True)
    if receipt.get("error") or receipt.get("timed_out") or not schedule.complete_results(receipt):
        return None
    return {
        "framework": framework.name,
        "command": command,
        "results": receipt.get("results") or {},
        "output": str(receipt.get("output") or evidence),
    }


def observe(state: RunState, body) -> list[dict]:
    """Each guard whose existing test fails on the original code: id, test name, failing test ids and detail."""
    cases = guards(body)
    where = state.get("workspace")
    if not cases or not where or not Path(where).is_dir() or test_cases.design_only(state):
        return []
    workspace = Path(where)
    base = run_base.base_commit(state, workspace)
    if not base:
        return []
    base = str(base)
    key = (str(workspace), base, tuple((case["id"], case["test_name"]) for case in cases))
    if key not in _ANSWERS:
        try:
            _ANSWERS[key] = _observe(state, workspace, base, cases)
        except ERRORS:
            _ANSWERS[key] = []  # nothing observed; the regression proof still decides
    return [dict(row) for row in _ANSWERS[key]]


def _observe(state: RunState, workspace: Path, base: str, cases: list[dict]) -> list[dict]:
    texts = original_test_files(workspace, base)
    defined = {}
    for case in cases:
        files = sorted(path for path, text in texts.items() if _defines(text, case["test_name"]))
        if files:
            defined[case["id"]] = files
    if not defined:
        return []
    run = _run(state, workspace, base, sorted({path for files in defined.values() for path in files}))
    if run is None:
        return []
    results = run["results"]
    setup = test_setup.not_evidence(results, new_behavior=False)
    named = [case for case in cases if case["id"] in defined]
    passed = test_cases.match_cases(named, list(results.get("passed") or []), framework=run["framework"])
    failed = test_cases.match_cases(named, list(results.get("failed") or []), framework=run["framework"])
    found = []
    for case in named:
        cid = case["id"]
        if failed[cid] and not passed[cid] and not any(test in setup for test in failed[cid]):
            found.append(
                {
                    "id": cid,
                    "test": case["test_name"],
                    "failed": failed[cid],
                    "detail": (
                        f"`{run['command']}` on the original code ({base[:12]}) fails {', '.join(failed[cid])} "
                        f"(output {run['output']})"
                    ),
                }
            )
    return found


def question(found) -> dict:
    ids = list(dict.fromkeys(row["id"] for row in found))
    named = ", ".join(f"{row['id']} (guard: {row['test']})" for row in found)
    return {
        "id": QUESTION_ID,
        "kind": "plan_defect",
        "category": "technical",
        "delegable": True,
        "convention_ref": "",
        "question": (
            f"{named} {'names a test that fails' if len(found) == 1 else 'name tests that fail'} on the original "
            f"code, so the behavior does not work yet. Should {', '.join(ids)} be a test: criterion for behavior "
            "the change adds, or guard a test that passes on the original code?"
        ),
        "why": (
            "AutoCode ran each guard's existing test on the original code before approval: "
            + "; ".join(row["detail"] for row in found)
            + ". The regression proof requires a guard's test to pass on the original code, so the plan as "
            "written cannot be proven."
        ),
        "options": [
            "Make it a test: criterion for behavior the change adds",
            "Guard a test that passes on the original code",
        ],
        "proposed_default": "Make it a test: criterion for behavior the change adds",
    }


def defect(state: RunState, body):
    """The plan-defect question for guards the original code contradicts, or None."""
    found = observe(state, body)
    return question(found) if found else None


def clear() -> None:
    """Forget cached observations (tests)."""
    _ANSWERS.clear()
