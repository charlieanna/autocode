"""Pure, additive public projections for one run's replay/export interface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def completion_policy(state: RunState):
    """The same replay completion boundary for full status and saved-run inventory."""
    settings = state.get("settings")
    authority = settings.get("replay_recording") if isinstance(settings, dict) else None
    observed = progress(state)
    invalid = isinstance(settings, dict) and "replay_recording" in settings and observed is None
    simulated = isinstance(authority, dict) and authority.get("checks") == "recorded"
    return {
        "progress": observed,
        "invalid": invalid,
        "simulated": simulated,
        "completion_allowed": not invalid and not simulated,
    }


RECORD_FIELDS = (
    "prompts_hash",
    "role",
    "route_role",
    "stage",
    "iteration",
    "engine",
    "provider",
    "reasoning_effort",
    "launch_route",
    "report_only",
    "original_stage",
    "criteria_revision",
    "task_id",
    "contract_hash",
    "expected_session",
    "thread_id",
    "output_mode",
    "supports_sessions",
    "events",
    "output",
    "reported_output",
    "prompt",
    "before_ref",
    "after_ref",
    "exit_code",
    "timed_out",
    "interrupted",
    "started_at",
    "capture_context",
    "job_source",
    "check_replay",
    "replay",
    "source_revision",
)
POLICY_KEYS = (
    "engine",
    "provider",
    "roles",
    "single_model_mode",
    "budget_origins",
    "effort_origins",
    "builder_retry",
    "route_ladders",
    "orchestration",
    "report_repair",
    "evidence_provenance",
    "milestone_checkpoints",
    "headroom",
    "regression",
    "context_soft_tokens",
    "rotation_after_input_tokens",
    "limits",
    "workflow",
    "joint_planning",
    "planning_flow",
    "adaptive_planning",
    "spec_first",
    "reviewer_routing",
    "conversation_handoff",
    "output_transport",
    "stuck_investigation",
    "protected_tests",
)


def _dict_rows(value):
    return isinstance(value, list) and all(isinstance(row, dict) for row in value)


def _optional_dict(value):
    return value is None or isinstance(value, dict)


def _projectable(state: RunState):
    """Keep corrupt replay inventories unavailable, never export a partial tape."""
    if not _dict_rows(state.get("stages", [])) or not _optional_dict(state.get("settings")):
        return False
    for key in ("contract_history", "user_events", "validation_archive", "regression_proofs"):
        value = state.get(key)
        if value is not None and not _dict_rows(value):
            return False
    if not all(_optional_dict(state.get(key)) for key in ("goal_contract", "validation", "regression_baseline")):
        return False
    contracts = [*(state.get("contract_history") or []), state.get("goal_contract") or {}]
    if any(not _optional_dict(row.get(key)) for row in contracts for key in ("approval_event", "body")):
        return False
    for event in state.get("user_events") or []:
        if event.get("kind") in ("answer", "delegated", "checkpoint_answer") and not _optional_dict(
            event.get("question")
        ):
            return False
    validations = [state.get("validation") or {}]
    for entry in state.get("validation_archive") or []:
        if not _optional_dict(entry.get("validation")):
            return False
        validations.append(entry.get("validation") or {})
    for validation in validations:
        replay = validation.get("check_replay")
        if not _optional_dict(replay):
            return False
        checks = (replay or {}).get("checks")
        if checks is not None and not _dict_rows(checks):
            return False
    return True


def progress(state: RunState):
    settings = state.get("settings") or {}
    stages = state.get("stages", [])
    if not isinstance(settings, dict) or not _dict_rows(stages):
        return None
    authority = settings.get("replay_recording")
    if not isinstance(authority, dict):
        return None
    ids = authority.get("dispatch_ids")
    digest = authority.get("sha256")
    if (
        not isinstance(ids, list)
        or not all(isinstance(value, str) and value for value in ids)
        or len(ids) != len(set(ids))
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or authority.get("checks") not in ("fresh", "recorded")
    ):
        return None
    rows: list = []
    for row in stages:
        replay = row.get("replay")
        if replay is None:
            continue
        if (
            not isinstance(replay, dict)
            or len(rows) >= len(ids)
            or replay.get("dispatch_id") != ids[len(rows)]
            or replay.get("manifest_sha256") != digest
            or type(replay.get("ordinal")) is not int
            or replay["ordinal"] != len(rows)
        ):
            return None
        rows.append(row)
    return {
        "manifest_sha256": digest,
        "checks": authority["checks"],
        "consumed": len(rows),
        "next_dispatch": ids[len(rows)] if len(rows) < len(ids) else "end",
    }


def _check_projection(state: RunState, records):
    """Expose only owned receipt references/pins, never raw command output."""
    result = []
    validations = [entry.get("validation") or {} for entry in state.get("validation_archive") or []]
    validations.append(state.get("validation") or {})
    seen = set()
    for ordinal, record in enumerate(records):
        for validation in validations:
            replay = validation.get("check_replay") or {}
            checks = replay.get("checks") or []
            if validation.get("output") != record.get("output") or not isinstance(checks, list) or not checks:
                continue
            output = checks[0].get("output")
            if not output:
                continue
            reference = replay.get("receipt_path")
            receipt = reference if isinstance(reference, str) and reference else None
            selector = receipt or (record.get("output"), util.digest(replay))
            if selector in seen:
                continue
            seen.add(selector)
            result.append(
                {
                    "dispatch": "dispatch-" + str(ordinal),
                    "purpose": "independent_clean_replay",
                    "command": "independent replay summary",
                    "repetition": 1,
                    "snapshot": "source-" + str(ordinal * 2 + 1),
                    "receipt": receipt,
                    "output": output,
                    "authentication": "intact",
                    "bindings": [],
                    "summary_sha256": util.digest(replay),
                    **(
                        {"summary_stem": Path(record["output"]).stem if "receipt_path" not in replay else None}
                        if not receipt
                        else {}
                    ),
                }
            )
    proofs = state.get("regression_proofs") or []
    baseline = state.get("regression_baseline") or {}
    for proof in proofs:
        if not proof.get("path"):
            continue
        match = next(
            (
                (index, record)
                for index, record in enumerate(records)
                if record.get("stage") in ("sol", "astra_checkpoint", "astra_review")
                and record.get("source_revision") == proof.get("source_revision")
                and record.get("started_at", "") >= proof.get("proved_at", "")
            ),
            None,
        )
        if match is None:
            continue
        ordinal, record = match
        common = {
            "dispatch": "dispatch-" + str(ordinal),
            "repetition": 1,
            "snapshot": "source-" + str(ordinal * 2),
            "authentication": "intact",
            "bindings": [],
        }
        if baseline.get("path"):
            result.append(
                {
                    **common,
                    "purpose": "regression_baseline",
                    "command": baseline.get("command") or "baseline",
                    "receipt": baseline["path"],
                    "output": None,
                    "receipt_sha256": baseline.get("sha256"),
                }
            )
        result.append(
            {
                **common,
                "purpose": "regression_proof",
                "command": "named regression proof",
                "receipt": proof["path"],
                "output": None,
            }
        )
    order = {"regression_baseline": 0, "regression_proof": 1, "independent_clean_replay": 2}
    result.sort(key=lambda row: (int(row["dispatch"].split("-")[1]), order[row["purpose"]]))
    for index, row in enumerate(result):
        row["id"] = "check-" + str(index)
        row["repetition"] = 1 + sum(
            prior["dispatch"] == row["dispatch"] and prior["purpose"] == row["purpose"] for prior in result[:index]
        )
    return result


def project(state: RunState, *, run_dir=None):
    """Expose no replay source when its inventories cannot be projected intact."""
    if not _projectable(state):
        return None
    try:
        return _project(state, run_dir=run_dir)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def _project(state: RunState, *, run_dir=None):
    """Export names only from this run; caller owns immutable payload inspection."""
    run_dir = str(run_dir) if run_dir is not None else state.get("run_dir")
    records = [
        {key: deepcopy(row[key]) for key in RECORD_FIELDS if key in row}
        for row in state.get("stages", [])
        if row.get("role") != "runner" and row.get("events")
    ]
    contracts = [*(state.get("contract_history") or []), state.get("goal_contract") or {}]
    actions = []
    for event in state.get("user_events") or []:
        kind = event.get("kind")
        anchor = next(
            (
                "dispatch-" + str(index)
                for index, row in enumerate(records)
                if row.get("started_at", "") > event.get("at", "")
            ),
            "end",
        )
        if kind == "goal_approval":
            token = event.get("token")
            contract = next((row for row in contracts if (row.get("approval_event") or {}).get("token") == token), None)
            if not contract:
                continue
            body = contract["body"]
            actions.append(
                {
                    "before_dispatch": anchor,
                    "kind": "approve_plan",
                    "payload": {},
                    "match": {
                        "kind": "approve_plan",
                        "plan": {
                            key: deepcopy(body.get(key))
                            for key in (
                                "acceptance_criteria",
                                "constraints",
                                "permission_boundaries",
                                "scope_exclusions",
                                "git_delivery",
                            )
                        },
                    },
                }
            )
        elif kind in ("answer", "delegated", "checkpoint_answer"):
            question = event.get("question") or {}
            fields = ("id", "question", "why", "options", "proposed_default")
            actions.append(
                {
                    "before_dispatch": anchor,
                    "kind": "answer",
                    "match": {"kind": "answer", "questions": [{key: deepcopy(question.get(key)) for key in fields}]},
                    "payload": {"question_id": event.get("question_id"), "text": event.get("text")},
                }
            )
        elif kind == "human_review":
            actions.append(
                {
                    "before_dispatch": anchor,
                    "kind": "approve_review",
                    "match": {"kind": "review", "criteria": [event.get("criterion")]},
                    "payload": {"criterion": event.get("criterion")},
                }
            )
    revisions = [state.get("base_commit")]
    checkpoints = state.get("code_checkpoints") or []
    if not isinstance(checkpoints, list):
        checkpoints = []
    revisions += [row.get("commit") for row in checkpoints if isinstance(row, dict)]
    workflow = state.get("workflow")
    # Only an explicit creation choice skips the recognizer. A model decision
    # remains a recorded dispatch; legacy missing provenance grants no pin.
    pin = (
        {"workflow_pin": workflow["kind"]}
        if isinstance(workflow, dict)
        and isinstance(workflow.get("kind"), str)
        and bool(workflow["kind"])
        and workflow.get("source") == "user"
        and "output" in workflow
        and workflow["output"] is None
        else {}
    )
    return {
        "version": 1,
        "workspace": state.get("workspace"),
        "run_dir": run_dir,
        "origin": {
            "run_name": (run_dir or "").rstrip("/").rsplit("/", 1)[-1],
            "runner_revision": None,
            "provenance": (state.get("settings") or {}).get("evidence_provenance", "unknown"),
            "verdict": state.get("status"),
        },
        "task": state.get("task"),
        **pin,
        "settings": {
            key: deepcopy(value) for key, value in (state.get("settings") or {}).items() if key in POLICY_KEYS
        },
        "records": records,
        "revisions": [value for value in revisions if value],
        "captures": [],
        "actions": actions,
        "checks": _check_projection(state, records),
    }
