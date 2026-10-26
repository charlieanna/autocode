"""Runner-bound report identity: adopt the launched identity over a mistyped echo (#2135).

Review reports echo the contract hash, contract revision and task id so a report
written against a stale contract is caught. The generation schema already binds
each to the one value the runner launched with, so a copy error there cost a paid
report repair. The report's identity comes from the launch instead: the active
attempt the runner admitted, its recorded launch identity and the bound schema,
all still current. Anything less keeps the existing refusal.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from .autocode_util import read as read_json
except ImportError:
    from autocode_util import read as read_json

FIELDS = ("contract_hash", "contract_revision", "task_id")
STAGES = ("sol", "astra_review", "astra_checkpoint")


def current_identity(state: RunState):
    contract = state.get("goal_contract") or {}
    return {
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
        "task_id": (state.get("current_task") or {}).get("id", ""),
    }


def _launched(record):
    return {
        "contract_hash": record.get("contract_hash"),
        "contract_revision": record.get("contract_revision"),
        "task_id": record.get("task_id", ""),
    }


def _prior(state: RunState):
    """Identities this run used before; an echo of one is stale, never a typo."""
    contracts = state.get("contract_history") or []
    return {
        "contract_hash": [row.get("hash") for row in contracts if isinstance(row, dict)],
        "contract_revision": [row.get("revision") for row in contracts if isinstance(row, dict)],
        "task_id": [row.get("id") for row in state.get("task_archive") or [] if isinstance(row, dict)],
    }


def bind(value, schema, record, state: RunState):
    """Return the report with its identity from the launch binding, and the corrections made.

    Corrects only the active, admitted attempt of a review stage whose recorded
    launch identity and bound schema both name today's contract and task. An
    echo of an identity this run used before is a stale report and is left for
    validation to refuse; older records without a launch identity are unchanged.
    """
    stage = record.get("original_stage") or str(record.get("stage", "")).removesuffix("_report_repair")
    if stage not in STAGES or not state or not isinstance(value, dict):
        return value, []
    active = state.get("active_stage") or {}
    if not record.get("output") or any(active.get(key) != record.get(key) for key in ("output", "events")):
        return value, []
    if record.get("contract_hash") is None or record.get("contract_revision") is None:
        return value, []
    current = current_identity(state)
    if _launched(record) != current or None in current.values():
        return value, []
    properties = schema.get("properties", {})
    prior = _prior(state)
    corrections = []
    for field in FIELDS:
        if field not in value or value[field] == current[field]:
            continue
        if properties.get(field, {}).get("enum") != [current[field]]:
            return value, []
        if any(value[field] == old for old in prior[field]):
            return value, []
        corrections.append({"field": field, "reported": value[field], "adopted": current[field]})
    if not corrections:
        return value, []
    result = copy.deepcopy(value)
    for row in corrections:
        result[row["field"]] = row["adopted"]
    return result, corrections


def adopt(value, record, state: RunState | None):
    """Load-time entry: correct against the record's saved schema and keep the receipt on the record.

    The loader preserves the raw response as ``reported_output`` whenever the value changes.
    """
    if not state or not record.get("schema"):
        return value
    value, corrections = bind(value, read_json(Path(record["schema"])), record, state)
    if corrections:
        record["identity_corrections"] = corrections
    return value
