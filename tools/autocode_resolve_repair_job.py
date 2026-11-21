"""Resolver repair and stall-resolution job (``resolve_repair``).

Escalates rejected stage reports after at most one cheap same-model repair pass
and intercepts mid-stage stalls (retry limits, milestone stalls, planning
budgets) so Resolver can directly emit a pinned report fix, request one missing
check execution, redirect Builder with a changed approach, trigger replanning,
or ask the user a genuine product question without blind loops.
"""

from __future__ import annotations

import contextlib
import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_policy as builder_policy
    from . import autocode_draft_assignment as draft_assignment
    from . import autocode_goals as goals
    from . import autocode_plan_revision_reference as revision_reference
    from . import autocode_planning_calls as planning_calls
    from . import autocode_progressive_state as progressive
    from . import autocode_prompts as prompts
    from . import autocode_report_edits as report_edits
    from . import autocode_report_repair_route as report_repair_route
    from . import autocode_report_source as report_source
    from . import autocode_requirements_observations as requirements_observations
    from . import autocode_roles as roles
    from . import autocode_route_permission as route_permission
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_access as stage_access
    from . import autocode_stop as stop
    from . import autocode_stray_writes as stray_writes
    from . import autocode_support as support
    from . import autocode_tool_handoff as tool_handoff
    from . import autocode_util as util
    from . import autocode_verifier_independence as verifier_independence
except ImportError:
    import autocode_builder_policy as builder_policy
    import autocode_draft_assignment as draft_assignment
    import autocode_goals as goals
    import autocode_plan_revision_reference as revision_reference
    import autocode_planning_calls as planning_calls
    import autocode_progressive_state as progressive
    import autocode_prompts as prompts
    import autocode_report_edits as report_edits
    import autocode_report_repair_route as report_repair_route
    import autocode_report_source as report_source
    import autocode_requirements_observations as requirements_observations
    import autocode_roles as roles
    import autocode_route_permission as route_permission
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_stage_access as stage_access
    import autocode_stop as stop
    import autocode_stray_writes as stray_writes
    import autocode_support as support
    import autocode_tool_handoff as tool_handoff
    import autocode_util as util
    import autocode_verifier_independence as verifier_independence

STAGE = "resolve_repair"
ROUTE = "repair_resolver"
PROMPT = prompts.get("resolve-repair.md")
BEFORE_INVESTIGATOR = frozenset(
    {"PAUSED_REPORT_REPAIR_LIMIT", "PAUSED_BUILDER_RETRY_LIMIT", "PAUSED_MILESTONE_STALLED", "PAUSED_MILESTONE_REPLAN"}
)
AFTER_INVESTIGATOR = frozenset({"PAUSED_PLANNING_BUDGET", "PAUSED_NO_PROGRESS", "PAUSED_REPEATED_FAILURE"})
STALL_STATUSES = BEFORE_INVESTIGATOR | AFTER_INVESTIGATOR
EXECUTION_RERUN_STAGES = (
    "builder",
    "tester",
    "review_completion",
    "review_checkpoint",
    "verify_build",
    "judge_acceptance",
)
VERIFIER_PRODUCERS = {
    "tester": "builder",
    "review_completion": "builder",
    "review_checkpoint": "builder",
    "verify_build": "builder",
    "judge_acceptance": "builder",
    "review_plan": "planner",
    "finalize_plan": "planner",
    "plan_review": "planner",
    "plan_finalize": "planner",
}
ACTIONS = ("repair_report", "rerun_missing_execution", "rework_tests_failed", "replan", "ask_user", "hold")
TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "diagnosis",
        "action",
        "repaired_report_json",
        "guidance",
        "next_objective",
        "affected_paths",
        "user_question",
        "user_options",
        "evidence_refs",
    ],
    "properties": {
        "diagnosis": TEXT,
        "action": {"type": "string", "enum": list(ACTIONS)},
        "repaired_report_json": TEXT,
        "guidance": TEXT,
        "next_objective": TEXT,
        "affected_paths": TEXTS,
        "user_question": TEXT,
        "user_options": TEXTS,
        "evidence_refs": TEXTS,
    },
}
_FORBIDDEN_QUESTION_TOKENS = (
    "--retry-report",
    "--retry-builder",
    "--max-planning-calls",
    "pending_report_repair",
    "paused_report_repair_limit",
)


def _executed_checks(events_path: Any, *, stage: Any = None) -> list[dict[str, Any]]:
    if not isinstance(events_path, str) or not events_path or not Path(events_path).is_file():
        return []
    return report_source.original_executed_checks(events_path, stage=stage)


def select_route(state: RunState, target_stage: str) -> dict[str, Any]:
    """Select an independent Resolver route for ``resolve_repair`` and pin it at ``ROUTE``."""
    settings = state.setdefault("settings", {})
    roles_map = settings.setdefault("roles", {})
    base = roles_map.get("resolver") or roles_map.get("task_planner") or roles_map.get("plan_reviewer") or {}
    chosen = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not chosen.get("reasoning_effort"):
        chosen["reasoning_effort"] = "high"
    producer_role = VERIFIER_PRODUCERS.get(target_stage)
    producer_model = (roles_map.get(producer_role) or {}).get("model") if producer_role else None
    vendor = verifier_independence.same_vendor_engine(settings)
    if (
        producer_model
        and chosen.get("model")
        and verifier_independence.shares(producer_model, chosen["model"], vendor_engine=vendor)
    ):
        inv = route_permission.investigator(settings, producer_model)
        pool = [roles_map.get("plan_reviewer"), inv[1] if inv else None, *roles_map.values()]
        for alt in pool:
            if (
                isinstance(alt, dict)
                and alt.get("model")
                and not verifier_independence.shares(producer_model, alt["model"], vendor_engine=vendor)
            ):
                chosen = copy.deepcopy(alt)
                if not chosen.get("reasoning_effort"):
                    chosen["reasoning_effort"] = "high"
                break
    roles_map[ROUTE] = copy.deepcopy(chosen)
    return chosen


def release_route(state: RunState) -> None:
    """Remove the temporary ``repair_resolver`` route, transport pin, and session."""
    report_repair_route.release(state, ROUTE)


def _history_has_repair(state: RunState, stage: str | None, output: str | None, revision: str | None) -> bool:
    for entry in state.get("resolve_repair_history") or ():
        if (
            isinstance(entry, dict)
            and entry.get("kind") == "report_repair"
            and entry.get("stage") == stage
            and entry.get("original_output") == output
            and entry.get("source_revision") == revision
        ):
            return True
    return False


def persons_review_total(state: RunState, status: str) -> bool:
    """A planning-budget pause at the review total the person chose in this run (--planning-review-call-limit).

    Nothing there needs investigating or Resolver repair: the runner keeps the counter itself and the person set
    the number, so a recovery call only costs a model call before the same pause (#3094), and its retry would
    raise the person's own limit. The pause goes straight back to them with its typed allowance offer."""
    return (
        status == "PAUSED_PLANNING_BUDGET"
        and (state.get("planning") or {}).get("review_call_limit_origin") == "user_explicit"
    )


def operator_retried(state: RunState, stage: str) -> bool:
    """An operator authorized a retry of this stage's failure (--retry-failed-stage): its
    outcome goes back to the operator, who is already handling that exact failure."""
    keys = {
        row.get("failure_key")
        for row in state.get("stages") or []
        if row.get("stage") == stage and row.get("failure_key")
    }
    return any(grant.get("failure_key") in keys for grant in state.get("failure_retry_authorizations") or [])


def eligible_report_repair(state: RunState) -> bool:
    """True when a rejected report already spent 1 local repair (or failed correction) and may escalate."""
    if stop.applied_stop(state) or state.get("version", 2) < 3:
        return False
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict):
        return False
    original = pending.get("original")
    active_request = state.get("resolve_repair_request")
    if (
        not isinstance(original, dict)
        or original.get("stage") in (STAGE, "investigate_stuck", "diagnose")
        or (isinstance(active_request, dict) and not active_request.get("in_force"))
        or state.get("next_stage") != original.get("stage")
        or operator_retried(state, original["stage"])
    ):
        return False
    if pending.get("attempts", 0) < 1 and not pending.get("correction_failed"):
        return False
    return not _history_has_repair(
        state, original.get("stage"), original.get("output"), original.get("source_revision")
    )


def queue_from_pending(state: RunState, run_dir: Path | None = None, *, force: bool = False) -> bool:
    """Escalate ``pending_report_repair`` into ``resolve_repair_request`` and schedule ``resolve_repair``."""
    del run_dir
    if stop.applied_stop(state) or state.get("version", 2) < 3:
        return False
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict):
        return False
    original = pending.get("original")
    active_request = state.get("resolve_repair_request")
    if (
        not isinstance(original, dict)
        or original.get("stage") in (STAGE, "investigate_stuck", "diagnose")
        or (isinstance(active_request, dict) and not active_request.get("in_force"))
        or state.get("next_stage") != original.get("stage")
        or operator_retried(state, original["stage"])
    ):
        return False
    if not force and not eligible_report_repair(state):
        return False
    stage = original["stage"]
    revision = original.get("source_revision")
    output = original.get("output")
    if _history_has_repair(state, stage, output, revision):
        return False
    popped = state.pop("pending_report_repair")
    chosen = select_route(state, stage)
    state["resolve_repair_request"] = {
        "kind": "report_repair",
        "stage": stage,
        "original_output": output,
        "source_revision": revision,
        "contract_hash": popped.get("contract_hash") or (state.get("goal_contract") or {}).get("hash"),
        "pending_report_repair": copy.deepcopy(popped),
        "previous_status": state.get("status") or "PAUSED_REPORT_REPAIR_LIMIT",
        "previous_stop_reason": state.get("stop_reason")
        or popped.get("error")
        or "Bounded report-only repair attempts exhausted",
        "previous_next_stage": stage,
        "requested_at": util.now(),
        "model": chosen.get("model"),
        "in_force": False,
    }
    run_status.running(state, "REPORT_REPAIR", next_stage=STAGE)
    return True


def queue_missing_execution(state: RunState, record: dict[str, Any], error: Exception) -> bool:
    """Queue ``resolve_repair`` directly when a stage exited 0 without running required checks."""
    if stop.applied_stop(state) or state.get("version", 2) < 3:
        return False
    stage = record.get("stage")
    revision = record.get("source_revision")
    contract_hash = (state.get("goal_contract") or {}).get("hash")
    if stage not in EXECUTION_RERUN_STAGES or not revision or operator_retried(state, stage):
        return False
    for entry in state.get("resolve_repair_history") or ():
        if (
            isinstance(entry, dict)
            and entry.get("kind") in ("missing_execution", "rerun_missing_execution")
            and entry.get("stage") == stage
            and entry.get("source_revision") == revision
            and entry.get("contract_hash") == contract_hash
        ):
            return False
    popped = state.pop("pending_report_repair", None)
    chosen = select_route(state, stage)
    state["resolve_repair_request"] = {
        "kind": "missing_execution",
        "stage": stage,
        "original_output": record.get("output"),
        "source_revision": revision,
        "contract_hash": contract_hash,
        "pending_report_repair": copy.deepcopy(popped) if isinstance(popped, dict) else None,
        "rejected_record": copy.deepcopy(record),
        "rejection_error": str(error),
        "previous_status": "PAUSED_INVALID_OUTPUT",
        "previous_stop_reason": str(error),
        "previous_next_stage": stage,
        "requested_at": util.now(),
        "model": chosen.get("model"),
        "in_force": False,
    }
    run_status.running(state, "EXECUTING", next_stage=STAGE)
    return True


def _repeated_failure_is_completed_rejection(state: RunState, target_stage: str) -> bool:
    """True only when ``PAUSED_REPEATED_FAILURE`` reflects a completed rejected stage output.

    Timeouts/abandoned attempts (``stage_stalls``, ``failures.repeated`` on timeouts), permission-denial
    operational holds (``denied_operation``), ``resolve`` self-failures, and partial report-repair stops
    still owned by ``autocode_report_retry`` (``pending_report_repair`` active before ``PAUSED_REPORT_REPAIR_LIMIT``)
    are not ``kind='stall'`` targets for ``resolve_repair``.
    """
    if target_stage == "resolve" or state.get("pending_report_repair"):
        return False
    if (state.get("recovery_context") or {}).get("denied_operation"):
        return False
    last = next(
        (
            row
            for row in reversed(state.get("stages") or ())
            if isinstance(row, dict) and (row.get("original_stage") or row.get("stage")) == target_stage
        ),
        None,
    )
    return bool(
        isinstance(last, dict)
        and last.get("rejected")
        and not last.get("timed_out")
        and not last.get("interrupted")
        and not last.get("abandoned")
        and last.get("exit_code", 0) == 0
    )


def intercept_stall(state: RunState, status: str, reason: str, *, before_investigator: bool) -> bool:
    """Intercept a qualifying pause/stall into ``resolve_repair`` once per distinct problem."""
    if stop.applied_stop(state) or state.get("version", 2) < 3 or state.get("active_stage"):
        return False
    if state.get("next_stage") in (STAGE, "investigate_stuck", "diagnose"):
        return False
    allowed = BEFORE_INVESTIGATOR if before_investigator else AFTER_INVESTIGATOR
    if status not in allowed:
        return False
    if progressive.retained_review_budget_pause(state, status) or persons_review_total(state, status):
        return False
    target_stage = state.get("next_stage")
    if not isinstance(target_stage, str) or not target_stage or operator_retried(state, target_stage):
        return False
    if status == "PAUSED_REPORT_REPAIR_LIMIT" and state.get("pending_report_repair"):
        return queue_from_pending(state, force=True)
    if status == "PAUSED_REPEATED_FAILURE" and not _repeated_failure_is_completed_rejection(state, target_stage):
        return False
    if planning_calls.planning_stage(target_stage):
        cap = planning_calls.cap(state)
        if cap is not None and planning_calls.used(state) >= cap:
            return False
    milestones = ((state.get("goal_contract") or {}).get("body") or {}).get("milestones") or ()
    max_stalls = max(6, 2 * len(milestones))
    prior_stalls = [
        row
        for row in (state.get("resolve_repair_history") or ())
        if isinstance(row, dict) and row.get("kind") == "stall"
    ]
    if len(prior_stalls) >= max_stalls:
        return False
    revision = str(
        state.get("source_revision")
        or next(
            (
                row.get("source_revision")
                for row in reversed(state.get("stages") or ())
                if isinstance(row, dict) and row.get("source_revision")
            ),
            "",
        )
    )
    contract_hash = (state.get("goal_contract") or {}).get("hash")
    for entry in prior_stalls:
        if (
            entry.get("stage") == target_stage
            and entry.get("pause_status") == status
            and entry.get("source_revision") == revision
            and entry.get("contract_hash") == contract_hash
        ):
            return False
    popped = state.pop("pending_report_repair", None)
    chosen = select_route(state, target_stage)
    state["resolve_repair_request"] = {
        "kind": "stall",
        "stage": target_stage,
        "pause_status": status,
        "source_revision": revision,
        "contract_hash": contract_hash,
        "pending_report_repair": copy.deepcopy(popped) if isinstance(popped, dict) else None,
        "previous_status": status,
        "previous_stop_reason": reason or state.get("stop_reason") or status,
        "previous_next_stage": target_stage,
        "requested_at": util.now(),
        "model": chosen.get("model"),
        "in_force": False,
    }
    phase = "PLANNING" if planning_calls.planning_stage(target_stage) else "EXECUTING"
    run_status.running(state, phase, next_stage=STAGE)
    return True


def launched(state: RunState, request: dict | None = None) -> bool:
    """Whether ``resolve_repair`` actually ran or left a recorded stage attempt for ``request``."""
    request = request if request is not None else state.get("resolve_repair_request")
    if not isinstance(request, dict) or not request.get("requested_at"):
        return False
    if state.get("active_stage") and (state["active_stage"] or {}).get("stage") == STAGE:
        return True
    requested_at = str(request["requested_at"])
    return any(
        isinstance(row, dict)
        and row.get("stage") == STAGE
        and not row.get("runner_owned")
        and str(row.get("started_at") or "") >= requested_at
        for row in reversed(state.get("stages") or ())
    )


def timed_out(state: RunState) -> bool:
    """True when ``state`` holds an unrecovered timeout or job failure on ``resolve_repair``."""
    if stop.applied_stop(state):
        return False
    current = state.get("resolve_repair_request")
    if not isinstance(current, dict) or not current or current.get("in_force"):
        return False
    if state.get("next_stage") != STAGE:
        return False
    status = state.get("status")
    jf = state.get("job_failure") or {}
    if status == "PAUSED_JOB_FAILURE" and isinstance(jf, dict) and jf.get("stage") == STAGE:
        return True
    ctx = state.get("recovery_context") or {}
    return status == "PAUSED_STAGE_TIMEOUT" and isinstance(ctx, dict) and ctx.get("stage") == STAGE


def handoff(state: RunState, workspace: Path | None = None, *, engine: str | None = None) -> dict[str, Any]:
    """Assemble the read-only ``CURRENT HANDOFF DATA`` packet for ``resolve_repair``."""
    del workspace
    raw_settings = state.get("settings")
    settings = raw_settings if isinstance(raw_settings, dict) else {}
    raw_roles = settings.get("roles")
    roles_cfg = raw_roles if isinstance(raw_roles, dict) else {}
    route_cfg = roles_cfg.get(ROUTE)
    resolved_engine = (
        engine
        or (route_cfg.get("engine") if isinstance(route_cfg, dict) else None)
        or settings.get("execution_engine")
        or settings.get("engine")
        or "codex"
    )
    request = state.get("resolve_repair_request") or {}
    pending = request.get("pending_report_repair")
    original = pending.get("original") if isinstance(pending, dict) else None
    source_record = original or request.get("rejected_record")
    rejected_report: Any = None
    if isinstance(source_record, dict) and source_record.get("output"):
        latest = pending.get("latest_rejected") if isinstance(pending, dict) else None
        target_rec = latest if isinstance(latest, dict) and latest.get("output") else source_record
        with contextlib.suppress(Exception):
            rejected_report = report_source.repair_report_source(target_rec)
    report_schema: Any = None
    schema_path = (original or request.get("rejected_record") or {}).get("schema")
    if isinstance(schema_path, str) and schema_path and Path(schema_path).is_file():
        with contextlib.suppress(Exception):
            report_schema = report_edits.validation_schema(util.read(Path(schema_path)))
    checks = _executed_checks(
        (original or request.get("rejected_record") or {}).get("events"),
        stage=(original or request.get("rejected_record") or {}).get("stage") or request.get("stage"),
    )
    contract = state.get("goal_contract") or {}
    recent = [
        {
            k: row.get(k)
            for k in (
                "stage",
                "original_stage",
                "role",
                "iteration",
                "exit_code",
                "rejected",
                "rejection_reason",
                "output",
            )
        }
        for row in (state.get("stages") or ())[-5:]
        if isinstance(row, dict)
    ]
    return {
        "execution_engine": resolved_engine,
        "stage": STAGE,
        "kind": request.get("kind", "stall"),
        "target": {
            "stage": request.get("stage"),
            "status": request.get("previous_status"),
            "reason": request.get("previous_stop_reason"),
        },
        "rejected_report": rejected_report,
        "rejection_error": (pending or {}).get("error") or request.get("rejection_error") or "",
        "original_error": (pending or {}).get("original_error") or request.get("previous_stop_reason") or "",
        "report_schema": report_schema,
        "original_executed_checks": checks,
        "goal_contract": {
            "hash": contract.get("hash"),
            "revision": contract.get("revision"),
            "approval_status": contract.get("approval_status"),
            "body": contract.get("body"),
        },
        "current_task": state.get("current_task"),
        "recent_stages": recent,
    }


def _rejected_report_object(pending: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    latest = pending.get("latest_rejected")
    for candidate in (latest, original):
        if not isinstance(candidate, dict):
            continue
        path = candidate.get("output")
        if isinstance(path, str) and path and Path(path).is_file():
            pin = (pending.get("pins") or {}).get(path)
            if pin and util.file_hash(path) != pin:
                raise ValueError("Pinned rejected report changed before applying edits")
            data = json.loads(Path(path).read_text())
            if isinstance(data, dict):
                return data
    raise ValueError("No JSON object rejected report is available for edits")


def _set_path(root: Any, parts: list[str | int], val: Any) -> None:
    cur = root
    for part in parts[:-1]:
        if isinstance(cur, list) and isinstance(part, int):
            cur = cur[part]
        elif isinstance(cur, dict):
            if part not in cur or not isinstance(cur[part], (dict, list)):
                cur[part] = {}
            cur = cur[part]
        else:
            raise ValueError(f"Cannot traverse edit path at {part!r}")
    leaf = parts[-1]
    if isinstance(cur, dict):
        cur[leaf] = val
        return
    if not isinstance(cur, list) or not isinstance(leaf, int):
        raise ValueError(f"Cannot set edit leaf {leaf!r}")
    cur[leaf] = val


def _delete_path(root: Any, parts: list[str | int]) -> None:
    cur = root
    for part in parts[:-1]:
        cur = cur[part]
    leaf = parts[-1]
    if isinstance(cur, list) and isinstance(leaf, int):
        cur.pop(leaf)
    elif isinstance(cur, dict):
        cur.pop(leaf, None)
    else:
        raise ValueError(f"Cannot delete edit leaf {leaf!r}")


def _parse_path_parts(raw: Any) -> list[str | int]:
    if isinstance(raw, list) and raw:
        return [int(p) if isinstance(p, int) or (isinstance(p, str) and p.isdigit()) else str(p) for p in raw]
    if isinstance(raw, str) and raw.strip():
        return [int(p) if p.isdigit() else p for p in raw.strip().lstrip("/").split(".") if p]
    raise ValueError("Edit path must be a non-empty string or list")


def _parse_repaired_report(raw: str, pending: dict[str, Any], original: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("action='repair_report' requires non-empty repaired_report_json")
    try:
        parsed = json.loads(raw)
    except ValueError as err:
        raise ValueError(f"repaired_report_json is not valid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise ValueError("repaired_report_json must decode to a JSON object")
    if isinstance(parsed.get("repair_report"), dict) and set(parsed) == {"repair_report"}:
        patch = parsed["repair_report"]
        base = _rejected_report_object(pending, original)
        expected_sha = patch.get("sha256")
        if expected_sha:
            latest = pending.get("latest_rejected") or original
            pin = (pending.get("pins") or {}).get(latest.get("output"))
            if pin and expected_sha != pin:
                raise ValueError("repair_report patch sha256 does not match pinned rejected report")
        candidate = report_edits.apply(base, patch.get("edits") or [], schema=schema)
    elif set(parsed) == {"edits"} and isinstance(parsed.get("edits"), list):
        base = copy.deepcopy(_rejected_report_object(pending, original))
        edits = parsed["edits"]
        if not edits:
            raise ValueError("edits must not be empty")
        if all(isinstance(item, dict) and "op" in item for item in edits):
            candidate = report_edits.apply(base, edits, schema=schema)
        else:
            for item in edits:
                if not isinstance(item, dict):
                    raise ValueError("Each edit entry must be an object")
                parts = _parse_path_parts(item.get("path"))
                action = item.get("action")
                if action == "set":
                    _set_path(base, parts, copy.deepcopy(item.get("value")))
                elif action == "delete":
                    _delete_path(base, parts)
                else:
                    raise ValueError(f"Unsupported edit action {action!r}")
            candidate = base
    else:
        candidate = parsed
    if isinstance(schema, dict) and schema:
        util.validate_schema(candidate, schema)
    return candidate


def _record_history(
    state: RunState,
    request: dict[str, Any],
    record: dict[str, Any],
    action: str,
    *,
    counted_in_stages: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    entry: dict[str, Any] = {
        "kind": request.get("kind", "stall"),
        "action": action,
        "stage": request.get("stage"),
        "pause_status": request.get("pause_status") or request.get("previous_status"),
        "original_output": request.get("original_output"),
        "source_revision": request.get("source_revision"),
        "contract_hash": request.get("contract_hash"),
        "requested_at": request.get("requested_at"),
        "resolved_at": util.now(),
        "model": record.get("model") or request.get("model"),
        "route": ROUTE,
        "output": record.get("output"),
        "counted_in_stages": counted_in_stages,
    }
    if extra:
        entry.update(extra)
    state.setdefault("resolve_repair_history", []).append(entry)


def _apply_repair_report(
    runtime: Any,
    state: RunState,
    request: dict[str, Any],
    value: dict[str, Any],
    record: dict[str, Any],
    workspace: Path,
    run_dir: Path,
) -> None:
    if runtime is None or not hasattr(runtime, "apply_result"):
        raise RuntimeError("resolve_repair action='repair_report' requires runtime with apply_result")
    if request.get("kind") != "report_repair" or not isinstance(request.get("pending_report_repair"), dict):
        raise ValueError("action='repair_report' is only valid for kind='report_repair'")
    pending = copy.deepcopy(request["pending_report_repair"])
    original = copy.deepcopy(pending["original"])
    schema_path = original.get("schema")
    schema = (
        report_edits.validation_schema(util.read(Path(schema_path)))
        if isinstance(schema_path, str) and schema_path and Path(schema_path).is_file()
        else {}
    )
    repaired_value = _parse_repaired_report(value.get("repaired_report_json", ""), pending, original, schema)
    if original.get("stage") == "revise_plan":
        # Ordinary stage loading decodes schema-offered null no-ops before
        # expanding the saved revision. Apply the same pinned rule to Resolver
        # repairs; the Resolver's own report retains its original bytes.
        repaired_value = revision_reference.normalize_nullable_reference(repaired_value, schema)
    runtime_support = getattr(runtime, "support", support)
    file_hash_fn = getattr(runtime_support, "file_hash", util.file_hash)
    snap_fn = getattr(runtime_support, "snapshot", support.snapshot)
    write_json_fn = getattr(runtime, "write_json", util.atomic_json)
    now_fn = getattr(runtime, "now", util.now)

    current_snap = source_scope.snapshot(workspace, state, base_snapshot=snap_fn)
    if (
        current_snap["revision"] != original["source_revision"]
        or (state.get("goal_contract") or {}).get("hash") != pending.get("contract_hash")
        or any(not Path(p).is_file() or file_hash_fn(p) != h for p, h in (pending.get("pins") or {}).items())
    ):
        raise runtime_support.Paused(
            "PAUSED_STALE_VALIDATION",
            "Original report evidence or goal changed during resolve_repair",
        )

    repaired_path = Path(original["output"]).with_name(Path(original["output"]).stem + ".resolve-repaired.json")
    write_json_fn(repaired_path, repaired_value)
    try:
        restore_fn = getattr(runtime, "restore_builder_history", None)
        if restore_fn and restore_fn(original, repaired_value):
            write_json_fn(repaired_path, repaired_value)
        output_hash = file_hash_fn(repaired_path)
        assert_fn = getattr(runtime, "assert_repair_preserves_builder_history", None)
        if assert_fn:
            assert_fn(original, repaired_value)
        retained_fn = getattr(runtime, "retained_dispositions", None)
        if retained_fn:
            orig_for_repair = getattr(runtime, "original_report_for_repair", None)
            stage_done = getattr(runtime, "stage_completed", None)
            orig_report = (
                orig_for_repair(original)["report"]
                if orig_for_repair and stage_done and stage_done(state, original)
                else None
            )
            original.update(retained_fn(original, orig_report, repaired_value))

        repair_record: dict[str, Any] = {
            "role": original.get("role"),
            "stage": original["stage"] + "_report_repair",
            "original_stage": original["stage"],
            "report_only": True,
            "exit_code": 0,
            "changed_files": [],
            "output": str(repaired_path),
            "events": record.get("events"),
            "schema": original.get("schema"),
            "source_revision": original.get("source_revision"),
            "iteration": original.get("iteration"),
            "contract_hash": original.get("contract_hash"),
            "criteria_revision": original.get("criteria_revision"),
            "task_id": original.get("task_id"),
            "before_ref": original.get("before_ref"),
            "after_ref": original.get("after_ref"),
            "engine": record.get("engine"),
            "model": record.get("model"),
            "route_role": ROUTE,
            "resolved_by": STAGE,
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "duration_seconds": record.get("duration_seconds", 0),
        }
        for billing_key in ("metrics", "cost_usd", "response_text"):
            if billing_key in record:
                repair_record[billing_key] = copy.deepcopy(record[billing_key])

        original_copy = copy.deepcopy(original)
        original_copy.update(
            output=str(repaired_path),
            repaired_by=record.get("events"),
            rejected=False,
            report_repaired=True,
        )
        previous_planning = copy.deepcopy(state.get("planning") or {})
        state["next_stage"] = original["stage"]
        runtime.apply_result(state, original["stage"], repaired_value, original_copy, workspace, run_dir)
        draft_assignment.retain_review_allowance(state, previous_planning, original_copy)
    except Exception:
        repaired_path.unlink(missing_ok=True)
        raise

    repair_record["applied_original_events"] = original["events"]
    state["stages"][-1] = repair_record
    state["history"][-1] = repair_record
    state.setdefault("report_repair_history", []).append(
        {
            "original_output": pending["original"]["output"],
            "repair": repair_record,
            "output_hash": output_hash,
            "rejections": list(pending.get("rejections") or filter(None, [pending.get("original_error")])),
            "attempts": pending.get("attempts", 0),
            "result": "accepted",
            "at": now_fn(),
            "via": STAGE,
        }
    )
    requirements_observations.seal_repaired(state, repair_record)
    state.pop("pending_report_repair", None)
    state.pop("resolve_repair_request", None)
    # Charge and count cost/planning calls exclusively on repair_record; keep trailing audit record $0 runner-owned.
    record.update({"runner_owned": True, "engine": "runner"})
    _record_history(state, request, repair_record, "repair_report", counted_in_stages=True)
    if state.get("status") == "RUNNING":
        next_stage = state.get("next_stage")
        phase = "PLANNING" if next_stage in roles.ALL_PLANNING_STAGES else "EXECUTING"
        run_status.running(state, phase)


def _apply_rerun_missing_execution(
    state: RunState,
    request: dict[str, Any],
    value: dict[str, Any],
    record: dict[str, Any],
) -> None:
    target_stage = request.get("stage")
    guidance = (value.get("guidance") or "").strip()
    if target_stage not in EXECUTION_RERUN_STAGES:
        raise ValueError(f"rerun_missing_execution is not valid for stage {target_stage!r}")
    if not guidance:
        raise ValueError("action='rerun_missing_execution' requires non-empty guidance")
    for entry in state.get("resolve_repair_history") or ():
        if (
            isinstance(entry, dict)
            and entry.get("action") == "rerun_missing_execution"
            and entry.get("stage") == target_stage
            and entry.get("source_revision") == request.get("source_revision")
            and entry.get("contract_hash") == request.get("contract_hash")
        ):
            raise ValueError("rerun_missing_execution is allowed at most once per stage and source revision")
    state.pop("pending_report_repair", None)
    _record_history(state, request, record, "rerun_missing_execution", counted_in_stages=False)
    request["in_force"] = True
    request["guidance"] = guidance
    request["evidence_refs"] = list(value.get("evidence_refs") or [])
    request["diagnosis"] = value.get("diagnosis") or ""
    state["resolve_repair_request"] = request
    state["iteration"] = int(state.get("iteration") or 0) + 1
    run_status.running(state, "EXECUTING", next_stage=target_stage, next_action=guidance)


def _apply_rework_tests_failed(
    state: RunState,
    request: dict[str, Any],
    value: dict[str, Any],
    record: dict[str, Any],
) -> None:
    guidance = (value.get("guidance") or "").strip()
    next_objective = (value.get("next_objective") or "").strip()
    affected_paths = [p.strip() for p in (value.get("affected_paths") or []) if isinstance(p, str) and p.strip()]
    if not guidance or (not next_objective and not affected_paths):
        raise ValueError("action='rework_tests_failed' requires guidance plus next_objective or affected_paths")
    task = state.get("current_task") if isinstance(state.get("current_task"), dict) else None
    pause_status = request.get("pause_status") or request.get("previous_status")
    if pause_status in ("PAUSED_BUILDER_RETRY_LIMIT", "PAUSED_MILESTONE_STALLED") and task is not None:
        old_obj = str(task.get("objective") or "").strip()
        old_paths = sorted(str(p) for p in (task.get("affected_paths") or ()))
        new_obj = next_objective or old_obj
        new_paths = sorted(affected_paths) if affected_paths else old_paths
        if (new_obj, new_paths) == (old_obj, old_paths):
            raise ValueError(
                "rework_tests_failed after Builder retry or milestone stall must change next_objective or affected_paths"
            )
    if task is not None:
        if next_objective:
            task["objective"] = next_objective
        if affected_paths:
            task["affected_paths"] = affected_paths
    if builder_policy.enabled(state) and task:
        ident = builder_policy.key(state)
        lane = (state.get("builder_retries") or {}).get(ident)
        if isinstance(lane, dict):
            ordinary = ((state.get("settings") or {}).get("builder_retry") or {}).get("ordinary_retries", 1)
            ordinary_int = ordinary if type(ordinary) is int and 0 <= ordinary <= 3 else 1
            lane["action"] = "retry"
            lane["position"] = min(int(lane.get("position") or 0), ordinary_int)
            failures = lane.get("failures") or []
            route = ((state.get("settings") or {}).get("roles") or {}).get("builder") or {}
            state.setdefault("builder_retry_decisions", []).append(
                {
                    "at": util.now(),
                    "owner": STAGE,
                    "action": "retry",
                    "milestone_key": ident,
                    "failure": failures[-1] if failures else request.get("previous_stop_reason"),
                    "attempt": len(failures),
                    "reason": f"Resolver redirected Builder with changed approach: {guidance}",
                    "selected_model": route.get("model"),
                    "selected_effort": route.get("reasoning_effort"),
                }
            )
        state.setdefault("sessions", {}).pop("builder", None)
    for row in (state.get("milestone_progress") or {}).values():
        if isinstance(row, dict) and not row.get("accepted"):
            row["reviews_without_progress"] = 0
            row["needs_replan"] = False
    state.pop("pending_report_repair", None)
    _record_history(
        state,
        request,
        record,
        "rework_tests_failed",
        counted_in_stages=False,
        extra={"next_objective": next_objective, "affected_paths": affected_paths},
    )
    request["in_force"] = True
    request["guidance"] = guidance
    request["next_objective"] = next_objective
    request["affected_paths"] = affected_paths
    request["diagnosis"] = value.get("diagnosis") or ""
    request["target_stage_for_guidance"] = "builder"
    state["resolve_repair_request"] = request
    state["iteration"] = int(state.get("iteration") or 0) + 1
    run_status.running(state, "EXECUTING", next_stage="builder", next_action=guidance)


def _apply_replan(
    state: RunState,
    request: dict[str, Any],
    value: dict[str, Any],
    record: dict[str, Any],
) -> None:
    guidance = (value.get("guidance") or "").strip()
    if not guidance:
        raise ValueError("action='replan' requires non-empty guidance")
    state.pop("pending_report_repair", None)
    approved = goals.approved(state)
    v2 = roles.planning_flow(state) == "v2"
    if not approved:
        planning = state.get("planning")
        if (
            isinstance(planning, dict)
            and planning.get("review_call_limit_origin") != "user_explicit"
            and isinstance(planning.get("review_call_limit"), int)
            and planning["review_call_limit"] > 0
        ):
            planning["review_call_limit"] += 1
        contract = state.get("goal_contract")
        next_stage = ("plan_revise" if v2 else "revise_plan") if contract else ("plan" if v2 else "draft_plan")
    else:
        contract = state.get("goal_contract")
        if isinstance(contract, dict):
            state.setdefault("contract_history", []).append(copy.deepcopy(contract))
            contract["approval_status"] = "draft"
            contract["approval_event"] = None
        goals.invalidate(state, f"Resolver replan required: {guidance}")
        if state.get("current_task"):
            state.setdefault("task_archive", []).append(state.pop("current_task"))
        next_stage = "plan" if v2 else "draft_plan"
    _record_history(state, request, record, "replan", counted_in_stages=False, extra={"target_next_stage": next_stage})
    request["in_force"] = True
    request["guidance"] = guidance
    request["diagnosis"] = value.get("diagnosis") or ""
    request["target_stage_for_guidance"] = next_stage
    state["resolve_repair_request"] = request
    run_status.running(state, "PLANNING", next_stage=next_stage, next_action=guidance, pending_questions=[])


def _apply_ask_user(
    state: RunState,
    request: dict[str, Any],
    value: dict[str, Any],
    record: dict[str, Any],
    ask_user: Any,
) -> None:
    question = (value.get("user_question") or "").strip()
    if not question:
        raise ValueError("action='ask_user' requires non-empty user_question")
    lowered = question.lower()
    if any(token in lowered for token in _FORBIDDEN_QUESTION_TOKENS):
        raise ValueError("action='ask_user' must never ask the user about AutoCode internal machinery")
    if ask_user is None:
        raise RuntimeError("resolve_repair action='ask_user' requires ask_user callback")
    options = [str(opt).strip() for opt in (value.get("user_options") or []) if str(opt).strip()]
    previous_next = request.get("previous_next_stage") or request.get("stage")
    state.pop("pending_report_repair", None)
    state.pop("resolve_repair_request", None)
    _record_history(state, request, record, "ask_user", counted_in_stages=False)
    diagnosis = (value.get("diagnosis") or request.get("previous_stop_reason") or "Resolver needs user input").strip()
    origin = {
        "stage": STAGE,
        **{key: record[key] for key in ("output", "source_revision", "task_id") if record.get(key)},
    }
    if "source_revision" not in origin and request.get("source_revision"):
        origin["source_revision"] = request["source_revision"]
    hashes = {}
    if isinstance(record.get("output"), str) and Path(record["output"]).is_file():
        hashes[record["output"]] = util.file_hash(Path(record["output"]))
    ask_user(
        state,
        {
            "kind": "blocker",
            "decision_needed": question,
            "impact": diagnosis,
            "options": options,
            "discovered": request.get("previous_stop_reason") or "",
            "proposed_delta": "",
        },
        origin=origin,
        evidence={
            "diagnosis": diagnosis,
            "output": record.get("output"),
            "evidence_refs": list(value.get("evidence_refs") or []),
            "hashes": hashes,
        },
        next_stage=previous_next,
    )


def apply(
    runtime: Any,
    state: RunState,
    value: dict[str, Any],
    record: dict[str, Any],
    workspace: Path,
    run_dir: Path,
    *,
    ask_user: Any = None,
) -> None:
    """Validate and apply ``resolve_repair``'s output without blind retries or loops."""
    if stop.applied_stop(state):
        raise support.Paused(stop.STOP_STATUS, stop.STOP_REASON)
    if stray := stage_access.stray(STAGE, record.get("changed_files") or []):
        raise stray_writes.StrayWrites(
            f"resolve_repair is read-only and may not modify files: {', '.join(stray)}",
            stray,
        )
    request = state.get("resolve_repair_request")
    if not isinstance(request, dict):
        raise ValueError("resolve_repair finished without an active resolve_repair_request")
    action = value.get("action")
    if action not in ACTIONS:
        raise ValueError(f"Unsupported resolve_repair action: {action!r}")
    prior_settings = copy.deepcopy(state.get("settings"))
    prior_sessions = copy.deepcopy(state.get("sessions"))
    release_route(state)
    request = copy.deepcopy(request)
    try:
        if action == "repair_report":
            _apply_repair_report(runtime, state, request, value, record, workspace, run_dir)
        elif action == "rerun_missing_execution":
            _apply_rerun_missing_execution(state, request, value, record)
        elif action == "rework_tests_failed":
            _apply_rework_tests_failed(state, request, value, record)
        elif action == "replan":
            _apply_replan(state, request, value, record)
        elif action == "ask_user":
            _apply_ask_user(state, request, value, record, ask_user)
        else:
            _record_history(state, request, record, "hold", counted_in_stages=False)
            abandon(state, value.get("diagnosis") or "", outcome="held")
    except Exception:
        if prior_settings is None:
            state.pop("settings", None)
        else:
            state["settings"] = prior_settings
        if prior_sessions is None:
            state.pop("sessions", None)
        else:
            state["sessions"] = prior_sessions
        raise
    if (state.get("recovery_context") or {}).get("stage") == STAGE:
        state.pop("recovery_context", None)


def abandon(state: RunState, reason: str = "", *, outcome: str = "could not finish") -> tuple[str, str]:
    """Restore the underlying pause when ``resolve_repair`` fails, times out, or returns ``hold``.

    The restored pause keeps its own reason, which says what to fix; ``reason`` (the Resolver's
    diagnosis or why it could not finish) is added after it, as the Investigator's note is.
    """
    release_route(state)
    if (state.get("recovery_context") or {}).get("stage") == STAGE:
        state.pop("recovery_context", None)
    if ((state.get("pending_report_repair") or {}).get("original") or {}).get("stage") == STAGE:
        state.pop("pending_report_repair", None)
    request = state.pop("resolve_repair_request", None) or {}
    if isinstance(request.get("pending_report_repair"), dict):
        state["pending_report_repair"] = copy.deepcopy(request["pending_report_repair"])
    status = str(request.get("previous_status") or "PAUSED_INVALID_OUTPUT")
    previous = str(request.get("previous_stop_reason") or "")
    note = str(reason or "").strip()
    if previous and note and note not in previous:
        stop_reason = f"{previous}\nResolver ({outcome}): {note}"
    else:
        stop_reason = previous or note or status
    restored_next_stage = request.get("previous_next_stage") or request.get("stage") or state.get("next_stage")
    if request:
        entry_recorded = any(
            isinstance(row, dict)
            and row.get("requested_at") == request.get("requested_at")
            and row.get("stage") == request.get("stage")
            for row in (state.get("resolve_repair_history") or ())
        )
        if not entry_recorded:
            state.setdefault("resolve_repair_history", []).append(
                {
                    "kind": request.get("kind", "stall"),
                    "action": "abandoned",
                    "stage": request.get("stage"),
                    "pause_status": request.get("pause_status") or status,
                    "original_output": request.get("original_output"),
                    "source_revision": request.get("source_revision"),
                    "contract_hash": request.get("contract_hash"),
                    "requested_at": request.get("requested_at"),
                    "resolved_at": util.now(),
                    "model": request.get("model") if launched(state, request) else None,
                    "route": ROUTE,
                    "counted_in_stages": False,
                }
            )
    run_status.pause(state, status, stop_reason, next_stage=restored_next_stage, paused_at=util.now())
    return status, stop_reason


def with_guidance(state: RunState, stage: str, request: Any) -> Any:
    """Inject active ``resolve_repair`` guidance into the target stage's prompt once."""
    current = state.get("resolve_repair_request")
    if not isinstance(current, dict) or not current.get("in_force"):
        return request
    target = current.get("target_stage_for_guidance") or current.get("stage")
    if target != stage:
        return request
    guidance = (current.get("guidance") or "").strip()
    if not guidance:
        return request
    banner = (
        "\n\nRESOLVER GUIDANCE (resolve_repair)\n"
        f"Diagnosis: {current.get('diagnosis') or 'See guidance below.'}\n"
        f"Required direction: {guidance}\n"
    )
    if current.get("next_objective"):
        banner += f"Updated objective: {current['next_objective']}\n"
    if current.get("affected_paths"):
        banner += f"Target paths: {', '.join(current['affected_paths'])}\n"
    return replace(request, prompt=tool_handoff.before_handoff(request.prompt, banner))


def settle(state: RunState, stage: str) -> None:
    """Consume active ``resolve_repair`` guidance once its redirected target stage has finished."""
    if stage == STAGE:
        return
    current = state.get("resolve_repair_request")
    if not isinstance(current, dict) or not current.get("in_force"):
        return
    target = current.get("target_stage_for_guidance") or current.get("stage")
    if target == stage:
        state.pop("resolve_repair_request", None)


def owns(state: RunState) -> bool:
    """``resolve_repair`` never marks a run complete on its own."""
    return False


def render(state: RunState) -> str:
    """No extra CLI status block is emitted by default."""
    return ""
