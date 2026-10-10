"""Keep current validation across a read-only completion interruption.

Changed or unbound evidence is archived and a completion retry goes through
the Validator first. Only existing state fields are read or written here.
"""

from pathlib import Path

try:
    from .autocode_util import file_hash
except ImportError:
    from autocode_util import file_hash


def current(state, record):
    validation = state.get("validation") or {}
    contract = state.get("goal_contract") or {}
    task = state.get("current_task") or {}
    if (
        not validation
        or record.get("changed_files")
        or validation.get("source_revision") != record.get("source_revision")
        or validation.get("criteria_revision") != state.get("criteria_revision")
        or (contract and any(validation.get("contract_" + key) != contract.get(key) for key in ("revision", "hash")))
        or (task and validation.get("task_id") != task.get("id"))
    ):
        return False
    pins = validation.get("evidence_hashes") or {}
    try:
        return bool(pins) and all(Path(path).is_file() and file_hash(path) == sha for path, sha in pins.items())
    except OSError:
        return False


def permission_retry(state, record, *, review_stage="sol"):
    completion = record["stage"] in ("astra_review", "astra_checkpoint")
    if completion and current(state, record):
        return record["stage"]
    if state.get("validation"):
        state.setdefault("validation_archive", []).append(
            {
                "reason": "Interrupted attempt changed or invalidated validation evidence",
                "validation": state.pop("validation"),
            }
        )
    state["human_reviews"] = {}
    state.pop("displayed_review", None)
    return review_stage if completion else record["stage"]
