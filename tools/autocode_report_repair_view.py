"""Typed, recorded rejection diagnostics; no raw reports or action authority."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REPLAY = re.compile(
    r"\ACheck `(?P<command>.*?)` "
    r"(?P<claim>was reported as exit -?\d{1,9}, but when the runner re-ran it|"
    r"is required by the approved verification methods or the current task's validation plan "
    r"\(the Validator did not report it\) and must exit 0, but when the runner ran it) "
    r"from the repository root in a clean copy of the current source it (?P<outcome>.*)",
    re.DOTALL,
)


def _digest(value):
    return value if isinstance(value, str) and _HASH.fullmatch(value) else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _reason(value):
    if not isinstance(value, str) or not value:
        return None
    result = {
        "category": "unclassified",
        "error_sha256": hashlib.sha256(value.encode()).hexdigest(),
        "command_sha256": None,
        "timeout_seconds": None,
        "exit_code": None,
        "planned": None,
    }
    # Error strings can contain whole commands, captured output or malformed model
    # text. Only recognize Core's fixed wrappers; never copy any of that text out.
    if len(value) > 65536:
        return result
    match = _REPLAY.match(value)
    if match:
        outcome = match["outcome"]
        result["command_sha256"] = hashlib.sha256(match["command"].encode()).hexdigest()
        result["planned"] = match["claim"].startswith("is required")
        if timeout := re.match(r"timed out after (\d{1,9}) seconds(?:[.;]|$)", outcome):
            result.update(category="replay_timeout", timeout_seconds=int(timeout[1]))
        elif exited := re.match(r"exited (-?\d{1,9})(?:[.;]|$)", outcome):
            result.update(category="replay_failed", exit_code=int(exited[1]))
        elif outcome.startswith("has no complete owned-command evidence"):
            result["category"] = "replay_receipt_incomplete"
        elif outcome.startswith("could not run ("):
            result["category"] = "replay_execution_error"
    elif value.startswith(
        (
            "Check is not supported by an exact executed Validator event",
            "No independently executed Validator tool event matches receipt",
        )
    ):
        result["category"] = "evidence_binding"
    elif re.match(
        r"\$(?:\.[A-Za-z_][\w]*|\[\d+\])*: (?:missing |expected |invalid enum|unexpected fields:|too (?:few|many) items)",
        value,
    ):
        result["category"] = "report_schema"
    elif value.startswith("Technically passing validation lists failed "):
        result["category"] = "uncited_failed_check"
    elif value.startswith("Technically passing validation cites failed "):
        result["category"] = "cited_failed_check"
    return result


def project(state: RunState, *, stale=False):
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict):
        return None
    original = pending.get("original")
    stage = original.get("stage") if isinstance(original, dict) else None
    if not isinstance(stage, str) or not re.fullmatch(r"[a-z_]{1,64}", stage):
        return None
    source = _digest(original.get("source_revision"))
    contract = _object(state.get("goal_contract")).get("hash")
    task = _object(state.get("current_task"))
    changed = (
        stale
        or stage != state.get("next_stage")
        or pending.get("contract_hash") != contract
        or (task.get("id") is not None and original.get("task_id") != task["id"])
        or (task.get("source_revision") is not None and source != task["source_revision"])
        or (_object(pending.get("latest_rejected")).get("source_revision", source) != source)
    )
    freshness = "unavailable" if source is None else "stale" if changed else "recorded"
    active = _object(state.get("active_stage"))
    return {
        "stage": stage,
        "source_revision": source,
        "freshness": freshness,
        "active": freshness == "recorded"
        and state.get("status") == "RUNNING"
        and active.get("report_only") is True
        and active.get("original_stage") == stage,
        "attempts": _count(pending.get("attempts")),
        "limit": _count(_object(_object(state.get("settings")).get("report_repair")).get("max_attempts")),
        "reason": _reason(pending.get("error")) if source else None,
        "original_reason": _reason(pending.get("original_error")) if source else None,
    }
