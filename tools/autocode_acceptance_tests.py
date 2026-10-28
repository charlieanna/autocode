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

State key ``acceptance_tests``: receipts, redirected_from, pending_case_ids and optional
pending_test_bindings (contract hash + approved shared names, pinned when routing a new job). The job
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


def shared_test_names(state: RunState) -> dict[str, str]:
    """Names pinned for this generation job; legacy or different-contract jobs keep their old rule."""
    record = state.get(KEY) or {}
    binding = record.get("pending_test_bindings") or {}
    # Older in-flight jobs retain their original naming contract. A changed contract
    # cannot inherit another draft's bindings; normal routing pins its own next job.
    return (
        dict(binding.get("names") or {})
        if binding.get("contract_hash") == (state.get("goal_contract") or {}).get("hash")
        else {}
    )


def job_cases(state: RunState) -> list[dict]:
    """The cases ``route`` sent the Tester to write (route saved their ids), in plan order."""
    pending = set((state.get(KEY) or {}).get("pending_case_ids") or [])
    names = shared_test_names(state)
    return [
        {**case, **({"test_name": names[case["id"]]} if case["id"] in names else {})}
        for case in _cases(state, all_due=True)
        if case["id"] in pending
    ]


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
    shared = test_cases.diagnosis_test_names(state)
    record.update(
        redirected_from=state["next_stage"],
        pending_case_ids=[case["id"] for case in cases],
        pending_test_bindings={
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "names": {case["id"]: shared[case["id"]] for case in cases if case["id"] in shared},
        },
    )
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


def all_files(state: RunState) -> dict[str, dict]:
    """The Tester's files under the current contract: {path: {"sha256", "archive"}}."""
    current = (state.get("goal_contract") or {}).get("hash")
    files: dict[str, dict] = {}
    for receipt in receipts(state).values():
        if receipt.get("contract_hash") == current:
            files.update(receipt.get("files") or {})
    return files


def protected(state: RunState) -> dict[str, dict]:
    """The Tester's files under the current contract that stay fixed in the workspace; {} when blind."""
    if is_blind(state):
        return {}
    return all_files(state)


def blind_files(state: RunState) -> dict[str, dict]:
    """Archived blind test files under the current contract; {} when not blind."""
    if not is_blind(state):
        return {}
    return all_files(state)


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
    """Current-contract tests and its most recently recorded generation result, or None."""
    current = (state.get("goal_contract") or {}).get("hash")
    rows = [row for row in receipts(state).values() if row.get("contract_hash") == current]
    if not rows:
        return None
    order = {row.get("output"): index for index, row in enumerate(state.get("stages") or [])}
    last = max(rows, key=lambda row: (order.get(row.get("output"), -1), str(row.get("output") or "")))
    payload = {
        "files": sorted(all_files(state)),
        "case_ids": list(last.get("case_ids") or []),
        "results": dict(last.get("results") or {}),
        "output": last.get("output"),
    }
    if is_blind(state):
        payload["blind"] = True
    return payload


def open_row(state: RunState):
    """The open finding about blind acceptance tests, or None."""
    try:
        from . import autocode_findings as findings_ledger
    except ImportError:
        import autocode_findings as findings_ledger
    return next((row for row in findings_ledger.open_entries(state, "runner") if row.get("blind_tests")), None)


def reconcile_candidate(state: RunState, receipt: dict, revision: str):
    """Open, refresh, or resolve runner findings for blind acceptance tests."""
    try:
        from . import autocode_findings as findings_ledger
    except ImportError:
        import autocode_findings as findings_ledger

    failed_names = []
    results = receipt.get("results")
    if isinstance(results, dict):
        failed_names = list(results.get("failed") or [])
    timed_out = bool(receipt.get("timed_out"))
    error = receipt.get("error")
    exit_code = receipt.get("exit_code", 0)

    is_failing = bool(exit_code != 0 or timed_out or error or failed_names)
    row = open_row(state)
    at = util.now()
    checked = f"Runner check of source {str(revision)[:12]}"

    if not is_failing:
        if row is not None:
            findings_ledger.settle(
                row,
                "resolved",
                resolved_at=at,
                resolved_in=checked,
                resolution_evidence=f"{checked}: blind acceptance tests passed",
            )
        return None

    failure_details = []
    if failed_names:
        failure_details.append(f"failed tests: {', '.join(failed_names)}")
    if timed_out:
        failure_details.append("timed out")
    if error:
        failure_details.append(str(error))
    if exit_code != 0 and not failure_details:
        failure_details.append(f"exit code {exit_code}")
    detail_str = "; ".join(failure_details)
    finding_text = (
        f"Blind acceptance tests failed on candidate source {str(revision)[:12]} ({detail_str}). "
        "The change must satisfy the requirements and pass all acceptance tests."
    )
    evidence_text = f"{checked}: {detail_str}"

    if row is None:
        row = {
            "id": findings_ledger.allocate_id(state),
            "source": "runner",
            "severity": "high",
            "finding": finding_text,
            "evidence": evidence_text,
            "blocking": True,
            "status": "open",
            "opened_at": at,
            "opened_in": checked,
            "scope": None,
            "times_reported": 1,
            "last_reported_at": at,
            "last_reported_in": checked,
            "assigned_task": None,
            "blind_tests": True,
            "source_revision": revision,
            "failed_tests": failed_names,
        }
        findings_ledger.ledger(state).append(row)
    else:
        row.update(
            finding=finding_text,
            evidence=evidence_text,
            times_reported=row.get("times_reported", 1) + 1,
            last_reported_at=at,
            last_reported_in=checked,
            source_revision=revision,
            failed_tests=failed_names,
        )
    return row


def check_candidate(state: RunState, workspace, run_dir=None, scratch_run=None, timeout=60):
    """Run archived blind acceptance tests in a candidate scratch tree and reconcile findings."""
    if not is_blind(state):
        return None
    files = blind_files(state)
    if not files:
        return None

    try:
        from . import autocode_source_scope as source_scope
        from . import autocode_verify as verify
    except ImportError:
        import autocode_source_scope as source_scope
        import autocode_verify as verify

    try:
        revision = source_scope.snapshot(workspace, state)["revision"]
    except Exception:
        revision = util.digest(sorted(files))
    runner_scratch = scratch_run or verify.scratch_run
    run_dir = run_dir or state.get("run_dir") or workspace
    scratch_dir = Path(run_dir) / "blind-tests" / util.digest({"revision": revision, "files": sorted(files)})

    overlay_files = {path: entry["archive"] for path, entry in files.items()}
    current = (state.get("goal_contract") or {}).get("hash")
    last_receipt = None
    for receipt in receipts(state).values():
        if receipt.get("contract_hash") == current:
            last_receipt = receipt
    cmd = (last_receipt or {}).get("command") or None

    receipt = runner_scratch(
        workspace,
        scratch_dir,
        files=overlay_files,
        command=cmd,
        tests=list(files.keys()),
        timeout=timeout,
    )
    return reconcile_candidate(state, receipt, revision)


def refuse(state: RunState, workspace, run_dir=None, scratch_run=None) -> None:
    """Raise ValueError while blind acceptance tests fail."""
    if not is_blind(state) or not blind_files(state):
        return
    row = check_candidate(state, workspace, run_dir=run_dir, scratch_run=scratch_run)
    if row is not None:
        raise ValueError("Completion acceptance requires blind acceptance tests to pass. " + row["finding"])
