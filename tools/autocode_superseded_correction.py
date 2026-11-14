"""Send a refused in-place test change back to plan review with the tests it replaces (#2748 follow-up).

A plan may name the original tests its fix replaces (autocode_superseded_tests). When the Planner leaves that
list empty and the Builder changes such a test where it stands, the original-test gate correctly refuses the
Tester's PASS. Before this module the run then paused, and the Investigator could only tell the person that a
plan revision was needed: a stop about AutoCode's own gate. Live Kilo runs with GLM 5.3 (tmp-superseded-mixed,
2026-11-05) stopped there twice, while the delivered code was correct.

Now the runner builds that revision itself, once, as the acceptance-tests correction does for a test: case
that already passes. It qualifies only when the refusal is exactly this conflict:

- the plan is approved and has a ``test:`` criterion, so the regression proof still requires a new test that
  fails on the original code and passes on the fix;
- the candidate suite passed, and the original suite ended on its own with complete per-test results,
  nothing uncollected and at least one failure;
- every failing original test is defined in exactly one original test file the change edited, and the
  approved plan does not list it already;
- the runner has not sent that test back before in this run.

The draft adds each failing test to the ``supersedes`` list of a ``test:`` criterion: the first whose named
test the changed file now defines (the replacement sits beside the old test), else the first ``test:``
criterion. The Plan Reviewer reviews the draft, the approval screen shows each entry as "Replaces existing
test", and approval binds to it as to any plan. Anything else, or a second refusal of the same test, keeps the
existing pause.

Pure apart from reading the edited test files, except ``send_back``, which installs the draft (the runner's
rejection path, autocode.reject_completed_stage, calls it).
State: ``superseded_corrections`` (the tests sent back, with the contract revision and receipt).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_superseded_tests as superseded_tests
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_superseded_tests as superseded_tests
    import autocode_test_cases as test_cases

# A runner-built draft (autocode_goal_lifecycle.UNAUTHORED_DRAFTS); plan review and approval still follow.
ORIGIN = "superseded_tests_correction"
KEY = "superseded_corrections"  # spelled out where the state is typed (autocode_run_state)
_PARAMETERS = re.compile(r"\[.*\]$")


def _test_name(test_id: str, file: str) -> str:
    """The test's own name in a runner's id, as ``supersedes`` names it for ``file``."""
    test_id = _PARAMETERS.sub("", test_id)
    if "::" in test_id:
        rest = test_id.split("::", 1)[1]
        return rest.split("/", 1)[0] if file.endswith(".go") else superseded_tests.own_names(rest)[0]
    return test_id.rsplit(".", 1)[-1]


def _entry(test_id: str, changed) -> dict | None:
    """The one changed original file and test name a failing id is, or None when it is not exactly one."""
    found = []
    for file in changed:
        name = _test_name(test_id, file)
        if name and superseded_tests.matches({"file": file, "test": name}, test_id):
            found.append({"file": file, "test": name})
    unique = {(row["file"], row["test"]) for row in found}
    return found[0] if len(unique) == 1 else None


def _sent(state: RunState) -> set[tuple[str, str]]:
    return {
        (str(row.get("file")), str(row.get("test")))
        for record in state.get("superseded_corrections") or []
        if isinstance(record, dict)
        for row in record.get("tests") or []
        if isinstance(row, dict)
    }


def _criterion(body: dict, file: str, workspace) -> str | None:
    """The ``test:`` criterion that takes an entry for ``file``: the first whose named test that file defines."""
    cases = [case for case in test_cases.plan_cases(body) if case.get("kind") != "preserve"]
    if not cases:
        return None
    try:
        text = (Path(workspace) / file).read_text(errors="replace")
    except OSError:
        text = ""
    for case in cases:
        name = case.get("test_name")
        if name and re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
            return case["id"]
    return cases[0]["id"]


def proposal(state: RunState, receipt, workspace) -> dict | None:
    """The corrected draft for a refused original gate, or None when the refusal is not that conflict.

    Returns ``body`` (the approved plan with the entries added), ``changes`` (declared for plan review) and
    ``tests`` (the entries, each with its criterion).
    """
    if not isinstance(receipt, dict) or receipt.get("verdict") != "FAIL" or not contracts.approved(state):
        return None
    candidate, original = receipt.get("candidate"), receipt.get("original")
    if (
        not isinstance(candidate, dict)
        or candidate.get("exit_code") != 0
        or candidate.get("error")
        or candidate.get("timed_out")
    ):
        return None
    changed = [str(name) for name in receipt.get("changed_tests") or []]
    failed = ((original or {}).get("results") or {}).get("failed") if isinstance(original, dict) else None
    if not changed or not isinstance(failed, list) or not failed:
        return None
    listed = superseded_tests.approved(state)
    entries = []
    for test_id in failed:
        if not isinstance(test_id, str) or any(superseded_tests.matches(row, test_id) for row in listed):
            continue
        entry = _entry(test_id, changed)
        if entry is None:
            return None
        if entry not in entries:
            entries.append(entry)
    sent = _sent(state)
    if not entries or any((row["file"], row["test"]) in sent for row in entries):
        return None
    # Every failure must be one listed test in an edited file, the gate's own rule for accepting the run.
    if not superseded_tests.excused(original, [*listed, *entries], changed):
        return None
    body = copy.deepcopy((state.get("goal_contract") or {}).get("body") or {})
    rows = {row.get("id"): row for row in body.get("acceptance_criteria") or [] if isinstance(row, dict)}
    tests = []
    for entry in entries:
        criterion = _criterion(body, entry["file"], workspace)
        if criterion is None:
            return None
        rows[criterion].setdefault(superseded_tests.KEY, []).append(dict(entry))
        tests.append({"criterion": criterion, **entry})
    try:
        superseded_tests.validate(state, body.get("acceptance_criteria"))
    except (ValueError, OSError):
        return None
    changes = [
        {
            "item": row["criterion"],
            "change": "reworded",
            "basis": "runner_proof",
            "answer_id": "",
            "replacement": (
                f"supersedes {row['file']}::{row['test']} (the change edited this original test and the original "
                "gate saw it fail while the changed suite passed; review whether it asserts the behavior this "
                "criterion changes, and remove the entry if it does not)"
            ),
        }
        for row in tests
    ]
    return {"body": body, "changes": changes, "tests": tests}


def send_back(state: RunState, record, error) -> bool:
    """Install the corrected draft for an original-gate refusal of a Tester report and begin plan review.

    False, leaving the state as it was, for any other error, or when the draft cannot be installed.
    """
    if record.get("stage") not in ("tester", "review_checkpoint"):
        return False
    try:
        from . import autocode_goal_lifecycle as lifecycle
        from . import autocode_planning_cycle as planning_cycle
    except ImportError:
        import autocode_goal_lifecycle as lifecycle
        import autocode_planning_cycle as planning_cycle
    found = proposal(state, getattr(error, "receipt", None), state.get("workspace") or ".")
    if found is None:
        return False
    candidate = copy.deepcopy(state)
    candidate.pop("pending_report_repair", None)  # a revised plan, not a repaired report, settles this refusal
    candidate.setdefault("superseded_corrections", []).append(
        {
            "contract_revision": (state.get("goal_contract") or {}).get("revision"),
            "tests": found["tests"],
            "receipt": (error.receipt or {}).get("receipt"),
            "output": record.get("output"),
        }
    )
    try:
        lifecycle.install_draft(
            candidate, found["body"], origin=ORIGIN, changes=found["changes"], record=record, queue_human=False
        )
    except ValueError:
        return False
    planning_cycle.start(candidate)
    cast(dict, state).clear()
    state.update(candidate)
    return True
