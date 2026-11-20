"""Validator evidence that points at the Validator's own checks.

A Validator cannot see the event IDs or exit codes of the shell commands it runs (OpenCode shows it
their output only), and reading its own event log to copy them cost about a fifth of its tokens in
live runs (2026-09-28..10-01). So it lists each check by its exact command and the runner attaches the
event (autocode_support.verify_checks), and its criterion, end-to-end and milestone evidence cite those
checks as ``check:<position, from 1>``. ``resolve`` replaces each such reference with the check's
attached evidence reference, after the checks are verified. ``drop_unverified_extras`` removes a
criterion's extra citation of something the runner did not verify while the criterion still cites a
verified one, before the report is sealed. Imports nothing from AutoCode.
"""

from __future__ import annotations

import re
from pathlib import Path

REFERENCE = re.compile(r"check:(\d+)")
# Under .autocode/ only these areas are written by models; everything else there is the runner's.
MODEL_WRITABLE = re.compile(r"tool-containment-[0-9a-f]{32}|scratch|investigation")


def runner_receipt(workspace, ref: str) -> bool:
    """Whether ``ref`` names evidence the runner alone wrote under the workspace's .autocode/.

    Outside model scratch, containment and investigation areas the runner alone writes there
    (events, receipts, captures, activity), so a citation there is evidence this run recorded;
    #638's live Validator cited activity.jsonl. Ownership stays with the rework policy.
    """
    managed_root = (Path(workspace) / ".autocode").resolve()
    try:
        path = (Path(ref) if Path(ref).is_absolute() else Path(workspace) / ref).resolve()
    except OSError:
        return False
    return path.is_relative_to(managed_root) and not any(
        MODEL_WRITABLE.fullmatch(part) for part in path.relative_to(managed_root).parts
    )


def drop_unverified_extras(report: dict, workspace) -> list[dict]:
    """Drop each criterion's unverified citations while it still cites verified evidence, in place.

    A Tester that cites a listed check and also names the file it read (``["check:1", "hello.txt"]``,
    live Kilo run 2026-10-20) has its proof in the check; the file name proves nothing the runner
    replayed, and refusing the whole report for it cost a report repair. Verified here means a
    listed check (``check:<n>`` or its evidence reference) or a runner receipt. A criterion whose
    citations are all unverified keeps them, so ``unbound_refs`` still refuses it. A ``check:<n>``
    naming no listed check and an ``event:`` ID the runner did not attach are never dropped: one is
    refused by ``resolve``, the other is a claim of an execution that did not happen, and both keep
    the refusal. For reports without design or visual evidence, whose verified set is exactly this.
    Returns what was dropped, as ``{"criterion", "ref"}`` rows in report order.
    """
    checks = [check for check in report.get("checks") or [] if isinstance(check, dict)]
    listed = {check.get("evidence_ref") for check in checks}

    def verified(ref) -> bool:
        match = REFERENCE.fullmatch(ref.strip())
        if match:
            return 1 <= int(match.group(1)) <= len(checks)
        return ref in listed or runner_receipt(workspace, ref)

    dropped: list[dict] = []
    for row in report.get("criterion_results") or []:
        refs = row.get("evidence_refs") if isinstance(row, dict) else None
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            continue
        if not any(verified(ref) for ref in refs):
            continue
        extra = [
            ref
            for ref in refs
            if not verified(ref) and not REFERENCE.fullmatch(ref.strip()) and not ref.startswith("event:")
        ]
        if extra:
            row["evidence_refs"] = [ref for ref in refs if ref not in extra]
            dropped += [{"criterion": row.get("id"), "ref": ref} for ref in extra]
    return dropped


def tidy_tester_citations(record: dict, report, workspace, settings) -> None:
    """Apply ``drop_unverified_extras`` to a Tester report before it is sealed, noting the drops on ``record``.

    Only for the Tester (``tester``), its report repair included, and only in a run without design or
    visual evidence: those verify more than checks and receipts, so they keep the full refusal.
    """
    settings = settings or {}
    if (
        workspace is None
        or not isinstance(report, dict)
        or (record.get("original_stage") or record.get("stage")) != "tester"
        or settings.get("design_manifest")
        or settings.get("figma_file")
    ):
        return
    if dropped := drop_unverified_extras(report, workspace):
        record["dropped_evidence_refs"] = dropped


def require_passing_evidence(report: dict) -> None:
    """A claimed PASS must cite evidence before replay or completion is attempted."""
    missing = [
        str(row.get("id", "?"))
        for row in report.get("criterion_results") or []
        if isinstance(row, dict)
        and row.get("status") == "PASS"
        and not any(isinstance(ref, str) and ref.strip() for ref in row.get("evidence_refs") or [])
    ]
    if missing:
        raise ValueError(
            "PASS criterion results lack evidence references: "
            + ", ".join(missing)
            + ". Cite check:<n> for an already listed check or an existing runner-written artifact. "
            "A report-only repair may reuse supplied evidence; it must not invent approval, evidence or a new execution. "
            "If no evidence establishes the criterion, report NOT_VERIFIED instead of PASS."
        )


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


def unbound_message(criterion, ref) -> str:
    """The refusal of a criterion citing ``ref``, which the runner did not verify: one text for the final
    gate (autopilot.apply_review_result) and `autocode report-check`."""
    return (
        f"Criterion {criterion} cites evidence the runner did not verify ({ref!r}): cite check:<n> "
        "naming a listed check, runner-written evidence under .autocode/, or verified design evidence"
    )


def public_unbound_events(report: dict) -> list[tuple[str, str]]:
    """Criterion event citations ``unbound_refs`` refuses whatever the runner attaches: (criterion id, ref).

    Decided before the runner binds the checks, from the report alone (#3891). A listed check's own
    ``event:<id>`` and an in-range ``check:<n>`` are bound. A bare ``event:`` in a criterion is never bound:
    the runner swaps a bare check's for the ID of the run it attaches, which the stage cannot see. So a
    concrete ``event:<id>`` no check names is decidable only while no listed check is a bare ``event:``.
    Prose and file citations are not decided here (a runner receipt can make one count)."""
    raw = report.get("checks")
    checks = raw if isinstance(raw, list) else []
    listed = {check.get("evidence_ref") for check in checks if isinstance(check, dict)}
    bare_check = "event:" in listed

    def named_check(ref) -> bool:  # as ``resolve`` reads check:<n>
        match = REFERENCE.fullmatch(ref.strip())
        return bool(match) and 1 <= int(match[1]) <= len(checks) and isinstance(checks[int(match[1]) - 1], dict)

    rows = [row for row in report.get("criterion_results") or [] if isinstance(row, dict) and "id" in row]
    allowed = (listed - {"event:"}) | {
        ref for row in rows for ref in row.get("evidence_refs") or [] if isinstance(ref, str) and named_check(ref)
    }
    return [
        (criterion, ref)
        for criterion, ref in unbound_refs({"criterion_results": rows}, allowed)
        if ref == "event:" or (ref.startswith("event:") and not bare_check)
    ]


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
