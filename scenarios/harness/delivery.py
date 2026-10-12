"""A completed run's delivery, re-proved from the public status view and the files on disk.

Recovery tests ask whether a run that was interrupted and resumed still delivered working
code. AutoCode's own claim (`TASK_COMPLETE`) is not that proof: a completion is verified only
while the delivered bytes still match what the Validator checked, every Validator check the
runner re-executed in a clean copy passed, and the scenario's oracle agrees. Each is published
through `autocode --status` (the `delivery` block from `autocode_status_command`, the
`check_replay` and `validator_source_revision` fields of the run view), so a test can re-derive
them without importing the runtime or reading private state.

The revision equality is what makes this a *recovery* assertion: an interrupted run that
resumed onto different source than the revision it validated would report a complete task whose
evidence belongs to code that no longer exists.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

from . import verdict

# Catalog oracle files import harness.oracle when loaded outside the runner, which grades them
# with `harness` already importable; the adversarial suites are loaded as scenarios.harness.*.
SCENARIOS = Path(__file__).resolve().parents[1]
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))

DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
EXECUTABLE = "executable:"
DELETED = "deleted"


def _sha(path: Path) -> str:
    """The same identity a source snapshot records for a regular file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity_matches(case, name: str, recorded: str, path: Path) -> None:
    """Re-derive a snapshot identity from the workspace and compare it with what was delivered."""
    failure = f"delivered {name!r} is not the byte content completion was verified against"
    if recorded == DELETED:
        case.assertFalse(path.exists(), f"{failure}: the snapshot says it was deleted, but it is on disk")
        return
    if recorded.startswith("symlink:"):
        case.assertTrue(path.is_symlink(), f"{failure}: expected a symlink")
        case.assertEqual(recorded[len("symlink:") :], os.readlink(path), failure)
        return
    if recorded.startswith("submodule:") or recorded == "uninitialized-submodule":
        case.fail(f"{failure}: a nested repository ({recorded.split(':')[0]}) is not deliverable source")
    digest = recorded[len(EXECUTABLE) :] if recorded.startswith(EXECUTABLE) else recorded
    if not DIGEST.match(digest):
        case.fail(f"{failure}: unrecognised snapshot identity {recorded!r}")
    case.assertTrue(path.is_file(), f"{failure}: {path} is missing")
    case.assertEqual(digest, _sha(path), failure)
    if recorded.startswith(EXECUTABLE):
        case.assertTrue(path.stat().st_mode & 0o111, f"{failure}: {name} was delivered executable, now is not")


def _proof(case, role: str, proof: dict, run_dir: Path, revision: str) -> None:
    """One completion proof: an artifact the runner published for the delivered revision."""
    path = Path(proof["path"])
    case.assertFalse(path.is_symlink(), f"{role} proof is a symlink: {path}")
    case.assertTrue(path.resolve().is_relative_to(run_dir.resolve()), f"{role} proof escaped the run directory: {path}")
    case.assertTrue(path.is_file(), f"{role} proof is missing: {path}")
    case.assertEqual(proof["sha256"], _sha(path), f"{role} proof changed after completion")
    case.assertEqual(revision, proof["source_revision"], f"{role} proof was recorded against another revision")


def assert_verified_delivery(case, view: dict, *, scenario, project: Path, run_dir: Path) -> None:
    """Every gate a completed run must still pass after an interruption and a resume."""
    case.assertTrue(view["done"], view)
    case.assertEqual("TASK_COMPLETE", view["status"], view)
    delivery = view.get("delivery")
    if not isinstance(delivery, dict):
        case.fail(f"a completed run published no delivery receipt: {delivery!r}")
        return

    files = delivery["files"]
    case.assertTrue(files, "the delivery receipt names no delivered files")
    for name, recorded in sorted(files.items()):
        _identity_matches(case, name, recorded, Path(project) / name)

    case.assertIs(True, delivery["verified_complete"], delivery)
    revision = delivery["source_revision"]
    case.assertEqual(revision, view["evidence"]["validator_source_revision"], "delivery validated another source")
    proofs = delivery["proofs"]
    case.assertEqual({"validator", "reviewer"}, set(proofs), "completion needs an independent Validator and Reviewer")
    for role, proof in proofs.items():
        _proof(case, role, proof, Path(run_dir), revision)

    replay = view["evidence"]["check_replay"]
    if not isinstance(replay, dict):
        case.fail(f"the runner saved no clean-copy replay: {replay!r}")
        return
    case.assertEqual(revision, replay["source_revision"], "the clean-copy re-run checked another source")
    case.assertEqual("PASS", replay["verdict"], "completion claimed a delivery its own re-run did not pass")
    rows = replay["checks"]
    case.assertTrue(rows, "completion needs at least one independently re-executed check")
    for row in rows:
        command = " ".join(row["command"]) if isinstance(row["command"], list) else str(row["command"])
        case.assertFalse(row["timed_out"], f"the re-run of {command} timed out")
        case.assertEqual(0, row["exit_code"], f"the re-run of {command} failed")
        output = Path(row["output"])
        case.assertTrue(output.is_file(), f"no saved output for the re-run of {command}")
        case.assertEqual(row["output_sha256"], _sha(output), f"the re-run output of {command} changed")

    checked = verdict.evaluate(scenario, project)
    case.assertTrue(checked.passed, f"AutoCode completed; the oracle disagrees: {checked.summary}")
    case.assertEqual(1, view["efficiency"]["delivery"]["verified_deliveries"], view["efficiency"]["delivery"])
