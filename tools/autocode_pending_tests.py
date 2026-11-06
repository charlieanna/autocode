"""Acceptance tests written ahead for milestones a regression proof does not cover yet.

A spec-first run writes a whole wave's acceptance tests before any of it is built. When that wave
is then built one milestone at a time, the other members' tests are still in the workspace, and
they cannot pass until their own milestone is built. A proof of the first member that runs them
fails on behavior nobody has built yet. ``withheld`` names those files so the proof leaves them
out of the candidate it builds; they come back in once their milestone is due, and every file is
in the final completion proof, where every case is due.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_test_cases as test_cases

if TYPE_CHECKING:
    from .autocode_run_state import RunState

NOTE = "Left out tests written ahead for criteria not yet due: "
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def withheld(state: RunState, workspace, due) -> list[str]:
    """Tester-written test files that hold only tests of criteria not yet due.

    A file is withheld when it is one an acceptance-test receipt pinned, still has the pinned
    content, names at least one not-yet-due case's test, and names no due case's test. Nothing is
    withheld when any due case has no test name (it could live in any file) or nothing is pending.
    """
    names = [case.get("test_name") for case in due]
    if not names or not all(names):
        return []
    due_ids = {case["id"] for case in due}
    pending = {
        case["test_name"]
        for case in test_cases.contract_cases(state, all_due=True)
        if case["id"] not in due_ids and case.get("test_name")
    }
    found = set()
    for receipt in acceptance_tests.receipts(state).values():
        for path, pinned in (receipt.get("files") or {}).items():
            try:
                content = Path(workspace, path).read_bytes()
            except OSError:
                continue
            if hashlib.sha256(content).hexdigest() != (pinned or {}).get("sha256"):
                continue
            words = set(WORD.findall(content.decode("utf-8", "replace")))
            if words & pending and not words & set(names):
                found.add(path)
    return sorted(found)
