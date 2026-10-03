"""Validator evidence that points at the Validator's own checks.

A Validator cannot see the event IDs or exit codes of the shell commands it runs (OpenCode shows it
their output only), and reading its own event log to copy them cost about a fifth of its tokens in
live runs (2026-09-28..10-01). So it lists each check by its exact command and the runner attaches the
event (autocode_support.verify_checks), and its criterion, end-to-end and milestone evidence cite those
checks as ``check:<position, from 1>``. ``resolve`` replaces each such reference with the check's
attached evidence reference, after the checks are verified. Imports nothing from AutoCode.
"""
from __future__ import annotations

import re

REFERENCE = re.compile(r"check:(\d+)")


def resolve(report: dict) -> dict:
    """Swap check:<n> evidence references for the n-th check's evidence_ref, in place; returns the report."""
    checks = report.get("checks") or []

    def swap(ref):
        match = REFERENCE.fullmatch(ref.strip()) if isinstance(ref, str) else None
        if not match:
            return ref
        index = int(match.group(1)) - 1
        if not 0 <= index < len(checks) or not isinstance(checks[index], dict):
            raise ValueError(f"Evidence {ref} names no listed check; checks are numbered from 1")
        return checks[index]["evidence_ref"]

    rows = [*(report.get("criterion_results") or []), *(report.get("milestone_results") or [])]
    if isinstance(report.get("end_to_end_result"), dict):
        rows.append(report["end_to_end_result"])
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("evidence_refs"), list):
            row["evidence_refs"] = [swap(ref) for ref in row["evidence_refs"]]
    return report
