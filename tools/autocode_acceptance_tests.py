"""Spec-first runs (#1200): the Tester writes the acceptance tests before the Builder starts.

With ``settings.spec_first`` (autocode_requirements_review), the Builder never starts on approved cases
that nobody has written a test for. When an approved contract's next stage is the Builder or the
orchestrator and its due ``test:``/``guard:`` cases (autocode_test_cases.contract_cases) have no receipt,
``route`` sends the run to STAGE first (autocode_acceptance_tests_job): the Tester, on the Validator's
model with a session of its own, writes one test per case, and the runner requires each ``test:`` case
to fail and each ``guard:`` case to pass on today's code. The Builder then works with those files fixed:
an attempt that changes or deletes one is rejected and the file restored from the receipt's archive
(``check_untouched``). The final regression proof (autocode_regression) is unchanged.

A bug fix keeps its Investigator's cases and is not routed here. A plan whose milestones run in
parallel batches gets one Tester pass for the cases due at the first build (#1200 PR 3 covers later
batches and the parallel worker merge).

State key ``acceptance_tests``: {"receipts": {batch key: receipt}, "redirected_from": stage}. The job
writes receipts; ``route`` writes redirected_from. Read here and by the status view. Imports only the
shared helpers and the test-case policy, so the build loop, Autopilot and the job can all use it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from .autocode_stray_writes import StrayWrites
except ImportError:
    import autocode_test_cases as test_cases
    import autocode_util as util
    from autocode_stray_writes import StrayWrites

STAGE = "write_acceptance_tests"
KEY = "acceptance_tests"
BUILD_STAGES = ("terra", "orchestrator")


def enabled(state: dict) -> bool:
    return bool((state.get("settings") or {}).get("spec_first"))


def due(state: dict) -> list[dict]:
    """The approved cases the Tester writes tests for now; [] when the setting is off or nothing is due."""
    contract = state.get("goal_contract") or {}
    if not enabled(state) or contract.get("approval_status") != "approved" or test_cases.bugfix(state):
        return []
    return test_cases.contract_cases(state)


def batch_key(state: dict, cases: list[dict]) -> str:
    contract = state.get("goal_contract") or {}
    return util.digest({"contract": contract.get("hash"), "cases": sorted(case["id"] for case in cases)})[:16]


def receipts(state: dict) -> dict:
    return (state.get(KEY) or {}).get("receipts") or {}


def route(state: dict) -> None:
    """Send a build that has unwritten acceptance tests to the Tester first; idempotent across resumes."""
    if state.get("next_stage") not in BUILD_STAGES:
        return
    cases = due(state)
    if not cases or batch_key(state, cases) in receipts(state):
        return
    record = state.setdefault(KEY, {"receipts": {}})
    record["redirected_from"] = state["next_stage"]
    state["next_stage"] = STAGE


def resume_stage(state: dict) -> str:
    """Where the run goes once the tests are accepted: the stage the build was routed away from."""
    return (state.get(KEY) or {}).get("redirected_from") or "terra"


def record_receipt(state: dict, cases: list[dict], receipt: dict) -> None:
    record = state.setdefault(KEY, {"receipts": {}})
    record.setdefault("receipts", {})[batch_key(state, cases)] = receipt


def protected(state: dict) -> dict[str, dict]:
    """The Tester's files under the current contract: {path: {"sha256", "archive"}}."""
    current = (state.get("goal_contract") or {}).get("hash")
    files = {}
    for receipt in receipts(state).values():
        if receipt.get("contract_hash") == current:
            files.update(receipt.get("files") or {})
    return files


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def check_untouched(state: dict, workspace) -> None:
    """Reject a Builder attempt that changed or deleted a Tester's file, after putting the file back."""
    changed = []
    for path, entry in sorted(protected(state).items()):
        target = Path(workspace) / path
        if sha256(target) == entry["sha256"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(entry["archive"]).read_bytes())
        changed.append(path)
    if changed:
        # paths=[]: the runner's snapshot restore must not touch these; they are already restored.
        raise StrayWrites(
            "The acceptance tests are fixed for the Builder; this attempt changed "
            + ", ".join(changed)
            + " (restored). Make the product pass them; add any extra tests in other files.",
            [],
        )


def builder_note(state: dict) -> str:
    """The Builder's prompt line naming the fixed test files; "" when there are none."""
    files = sorted(protected(state))
    if not files:
        return ""
    return (
        "\nAcceptance tests written by the Tester before you started (spec-first run): "
        + ", ".join(files)
        + ". They fail on the code as it was and must pass when you finish. Do not edit, move or delete them; "
        "the runner rejects an attempt that does. Put any further tests in other files.\n"
    )


def view(state: dict) -> dict | None:
    """The status view's ``acceptance_tests`` field: files, cases and the last result, or None."""
    rows = list(receipts(state).values())
    if not rows:
        return None
    last = rows[-1]
    return {
        "files": sorted(protected(state)),
        "case_ids": list(last.get("case_ids") or []),
        "results": dict(last.get("results") or {}),
        "output": last.get("output"),
    }
