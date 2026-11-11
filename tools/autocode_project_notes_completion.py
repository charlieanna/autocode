"""What a completed run's own proofs show about the repository: tests that flaked on unchanged code (#2943).

The runner proves a candidate more than once on the same source: the Completion Owner replays a
proof that failed only once (#2065, autocode_regression._review_needs_proof), and a Builder that
changes nothing is proved again. When one of those proofs failed a test and a later one passed it, on
the same source revision and the same proof context, the test's outcome did not follow the code: it
is a flaky test, and the next task in the project should not mistake one failure of it for a
regression. The pair may come before the source the run delivers: a run that is sent back after the
replay still saw the flake.

``flaky`` finds those tests in a completed run. It reads only runner-owned receipts: the run's
``regression_proofs`` history and each proof's ``verification.json`` under the run's own
``regression`` folder, read without following links. Only the candidate's checks count, since the
candidate is the source both proofs ran; a check that timed out, errored or reported incomplete
results shows nothing either way. Model text is never read.

Domain layer: a pure function over run state and the run's own files; nothing from the runner.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

PASS, FAIL = "PASS", "FAIL"
# At most this many flaky tests from one run, so one bad suite cannot crowd the notes file.
MAX_FLAKY = 3
# A test ID longer than this would not fit a note's text with its sentence.
MAX_TEST_ID = 400
# verification.json keeps the check receipts, not their logs; a larger file is not a proof the runner wrote.
MAX_PROOF_BYTES = 16 * 1024 * 1024
PROOF_DIR = re.compile(r"proof-(\d+)-[0-9a-f]{32}")


def _proofs(state: RunState) -> list[dict]:
    """The proof rows autocode_regression appended; recovery and rework events in the history are not proofs."""
    return [
        row
        for row in state.get("regression_proofs") or []
        if isinstance(row, dict)
        and row.get("verdict") in (PASS, FAIL)
        and isinstance(row.get("path"), str)
        and isinstance(row.get("source_revision"), str)
        and isinstance(row.get("proof_context"), str)
        and not ({"kind", "action"} & row.keys())
    ]


def _read(run_dir: Path, value: str) -> dict | None:
    """One proof's verification.json, only when it is a regular file in this run's regression folder."""
    path = Path(value)
    folder = Path(run_dir).resolve() / "regression"
    if path.name != "verification.json" or path.parent.parent != folder or not PROOF_DIR.fullmatch(path.parent.name):
        return None
    try:
        if not stat.S_ISDIR(os.lstat(folder).st_mode) or not stat.S_ISDIR(os.lstat(path.parent).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    handed = False  # once os.fdopen owns the descriptor, its handle closes it
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROOF_BYTES:
            return None
        with os.fdopen(fd, "rb") as handle:
            handed = True
            value = json.loads(handle.read(MAX_PROOF_BYTES + 1))
    except (OSError, ValueError):
        return None
    finally:
        if not handed:
            os.close(fd)
    return value if isinstance(value, dict) else None


def _outcomes(proof: dict | None) -> tuple[set[str], set[str]]:
    """(passed, failed) test IDs over the candidate checks whose results are complete."""
    passed: set[str] = set()
    failed: set[str] = set()
    checks = (proof or {}).get("checks")
    for label, receipt in checks.items() if isinstance(checks, dict) else ():
        if "candidate" not in str(label) or not isinstance(receipt, dict):
            continue
        results = receipt.get("results")
        if receipt.get("timed_out") or receipt.get("error") or not isinstance(results, dict):
            continue
        if not results.get("complete"):
            continue
        passed |= {test for test in results.get("passed") or [] if isinstance(test, str)}
        failed |= {test for test in results.get("failed") or [] if isinstance(test, str)}
    return passed, failed


def _number(path: str) -> str:
    match = PROOF_DIR.fullmatch(Path(path).parent.name)
    return f"proof {int(match.group(1))}" if match else "a proof"


def flaky(state: RunState, run_dir) -> list[dict]:
    """The tests that failed in one proof and passed in a later passing proof of the same source.

    Two proofs count only when they proved the same source revision in the same proof context, so
    the code, the approved contract, the cases and the commands were the same and only the outcome
    differed. Each row is ``{test, failed_in, passed_in, source_revision}``, oldest failure first; the
    caller keeps at most MAX_FLAKY. [] when no such pair exists.
    """
    proofs = _proofs(state)
    rows: list[dict] = []
    seen: set[str] = set()
    outcomes: dict[int, tuple[set[str], set[str]]] = {}

    def of(index):
        if index not in outcomes:
            outcomes[index] = _outcomes(_read(run_dir, proofs[index]["path"]))
        return outcomes[index]

    for later, passing in enumerate(proofs):
        if passing["verdict"] != PASS:
            continue
        for earlier, row in enumerate(proofs[:later]):
            if (
                row["verdict"] != FAIL
                or row["source_revision"] != passing["source_revision"]
                or row["proof_context"] != passing["proof_context"]
            ):
                continue
            passed, failed_too = of(later)
            for test in sorted(of(earlier)[1] & (passed - failed_too)):
                if test in seen or len(test) > MAX_TEST_ID:
                    continue
                seen.add(test)
                rows.append(
                    {
                        "test": test,
                        "failed_in": _number(row["path"]),
                        "passed_in": _number(passing["path"]),
                        "source_revision": passing["source_revision"],
                    }
                )
    return rows
