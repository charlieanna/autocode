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
from pathlib import Path

REFERENCE = re.compile(r"check:(\d+)")


def unbound_refs(report: dict, allowed) -> list[tuple[str, str]]:
    """Criterion citations that name nothing the runner verified: (criterion id, reference).

    ``allowed`` is what the runner attached itself: every listed check's own evidence
    reference (after ``resolve`` swapped ``check:<n>`` citations for it) plus runner-verified
    design or visual evidence. Anything else — prose, or a path the report merely names —
    proves nothing the runner replayed, so the completion gate must not accept it.
    """
    rows = report.get("criterion_results") or []
    return [
        (row["id"], ref)
        for row in rows
        if isinstance(row, dict)
        for ref in row.get("evidence_refs") or []
        if isinstance(ref, str) and ref not in allowed
    ]


def outside(root, workspace):
    """A ``droppable`` test: true for a citation outside ``root`` (the run's .autocode/). A citation into
    it the runner did not write (model scratch, containment) is never dropped quietly."""

    def check(ref: str) -> bool:
        try:
            path = (Path(ref) if Path(ref).is_absolute() else Path(workspace) / ref).resolve()
        except (OSError, ValueError):
            return False
        return not path.is_relative_to(Path(root))

    return check


def drop_redundant_unbound(report: dict, allowed, droppable=lambda ref: True) -> list[tuple[str, str]]:
    """Remove, in place, the citations the runner cannot verify from a criterion that also cites
    verified evidence; return what was removed as (criterion id, reference).

    A reviewer that ran and cited a check sometimes adds the file it checked ("hello.txt") beside
    ``check:1``. That extra name proves nothing, but the verified check still does: dropping it is
    what a report repair would do, without a model call. A criterion citing nothing verified keeps
    its citations, so ``unbound_refs`` still refuses it and nothing unverified is ever accepted.
    ``droppable`` limits what may go quietly: a citation it rejects (model scratch under .autocode/)
    is kept, so the report is still refused for it.
    """
    dropped: list[tuple[str, str]] = []
    for row in report.get("criterion_results") or []:
        if not isinstance(row, dict):
            continue
        refs = [ref for ref in row.get("evidence_refs") or [] if isinstance(ref, str)]
        verified = [ref for ref in refs if ref in allowed]
        extra = [ref for ref in refs if ref not in allowed]
        if verified and extra and all(droppable(ref) for ref in extra):
            dropped += [(row["id"], ref) for ref in extra]
            row["evidence_refs"] = verified
    return dropped


def citations(report: dict, verified, droppable) -> tuple[list[tuple[str, str]], list[dict]]:
    """Drop what may go quietly (``drop_redundant_unbound``), then what is still unverified: the
    refusals (criterion id, reference), and the dropped citations as the validation records them."""
    dropped = drop_redundant_unbound(report, verified, droppable)
    return unbound_refs(report, verified), [{"criterion": criterion, "ref": ref} for criterion, ref in dropped]


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
        flow = report["end_to_end_result"]
        rows.append(flow)
        if isinstance(flow.get("technical_result"), dict):
            rows.append(flow["technical_result"])
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("evidence_refs"), list):
            row["evidence_refs"] = [swap(ref) for ref in row["evidence_refs"]]
    return report
