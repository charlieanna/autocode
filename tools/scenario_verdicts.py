"""The verdict vocabulary the live-trial scenarios and their oracles report in.

live_scenarios re-exports it. task_scenarios imports it from here rather than from live_scenarios,
which imports task_scenarios back for its registry. Imports nothing.
"""

from __future__ import annotations

PASS = "PASS"
FAIL = "FAIL"
FALSE_COMPLETE = "FALSE_COMPLETE"
HONEST_BLOCKER = "HONEST_BLOCKER"
DEFERRED = "DEFERRED"
ERROR = "ERROR"


class OracleResult:
    def __init__(self, status: str, summary: str, checks: list[dict]):
        self.status, self.summary, self.checks = status, summary, checks

    @property
    def failed(self) -> list[dict]:
        return [row for row in self.checks if not row["ok"]]
