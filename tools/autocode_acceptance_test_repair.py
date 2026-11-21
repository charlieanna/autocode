"""Bind an accepted independent review's protected-test repair to ``write_acceptance_tests`` (#3350).

When spec-first ``write_acceptance_tests`` writes a test before product code exists, the base check
only sees that the test fails on base. Two defects can only be observed once candidate code exists:

1. ``contradicts_case``: the protected test asserts behavior the approved criterion does not state
   (for example, inventing a host-fallback exception when ``/proc`` is absent instead of testing
   ``/proc`` parsing).
2. ``broken_fixture``: the protected test fails below its base-failure line because of a bug in the
   test or its fixture rather than in the product (for example, reading ``stages[-1]`` after a
   stage transition appended another record).

Because the Builder is forbidden from editing protected ``write_acceptance_tests`` lines, only an
accepted, read-only independent review or resolution (``tester``, ``review_checkpoint``,
``review_completion``, ``resolve``) on the current contract and source may authorize a repair,
sending the batch back to ``write_acceptance_tests`` before the Builder runs, within ``MAX_REPAIRS``
rounds per ``(contract_hash, case_id)``.

Pure domain module: imports only ``autocode_util`` so lower and higher layers can share one rule.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_run_status as run_status
    import autocode_util as util

FIELD = "acceptance_test_repairs"
RECEIPT_FIELD = "test_repair"
HISTORY_KEY = "test_repairs"
PENDING_KEY = "pending_test_repair"
MAX_REPAIRS = 2
WRONG_IMPLEMENTATION = "accepts_wrong_implementation"
# accepts_wrong_implementation: the runner saw a wrong implementation of the case pass every acceptance test
# (autocode_wrong_implementations, #3977); only a case with such a recorded survivor may be repaired for it.
KINDS = ("contradicts_case", "broken_fixture", WRONG_IMPLEMENTATION)
REVIEW_STAGES = ("tester", "review_checkpoint")
DECISION_STAGES = ("review_completion", "resolve")
ALL_STAGES = (*REVIEW_STAGES, *DECISION_STAGES)

SCHEMA: dict[str, Any] = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["case_id", "kind", "reason"],
        "properties": {
            "case_id": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "reason": {"type": "string"},
        },
    },
}

AUDIT_NOTE = (
    "\nPROTECTED ACCEPTANCE TESTS: This spec-first run fixed pre-build Tester files against Builder "
    "edits (`acceptance_tests` in CURRENT HANDOFF DATA). Audit each protected test against its exact "
    "approved criterion. Never pass a criterion whose test substitutes or adds unapproved behavior "
    "(such as asserting an unspecified fallback or host error instead of the approved case), and do "
    "not ask the Builder to edit a protected test line. When a protected test contradicts/substitutes "
    'for the approved case (`kind="contradicts_case"`) or fails due to a fixture/assertion bug in '
    'the test itself rather than a product defect (`kind="broken_fixture"`), or accepts a wrong implementation '
    "the runner showed passing every acceptance test of its case (WRONG IMPLEMENTATIONS below, "
    '`kind="accepts_wrong_implementation"`), fail/rework the task '
    "and list `{case_id, kind, reason}` in `acceptance_test_repairs` so the runner routes the repair "
    "to the independent Tester before the Builder.\n"
)


def _intact(path: Any, digest: Any) -> bool:
    try:
        return isinstance(path, str) and bool(path) and isinstance(digest, str) and util.file_hash(path) == digest
    except (OSError, ValueError, TypeError):
        return False


def _fields(schema: dict[str, Any], stage: str) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return {}
    if stage == "review_checkpoint":
        nested = (props.get("validation") or {}).get("properties")
        return nested if isinstance(nested, dict) else {}
    return props


def _container(schema: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage == "review_checkpoint":
        nested = (schema.get("properties") or {}).get("validation")
        return nested if isinstance(nested, dict) else {}
    return schema if isinstance(schema, dict) else {}


def extend_schema(schema: dict[str, Any], covered_case_ids: set[str] | list[str], stage: str) -> dict[str, Any]:
    """Offer ``acceptance_test_repairs`` constrained to covered spec-first case IDs; omit when none exist."""
    if not isinstance(schema, dict):
        return schema
    covered = sorted({str(cid) for cid in (covered_case_ids or ()) if str(cid).strip()})
    if stage not in ALL_STAGES or not covered:
        if FIELD not in _fields(schema, stage):
            return schema
        result = copy.deepcopy(schema)
        props = _fields(result, stage)
        props.pop(FIELD, None)
        container = _container(result, stage)
        if isinstance(container.get("required"), list):
            container["required"] = [key for key in container["required"] if key != FIELD]
        return result
    result = copy.deepcopy(schema)
    props = _fields(result, stage)
    if not props:
        return result
    entry = copy.deepcopy(SCHEMA)
    entry["items"]["properties"]["case_id"] = {"type": "string", "enum": covered}
    props[FIELD] = entry
    return result


generation_schema = extend_schema


def normalize_absent(value: Any, schema: Any, record: Any = None) -> Any:
    """Default omitted ``acceptance_test_repairs`` to ``None`` under strict wire schemas."""
    del record
    if not isinstance(value, dict) or not isinstance(schema, dict):
        return value
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    top_field = props.get(FIELD) if isinstance(props, dict) else None
    val_props = ((props.get("validation") or {}).get("properties") or {}) if isinstance(props, dict) else {}
    val_field = val_props.get(FIELD) if isinstance(val_props, dict) else None
    top_nullable = (
        isinstance(top_field, dict) and isinstance(top_field.get("type"), list) and "null" in top_field["type"]
    )
    val_nullable = (
        isinstance(val_field, dict)
        and isinstance(val_field.get("type"), list)
        and "null" in val_field["type"]
        and isinstance(value.get("validation"), dict)
    )
    if not top_nullable and not val_nullable:
        return value
    result = copy.deepcopy(value)
    if top_nullable:
        result.setdefault(FIELD, None)
    if val_nullable and isinstance(result.get("validation"), dict):
        result["validation"].setdefault(FIELD, None)
    return result


def survivor_case_ids(state: RunState) -> set[str]:
    """Cases whose acceptance tests the runner saw accept a wrong implementation, under the current contract."""
    study = (state.get("acceptance_tests") or {}).get("wrong_implementations") or {}
    if not isinstance(study, dict) or study.get("contract_hash") != (state.get("goal_contract") or {}).get("hash"):
        return set()
    return {str(row.get("case_id")) for row in study.get("survivors") or [] if isinstance(row, dict)}


def validate_report(
    data: dict[str, Any],
    *,
    stage: str,
    covered_case_ids: set[str] | list[str] | None = None,
    survivor_case_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Validate ``acceptance_test_repairs`` as a pure function of the report and covered cases.

    ``survivor_case_ids`` (``survivor_case_ids(state)``) limits ``accepts_wrong_implementation`` to cases with a
    recorded survivor; None, where no run state is at hand (a pre-submit report check), leaves that to the
    final validation, which always passes it."""
    raw = data.get(FIELD)
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{FIELD} must be an array")
    covered = {str(cid) for cid in covered_case_ids} if covered_case_ids is not None else None
    if covered is not None and not covered:
        raise ValueError(f"{FIELD} may name only protected spec-first acceptance cases; none are active")
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"Each {FIELD} entry must be an object")
        cid = str(item.get("case_id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not cid:
            raise ValueError(f"Each {FIELD} entry requires a non-empty case_id")
        if cid in seen:
            raise ValueError(f"{FIELD} lists duplicate case_id {cid!r}")
        if covered is not None and cid not in covered:
            raise ValueError(f"{FIELD} case_id {cid!r} is not a protected acceptance case {sorted(covered)}")
        if kind not in KINDS:
            raise ValueError(f"{FIELD} kind for {cid!r} must be one of {list(KINDS)}")
        if not reason:
            raise ValueError(f"{FIELD} for {cid!r} requires a non-empty reason")
        if kind == WRONG_IMPLEMENTATION and survivor_case_ids is not None and cid not in survivor_case_ids:
            raise ValueError(
                f"{FIELD} kind {WRONG_IMPLEMENTATION!r} for {cid!r} needs a wrong implementation the runner saw pass "
                "every acceptance test of that case; none is recorded"
            )
        seen.add(cid)
        normalized.append({"case_id": cid, "kind": kind, "reason": reason})
    if stage in REVIEW_STAGES:
        if data.get("verdict") == "PASS":
            raise ValueError(f"A review naming {FIELD} cannot report verdict PASS")
        passing = sorted(
            str(row.get("id"))
            for row in (data.get("criterion_results") or [])
            if isinstance(row, dict) and row.get("id") in seen and row.get("status") == "PASS"
        )
        if passing:
            raise ValueError(f"Criteria listed in {FIELD} cannot have status PASS: {passing}")
    elif stage in DECISION_STAGES and data.get("status") in ("COMPLETE", "TASK_COMPLETE"):
        raise ValueError(f"A decision naming {FIELD} cannot report {data.get('status')}")
    return normalized


def spent_counts(state: RunState, contract_hash: str | None) -> dict[str, int]:
    """Completed post-build test repairs per ``case_id`` under ``contract_hash``."""
    counts: dict[str, int] = {}
    if not contract_hash:
        return counts
    saved = state.get("acceptance_tests") or {}
    all_receipts = [*(saved.get("receipt_history") or []), *((saved.get("receipts") or {}).values())]
    seen_outputs: set[str] = set()
    for receipt in all_receipts:
        if not isinstance(receipt, dict) or receipt.get("contract_hash") != contract_hash:
            continue
        repair = receipt.get(RECEIPT_FIELD)
        output = receipt.get("output")
        if not isinstance(repair, dict):
            continue
        if isinstance(output, str) and output:
            if output in seen_outputs:
                continue
            seen_outputs.add(output)
        for cid in repair.get("requested_case_ids") or repair.get("case_ids") or []:
            counts[str(cid)] = counts.get(str(cid), 0) + 1
    return counts


def _batch_case_ids(
    requested: list[str], cases: list[dict], receipts_map: dict[str, dict], contract_hash: str
) -> list[str]:
    """Expand ``requested`` case IDs to full receipt batches so whole-batch receipts replace cleanly."""
    wanted = set(requested)
    expanded = set(wanted)
    for receipt in (receipts_map or {}).values():
        if not isinstance(receipt, dict) or receipt.get("contract_hash") != contract_hash:
            continue
        batch = [str(cid) for cid in (receipt.get("case_ids") or []) if str(cid)]
        if wanted & set(batch):
            expanded.update(batch)
    ordered = [case["id"] for case in cases if case.get("id") in expanded]
    for cid in sorted(expanded):
        if cid not in ordered:
            ordered.append(cid)
    return ordered


def _stage_intact(state: RunState, output: str, stage: str, source_revision: str, contract_hash: str) -> bool:
    matches = [row for row in (state.get("stages") or []) if isinstance(row, dict) and row.get("output") == output]
    if len(matches) != 1:
        return False
    record = matches[0]
    actual = record.get("original_stage") or str(record.get("stage") or "").removesuffix("_report_repair")
    return (
        actual == stage
        and record.get("source_revision") == source_revision
        and record.get("contract_hash") == contract_hash
        and record.get("exit_code") == 0
        and record.get("accounted") is True
        and (record.get("changed_files") or []) == []
        and not any(record.get(key) for key in ("rejected", "rolled_back", "interrupted", "abandoned", "timed_out"))
    )


def _fulfilled(state: RunState, binding: dict[str, Any]) -> bool:
    saved = state.get("acceptance_tests") or {}
    for receipt in [*(saved.get("receipt_history") or []), *((saved.get("receipts") or {}).values())]:
        if isinstance(receipt, dict) and receipt.get(RECEIPT_FIELD) == binding:
            return True
    cw = saved.get("cannot_write")
    return isinstance(cw, dict) and cw.get(RECEIPT_FIELD) == binding


def _record_event(
    state: RunState,
    repairs: list[dict[str, str]],
    record: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any]:
    contract = state.get("goal_contract") or {}
    task = state.get("current_task") or {}
    output = str(record.get("output") or "")
    if not output or not Path(output).is_file():
        raise util.Paused("PAUSED_STALE_HANDOFF", "Acceptance-test repair requires a saved review output")
    saved = state.setdefault("acceptance_tests", {"receipts": {}})
    history = saved.setdefault(HISTORY_KEY, [])
    existing = next((row for row in reversed(history) if row.get("review_output") == output), None)
    if existing is not None:
        return existing
    contract_hash = str(contract.get("hash") or "")
    requested_ids = [row["case_id"] for row in repairs]
    spent = spent_counts(state, contract_hash)
    used = max((spent.get(cid, 0) for cid in requested_ids), default=0)
    under_limit = used < MAX_REPAIRS
    binding = {
        "task_id": task.get("id"),
        "contract_hash": contract_hash,
        "contract_revision": contract.get("revision"),
        "source_revision": record.get("source_revision"),
        "stage": stage,
        "review_output": output,
        "review_sha256": util.file_hash(output),
        "requested_case_ids": requested_ids,
    }
    entry = {
        "binding": binding,
        "review_output": output,
        "repairs": copy.deepcopy(repairs),
        "action": "writer" if under_limit else "exhausted",
        "round": min(used + 1, MAX_REPAIRS),
        "round_limit": MAX_REPAIRS,
        "at": util.now(),
    }
    history.append(entry)
    if not under_limit:
        exhausted = sorted(cid for cid in requested_ids if spent.get(cid, 0) >= MAX_REPAIRS)
        summary = "; ".join(f"{row['case_id']} ({row['kind']}): {row['reason']}" for row in repairs)
        raise util.Paused(
            "PAUSED_REPEATED_FAILURE",
            f"Protected acceptance-test repairs reached {MAX_REPAIRS} round(s) for {exhausted}: {summary}",
        )
    return entry


def route_from_review(
    state: RunState,
    validation: dict[str, Any],
    record: dict[str, Any],
    *,
    covered_case_ids: set[str] | list[str],
) -> bool:
    """Route a ``tester`` / ``review_checkpoint`` report with ``acceptance_test_repairs`` back to the writer."""
    actual = str(record.get("original_stage") or str(record.get("stage") or "").removesuffix("_report_repair"))
    if actual not in REVIEW_STAGES:
        return False
    repairs = validate_report(
        validation, stage=actual, covered_case_ids=covered_case_ids, survivor_case_ids=survivor_case_ids(state)
    )
    if not repairs:
        return False
    _record_event(state, repairs, record, stage=actual)
    summary = "; ".join(f"{row['case_id']} ({row['kind']}): {row['reason']}" for row in repairs)
    run_status.running(
        state,
        "EXECUTING",
        next_stage="builder",
        next_action=f"Repair protected acceptance tests identified by independent review: {summary}",
    )
    return True


def record_decision(
    state: RunState,
    decision: dict[str, Any],
    record: dict[str, Any],
    *,
    covered_case_ids: set[str] | list[str],
) -> bool:
    """Record ``acceptance_test_repairs`` from an accepted ``review_completion`` or ``resolve`` decision."""
    actual = str(record.get("original_stage") or str(record.get("stage") or "").removesuffix("_report_repair"))
    if actual not in DECISION_STAGES:
        return False
    repairs = validate_report(
        decision, stage=actual, covered_case_ids=covered_case_ids, survivor_case_ids=survivor_case_ids(state)
    )
    if not repairs:
        return False
    _record_event(state, repairs, record, stage=actual)
    return True


def queued(state: RunState, record: dict[str, Any]) -> bool:
    """True when ``record`` already queued a protected acceptance-test repair on its source revision."""
    return any(
        isinstance(row, dict)
        and row.get("action") == "writer"
        and row.get("review_output") == record.get("output")
        and isinstance(row.get("binding"), dict)
        and row["binding"].get("source_revision") == record.get("source_revision")
        for row in ((state.get("acceptance_tests") or {}).get(HISTORY_KEY) or [])
    )


def _allowed_task_ids(state: RunState, contract_hash: str, source_revision: str) -> set[str]:
    task = state.get("current_task") or {}
    ids = {str(task["id"])} if task.get("id") else set()
    if task.get("source_revision") in (None, source_revision):
        for archived in reversed(state.get("task_archive") or []):
            if isinstance(archived, dict) and archived.get("contract_hash") == contract_hash and archived.get("id"):
                ids.add(str(archived["id"]))
    return ids


def pending(
    state: RunState, cases: list[dict], *, receipts_map: dict[str, dict] | None = None
) -> dict[str, Any] | None:
    """Return the current authenticated protected-test repair that still owes ``write_acceptance_tests``."""
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    saved = state.get("acceptance_tests") or {}
    history = saved.get(HISTORY_KEY) or []
    if (
        not history
        or not cases
        or not task.get("id")
        or task.get("kind") != "implement"
        or contract.get("approval_status") != "approved"
    ):
        return None
    contract_hash = str(contract.get("hash") or "")
    contract_revision = contract.get("revision")
    latest = next(
        (
            row
            for row in reversed(history)
            if isinstance(row, dict)
            and row.get("action") == "writer"
            and isinstance(row.get("binding"), dict)
            and row["binding"].get("contract_hash") == contract_hash
            and row["binding"].get("contract_revision") == contract_revision
        ),
        None,
    )
    if latest is None:
        return None
    base_binding = latest["binding"]
    source = str(base_binding.get("source_revision") or "")
    output = str(base_binding.get("review_output") or "")
    stage = str(base_binding.get("stage") or "")
    if (
        not source
        or task.get("source_revision") not in (None, source)
        and (state.get("validation") or {}).get("source_revision") != source
    ):
        return None
    allowed_tasks = _allowed_task_ids(state, contract_hash, source)
    if str(base_binding.get("task_id") or "") not in allowed_tasks:
        return None
    if not _intact(output, base_binding.get("review_sha256")):
        return None
    if not _stage_intact(state, output, stage, source, contract_hash):
        return None
    receipts_map = receipts_map if receipts_map is not None else (saved.get("receipts") or {})
    requested = [str(cid) for cid in (base_binding.get("requested_case_ids") or []) if str(cid)]
    expanded = _batch_case_ids(requested, cases, receipts_map, contract_hash)
    if not expanded:
        return None
    spent = spent_counts(state, contract_hash)
    if any(spent.get(cid, 0) >= MAX_REPAIRS for cid in requested):
        return None
    binding = {
        **base_binding,
        "case_ids": expanded,
    }
    if _fulfilled(state, binding):
        return None
    proof = state.get("regression_proof") or {}
    return {
        "binding": binding,
        "repairs": copy.deepcopy(latest.get("repairs") or []),
        "round": latest.get("round", 1),
        "round_limit": latest.get("round_limit", MAX_REPAIRS),
        "proof_path": proof.get("path") if proof.get("source_revision") == source else None,
    }


def packet(state: RunState, *, receipts_map: dict[str, dict] | None = None) -> dict[str, Any] | None:
    """Only the repair pinned when routing may enter ``write_acceptance_tests``'s handoff."""
    bound = (state.get("acceptance_tests") or {}).get(PENDING_KEY)
    if not isinstance(bound, dict) or not isinstance(bound.get("binding"), dict):
        return None
    cases = [{"id": cid} for cid in bound["binding"].get("case_ids") or []]
    current = pending(state, cases, receipts_map=receipts_map)
    return current if current and current["binding"] == bound["binding"] else None


def owns_builder_round(state: RunState, revision: str | None) -> bool:
    """True for the first Builder round on ``revision`` immediately after ``write_acceptance_tests`` repaired tests."""
    if not revision:
        return False
    contract = state.get("goal_contract") or {}
    task = state.get("current_task") or {}
    contract_hash = contract.get("hash")
    if not contract_hash or not task.get("id"):
        return False
    if any(
        isinstance(row, dict)
        and (row.get("original_stage") or row.get("stage")) == "builder"
        and row.get("task_id") == task.get("id")
        and row.get("source_revision") == revision
        and not row.get("rejected")
        and not row.get("abandoned")
        for row in (state.get("stages") or [])
    ):
        return False
    receipts = (state.get("acceptance_tests") or {}).get("receipts") or {}
    allowed_tasks = _allowed_task_ids(state, str(contract_hash), str(task.get("source_revision") or revision))
    return any(
        isinstance(receipt, dict)
        and receipt.get("contract_hash") == contract_hash
        and receipt.get("source_revision") == revision
        and isinstance(receipt.get(RECEIPT_FIELD), dict)
        and str(receipt[RECEIPT_FIELD].get("task_id") or "") in allowed_tasks
        for receipt in receipts.values()
    )
