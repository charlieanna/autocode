"""Spec-first runs (#1200): the Tester writes the acceptance tests before the Builder starts.

With ``settings.spec_first`` (autocode_requirements_review), the Builder never starts on approved cases
that nobody has written a test for. When an approved contract's next stage is the Builder or the
orchestrator and its due ``test:``/``guard:`` cases (autocode_test_cases.contract_cases) have no receipt,
``route`` sends the run to STAGE first (autocode_acceptance_tests_job): the Tester, on the Validator's
model with a session of its own, writes one test per case, and the runner requires each ``test:`` case
to fail and each ``guard:`` case to pass on today's code. The Builder then works with those files fixed:
an attempt that changes or deletes one is rejected and the file restored from the receipt's archive
(``check_untouched``). The final regression proof (autocode_regression) is unchanged.

A bug fix's cases are its Investigator's (autocode_test_cases.proof_cases): reproduction tests must
really fail and preserve tests pass, before the fix exists.

State key ``acceptance_tests``: {"receipts": {batch key: receipt}, "redirected_from": stage}. The job
writes receipts; ``route`` writes redirected_from. Read here and by the status view. Imports only the
shared helpers and the test-case policy, so the build loop, Autopilot and the job can all use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
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
KEY: Final = "acceptance_tests"
SETTING_BLIND: Final = "blind_tests"
BUILD_STAGES = ("terra", "orchestrator")


def is_blind(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING_BLIND))


def enabled(state: RunState) -> bool:
    settings = state.get("settings") or {}
    return bool(settings.get("spec_first") or settings.get(SETTING_BLIND))


def add_argument(parser) -> None:
    parser.add_argument(
        "--blind-tests",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run Tester acceptance tests blindly (#702): tests are archived under the run directory and removed "
        "from the workspace so the Builder never sees them, then evaluated on candidate scratch trees; off by default",
    )


def new_run_setting(requested, joint: bool) -> bool:
    """Off unless asked for; requires joint planning so acceptance tests can be written for approved cases."""
    if requested is True and not joint:
        raise ValueError("--blind-tests needs joint planning")
    return requested is True


def resume_refused(saved_settings: dict, requested) -> bool:
    """A saved run keeps its choice; repeating its own choice is harmless, no flag changes nothing."""
    return requested is not None and bool(requested) != bool(saved_settings.get(SETTING_BLIND))


def _cases(state: RunState, *, all_due: bool = False) -> list[dict]:
    planned = test_cases.contract_cases(state, all_due=all_due)
    if not test_cases.bugfix(state):
        return planned
    # A bug fix's cases are its Investigator's reproduction and preserve cases (proof_cases) and the plan's
    # own test:/guard: cases: a live run (2026-10-14) whose plan added AC11/AC12 to the Tester's file stuck.
    found = test_cases.proof_cases(state, all_due=all_due)
    known = {case["id"] for case in found}
    return found + [case for case in planned if case["id"] not in known]


def covered(state: RunState) -> set[str]:
    """Case ids whose tests the Tester already wrote under the current contract."""
    current = (state.get("goal_contract") or {}).get("hash")
    return {
        cid
        for row in receipts(state).values()
        if row.get("contract_hash") == current
        for cid in row.get("case_ids") or []
    }


def due(state: RunState, wave=None) -> list[dict]:
    """The approved cases the Tester writes tests for now, minus those it already wrote; [] when off.

    ``wave`` lists the milestones a parallel batch is about to build (autocode_dispatch.select): their cases
    are due together, beside the current milestone's and the accepted ones'. A later milestone's tests are
    written when it is reached, so tests for unbuilt milestones never fail an earlier milestone's checks."""
    contract = state.get("goal_contract") or {}
    if not enabled(state) or contract.get("approval_status") != "approved":
        return []
    cases = _cases(state)
    if wave:
        members = {
            cid
            for row in (contract.get("body") or {}).get("milestones") or []
            if row.get("id") in set(wave)
            for cid in row.get("acceptance_criteria") or []
        }
        known = {case["id"] for case in cases}
        cases += [case for case in _cases(state, all_due=True) if case["id"] in members and case["id"] not in known]
    done = covered(state)
    return [case for case in cases if case["id"] not in done]


def job_cases(state: RunState) -> list[dict]:
    """The cases ``route`` sent the Tester to write (route saved their ids), in plan order."""
    pending = set((state.get(KEY) or {}).get("pending_case_ids") or [])
    return [case for case in _cases(state, all_due=True) if case["id"] in pending]


def batch_key(state: RunState, cases: list[dict]) -> str:
    contract = state.get("goal_contract") or {}
    return util.digest({"contract": contract.get("hash"), "cases": sorted(case["id"] for case in cases)})[:16]


def receipts(state: RunState) -> dict:
    return (state.get(KEY) or {}).get("receipts") or {}


def route(state: RunState, wave=None) -> None:
    """Send a build that has unwritten acceptance tests to the Tester first; idempotent across resumes.

    ``wave()`` returns the milestones the orchestrator would build together (autocode_dispatch.select);
    it is called only when the orchestrator is next on a spec-first run."""
    if state.get("next_stage") not in BUILD_STAGES or not enabled(state):
        return
    members = wave() if wave and state["next_stage"] == "orchestrator" else None
    cases = due(state, members)
    if not cases:
        return
    record = state.setdefault(KEY, {"receipts": {}})
    record.update(redirected_from=state["next_stage"], pending_case_ids=[case["id"] for case in cases])
    state["next_stage"] = STAGE


def resume_stage(state: RunState) -> str:
    """Where the run goes once the tests are accepted: the stage the build was routed away from."""
    return (state.get(KEY) or {}).get("redirected_from") or "terra"


def record_receipt(state: RunState, cases: list[dict], receipt: dict) -> None:
    record = state.setdefault(KEY, {"receipts": {}})
    record.setdefault("receipts", {})[batch_key(state, cases)] = receipt


def sent_back(state: RunState) -> set[str]:
    """Case ids already sent back to plan review once; a second disagreement pauses for the user."""
    return {cid for row in (state.get(KEY) or {}).get("send_backs") or [] for cid in row.get("case_ids") or []}


def record_send_back(state: RunState, case_ids: list[str], output) -> None:
    record = state.setdefault(KEY, {"receipts": {}})
    contract = state.get("goal_contract") or {}
    record.setdefault("send_backs", []).append(
        {"case_ids": list(case_ids), "contract_hash": contract.get("hash"), "output": output}
    )


def protected(state: RunState) -> dict[str, dict]:
    """The Tester's files under the current contract: {path: {"sha256", "archive"}}."""
    current = (state.get("goal_contract") or {}).get("hash")
    files: dict[str, dict] = {}
    for receipt in receipts(state).values():
        if receipt.get("contract_hash") == current:
            files.update(receipt.get("files") or {})
    return files


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def kept(archived: bytes, current: bytes | None) -> bool:
    """Whether every line the Tester wrote is still there, in order: additions are allowed.

    A Builder may add tests (a plan can name further tests in the same file). An inserted skip or early
    return still fails the final regression proof, which needs each case's test to fail without the
    change; a changed or deleted line is refused here."""
    if current is None:
        return False
    lines = iter(current.splitlines())
    return all(any(line == other for other in lines) for line in archived.splitlines())


def edited(state: RunState, patch: bytes) -> list[str]:
    """The Tester's files a parallel batch's patch changes other than by adding lines, sorted."""
    files, touched = set(protected(state)), set()
    current = None
    for line in (patch or b"").splitlines():
        if line.startswith(b"diff --git "):
            current = line.split(b" b/", 1)[-1].decode(errors="replace")
        elif (
            current in files
            and line.startswith(b"-")
            and not line.startswith(b"---")
            or current in files
            and line.startswith((b"deleted file", b"rename from"))
        ):
            touched.add(current)
    return sorted(touched)


# Builder attempts that changed a Tester's file and were sent straight back, per contract, before the
# runner rejects one through its ordinary invalid-output path instead.
DIRECT_RETRIES = 2


def check_untouched(state: RunState, workspace) -> bool:
    """Put back any Tester file a Builder attempt changed or deleted.

    Returns True when the Builder goes again at once (its product work kept, the restored files named in
    its next prompt); after DIRECT_RETRIES such attempts under one contract it rejects the attempt
    (StrayWrites). False when nothing was touched."""
    changed = []
    for path, entry in sorted(protected(state).items()):
        target = Path(workspace) / path
        archived = Path(entry["archive"]).read_bytes()
        if sha256(target) == entry["sha256"] or kept(archived, target.read_bytes() if target.is_file() else None):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archived)
        changed.append(path)
    record = state.setdefault(KEY, {"receipts": {}})
    if not changed:
        for row in record.get("builder_edits") or []:
            row["resolved"] = True  # a clean attempt: the Builder's prompt stops naming the old edit
        return False
    current = (state.get("goal_contract") or {}).get("hash")
    retries = [row for row in record.get("builder_edits") or [] if row.get("contract_hash") == current]
    if len(retries) < DIRECT_RETRIES:
        record.setdefault("builder_edits", []).append({"contract_hash": current, "paths": changed})
        state["next_stage"] = "terra"
        return True
    # paths=[]: the runner's snapshot restore must not touch these; they are already restored.
    raise StrayWrites(
        "The acceptance tests are fixed for the Builder; this attempt changed "
        + ", ".join(changed)
        + " (restored). Make the product pass them; you may add tests, never change or remove the Tester's lines.",
        [],
    )


def builder_note(state: RunState) -> str:
    """The Builder's prompt line naming the fixed test files; "" when there are none."""
    files = sorted(protected(state))
    if not files:
        return ""
    edits = (state.get(KEY) or {}).get("builder_edits") or []
    current = (state.get("goal_contract") or {}).get("hash")
    last = (
        edits[-1]["paths"]
        if edits and edits[-1].get("contract_hash") == current and not edits[-1].get("resolved")
        else []
    )
    restored = (
        "\nYour previous attempt changed the Tester's lines in " + ", ".join(last) + "; the runner put them back. "
        "Change the product so they pass instead.\n"
        if last
        else ""
    )
    return restored + (
        "\nThe tests for the cases above are already written, by the Tester, before you started (spec-first "
        "run): "
        + ", ".join(files)
        + ". Do not write them again. They fail on the code as it was and must pass when you finish. You may add "
        "tests to these files, but never change, move or delete a line the Tester wrote; the runner puts such a "
        "file back.\n"
    )


def view(state: RunState) -> dict | None:
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
