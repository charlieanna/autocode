"""Deterministic classification of rejected stage attempts into one fixed taxonomy (#3346).

Turns free-form ``rejection_reason`` / ``timeout_kind`` / ``timeout_reason`` strings recorded on
failed or timed-out stage attempts into one of the seven fixed classes measured in the
2026-11-11 efficiency note and #3344:

- ``provider_timeout``: idle, first-event, tool, or non-terminal stream stalls (#3309)
- ``stage_wall``: hard stage runtime deadline limits (#3284)
- ``not_a_report``: non-JSON output, leaked markup, repair edit syntax/application failures, truncation
- ``schema``: JSON Schema validation failures (``$:`` / ``$.`` clauses, shared with ``autocode_recovery_history``)
- ``contract_rule``: semantic/contract/citation/checklist/reproduction/read-only rules on a valid JSON report
- ``evidence_not_authenticated``: unverified check claims, missing capture receipts, or missing tool events
- ``other``: operator cancellations, unlabelled non-zero exits, or anything outside the classes above

Pure stdlib-only helpers: imports nothing from AutoCode so every layer and report tool can share it.
"""

from __future__ import annotations

import re

CLASSES: tuple[str, ...] = (
    "provider_timeout",
    "stage_wall",
    "not_a_report",
    "schema",
    "contract_rule",
    "evidence_not_authenticated",
    "other",
)

# Classes (beside report_repair) where a model ran to completion and emitted an answer that
# AutoCode's format, schema, evidence, or contract gates rejected (#3344, #3346).
ANSWER_CLASSES: frozenset[str] = frozenset(
    {
        "not_a_report",
        "schema",
        "contract_rule",
        "evidence_not_authenticated",
    }
)

_WRAPPED_ARCHIVE = re.compile(r"^Completed \w+ output was rejected \((?P<why>.+)\);\s*attempt archived\b", re.S)
_WRAPPED_GENERIC = re.compile(r"^Completed \w+ output was rejected \((?P<why>.+)\)(?:;|\.|$)", re.S)
_REPORT_EDIT = re.compile(r"^Report edits?\b", re.IGNORECASE)

_STAGE_WALL_MARKERS = (
    "hard runtime limit",
    "hard time limit",
    "stage exceeded its ",
    "exceeded its hard",
)

_PROVIDER_TIMEOUT_KINDS = frozenset({"idle", "first_event", "tool"})
_PROVIDER_TIMEOUT_MARKERS = (
    "timed-out non-terminal",
    "no provider activity for",
    "no first provider event",
    "provider tool call exceeded",
    "saved repair timeout",
    "stage timed out",
    "nonterminal report repair",
    "provider timeout",
)

_NOT_A_REPORT_MARKERS = (
    "is not a json report",
    "is not valid json",
    "is not a json object",
    "instead of the json report",
    "leaked transport markup",
    "jsondecodeerror",
    "expecting value",
    "extra data:",
    "unterminated string",
    "invalid control character",
    "no completed response is available",
    "repair report is empty",
    "output token limit",
    "not in a form autocode can apply",
)

_EVIDENCE_MARKERS = (
    "not supported by an exact executed",
    "no independently executed validator tool event",
    "no independently executed tester tool event",
    "check needs a command and evidence reference",
    "check exit code must be an integer",
    "require capture receipts",
    "executed check receipt",
    "cannot read check receipt",
    "check receipt",
    "not authenticated",
    "unauthenticated",
    "no authenticated",
    "requires authenticated",
)

_CONTRACT_MARKERS = (
    "source_quote",
    "reproduced bug",
    "reproduction claims",
    "acceptance_criteri",
    "acceptance criterion",
    "criterion ",
    "criteria ",
    "milestone",
    "requirement_trace",
    "citation",
    "checklist",
    "obligation",
    "dag",
    "worked example",
    "backtick",
    "literal",
    "finding",
    "supersedes",
    "unchanged_review",
    "read-only stage",
    "contract ",
)


def unwrap_rejection(text: object) -> str:
    """Return normalized rejection text with the outer ``Completed <stage> output was rejected (...)`` removed."""
    if not isinstance(text, str) or not text.strip():
        return ""
    collapsed = " ".join(text.split())
    matched = _WRAPPED_ARCHIVE.match(collapsed) or _WRAPPED_GENERIC.match(collapsed)
    return matched.group("why").strip() if matched else collapsed


def is_schema_rejection(text: object) -> bool:
    """Return True when ``text`` contains JSON Schema path/root validation diagnostics (``$:`` or ``$.``)."""
    return isinstance(text, str) and ("$:" in text or "$." in text)


def classify(
    reason: object = None,
    *,
    timeout_kind: object = None,
    timeout_reason: object = None,
    timed_out: bool = False,
) -> str:
    """Classify a failed or timed-out attempt into one fixed class from ``CLASSES``.

    ``stage_wall`` (#3284) is checked before ``provider_timeout`` (#3309) because hard deadline
    archival shares the generic ``Timed-out non-terminal provider request...`` prefix in
    ``rejection_reason`` while ``timeout_kind == "stage"`` / ``timeout_reason`` distinguishes it.
    """
    raw = " ".join(reason.split()) if isinstance(reason, str) and reason.strip() else ""
    t_reason = " ".join(timeout_reason.split()) if isinstance(timeout_reason, str) and timeout_reason.strip() else ""
    combined_lower = f"{raw}\n{t_reason}".lower()

    if timeout_kind == "stage" or any(marker in combined_lower for marker in _STAGE_WALL_MARKERS):
        return "stage_wall"

    if (
        timeout_kind in _PROVIDER_TIMEOUT_KINDS
        or bool(timed_out)
        or any(marker in combined_lower for marker in _PROVIDER_TIMEOUT_MARKERS)
    ):
        return "provider_timeout"

    unwrapped = unwrap_rejection(raw)
    unwrapped_lower = unwrapped.lower()

    if _REPORT_EDIT.match(unwrapped) or any(marker in unwrapped_lower for marker in _NOT_A_REPORT_MARKERS):
        return "not_a_report"

    if is_schema_rejection(unwrapped):
        return "schema"

    if any(marker in unwrapped_lower for marker in _EVIDENCE_MARKERS):
        return "evidence_not_authenticated"

    if any(marker in unwrapped_lower for marker in _CONTRACT_MARKERS):
        return "contract_rule"

    return "other"
