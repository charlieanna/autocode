"""Cross-run outcome split, rejection classes, latency and prompt telemetry for usage.jsonl (#3346).

Projects additive, secret-free facts from saved run state into ``PROJECT/.autocode/usage.jsonl``
so ``autocode usage report`` can aggregate stage outcomes, rejections, first-interaction
latency, prompt size / first-request cache counts and completed-task cost without reading any
run's private ``state.json``.

Pure projection over supplied state and records: never reads ``state.json``, never modifies run
state, never stores prompts/outputs/rejection prose, and imports no upper-layer cost/status
modules (avoiding any cycle back through ``autocode_cost`` or ``autocode_usage``).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_evidence_provenance as evidence_provenance
    from . import autocode_interaction_timing as interaction_timing
    from . import autocode_names as names
    from . import autocode_rejection_classes as rejection_classes
    from . import autocode_roles as roles
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_evidence_provenance as evidence_provenance
    import autocode_interaction_timing as interaction_timing
    import autocode_names as names
    import autocode_rejection_classes as rejection_classes
    import autocode_roles as roles

LEDGER_SCHEMA = 2
OUTCOME_BUCKETS: tuple[str, ...] = ("accepted", "rejected", "report_repair")
_EXCLUDED_STAGE_NAMES = frozenset({"orchestrator", "model_preflight"})
_RESOLVER_MARKER_NAMES = frozenset({"resolver", "resolve"})
_QUESTION_STAGES = frozenset(
    {"requirements_gather", "requirements", "requirements_planner", "intake", "collect_design"}
)
_PLAN_STAGES = frozenset({"draft_plan", "review_plan", "revise_plan", "finalize_plan", "plan", "plan_review"})
_COMPLETED_STATUSES = frozenset({"TASK_COMPLETE", "COMPLETE"})
_ABANDONED_STATUSES = frozenset({"ABANDONED", "CANCELLED"})
_PAUSED_EXACT_STATUSES = frozenset(
    {"WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL", "RESOLVER_PENDING", "STOPPED", "FAILED"}
)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nonneg_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = float(value)
        return num if math.isfinite(num) and num >= 0 else None
    return None


def _nonneg_int(value: object) -> int | None:
    return int(value) if type(value) is int and value >= 0 else None


def _parse_iso(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_runner_owned(record: dict[str, Any]) -> bool:
    return record.get("runner_owned") is True and record.get("engine") == "runner"


def _mapped_base_stage(raw_stage: str) -> str:
    return names.stage(raw_stage.removesuffix("_report_repair"))


def _is_excluded_stage(record: dict[str, Any], raw_stage: str, mapped_base: str) -> bool:
    if not raw_stage:
        return True
    if raw_stage in _EXCLUDED_STAGE_NAMES or mapped_base in _EXCLUDED_STAGE_NAMES:
        return True
    if mapped_base in _RESOLVER_MARKER_NAMES:
        duration = _nonneg_float(record.get("duration_seconds"))
        return _is_runner_owned(record) or duration is None or duration == 0.0
    return False


def _is_report_repair(record: dict[str, Any], raw_stage: str) -> bool:
    original = record.get("original_stage")
    return bool(
        record.get("report_only")
        or raw_stage.endswith("_report_repair")
        or (isinstance(original, str) and bool(original) and original != raw_stage)
    )


def _is_failed_attempt(record: dict[str, Any]) -> bool:
    exit_code = record.get("exit_code")
    return bool(
        record.get("rejected")
        or record.get("interrupted")
        or record.get("abandoned")
        or record.get("timed_out")
        or (type(exit_code) is int and exit_code != 0)
    )


def _route_dict(record: dict[str, Any], model: str) -> dict[str, str | None]:
    launch_route = _dict(record.get("launch_route"))
    exec_id = _dict(record.get("execution_identity"))
    provider = next(
        (
            str(candidate)
            for candidate in (
                exec_id.get("provider"),
                record.get("provider"),
                launch_route.get("provider"),
                record.get("engine"),
            )
            if isinstance(candidate, str) and candidate
        ),
        None,
    )
    level = next(
        (
            str(candidate)
            for candidate in (
                record.get("reasoning_effort"),
                launch_route.get("reasoning_effort"),
                exec_id.get("reasoning_effort"),
            )
            if isinstance(candidate, str) and candidate
        ),
        None,
    )
    return {
        "provider": provider,
        "model": model if model else None,
        "level": level,
    }


def _configured_route(state: RunState, candidate_roles: tuple[str, ...]) -> dict[str, str | None] | None:
    settings = _dict(state.get("settings"))
    configured = _dict(settings.get("roles"))
    engine = settings.get("engine")
    default_provider = settings.get("provider") or (engine if isinstance(engine, str) and engine else None)
    for slot in candidate_roles:
        route = _dict(configured.get(slot))
        if not route:
            continue
        provider = next(
            (
                str(item)
                for item in (route.get("provider"), route.get("engine"), default_provider)
                if isinstance(item, str) and item
            ),
            None,
        )
        model = route.get("model")
        level = route.get("reasoning_effort")
        if provider or model or level:
            return {
                "provider": provider,
                "model": str(model) if isinstance(model, str) and model else None,
                "level": str(level) if isinstance(level, str) and level else None,
            }
    return None


def _prompt_bytes(record: dict[str, Any], run_dir: Path | None) -> int | None:
    for candidate in (record.get("prompt_bytes"), _dict(record.get("context")).get("prompt_bytes")):
        count = _nonneg_int(candidate)
        if count is not None:
            return count
    prompt_ref = record.get("prompt")
    if not isinstance(prompt_ref, str) or not prompt_ref:
        return None
    try:
        path = Path(prompt_ref).resolve()
        if run_dir is not None and not path.is_relative_to(run_dir.resolve()):
            return None
        return path.stat().st_size
    except (OSError, ValueError, RuntimeError):
        return None


def _first_request_cache(record: dict[str, Any]) -> tuple[int | None, int | None]:
    accounting = _dict(_dict(_dict(record.get("metrics")).get("request_context")).get("accounting"))
    requests = accounting.get("requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        return None, None
    tokens = _dict(requests[0].get("tokens"))
    return _nonneg_int(tokens.get("cached_input_tokens")), _nonneg_int(tokens.get("cache_write_tokens"))


def _empty_bucket() -> dict[str, Any]:
    return {"attempts": 0, "seconds": 0.0, "complete": True}


def _accumulate(bucket: dict[str, Any], duration: float | None) -> None:
    bucket["attempts"] += 1
    if duration is None:
        bucket["complete"] = False
    else:
        bucket["seconds"] = round(bucket["seconds"] + duration, 3)


def _has_human_wait(state: RunState, launched_at: object, event_at: object) -> bool | None:
    launch_dt = _parse_iso(launched_at)
    event_dt = _parse_iso(event_at)
    if launch_dt is None or event_dt is None or event_dt < launch_dt:
        return None
    escalations = _dict(_dict(state.get("resolver")).get("human_escalations"))
    for entry in escalations.values():
        row = _dict(entry)
        response = _dict(row.get("response"))
        issued_dt = _parse_iso(row.get("issued_at"))
        responded_dt = _parse_iso(response.get("at"))
        if (
            row.get("status") == "consumed"
            and response.get("actor") == "user_cli"
            and issued_dt is not None
            and responded_dt is not None
            and responded_dt >= issued_dt
            and issued_dt >= launch_dt
            and issued_dt < event_dt
            and responded_dt <= event_dt
        ):
            return True
    for obs in state.get("efficiency_observations") or ():
        row = _dict(obs)
        if row.get("kind") != "activity" or row.get("category") != "waiting":
            continue
        start_dt = _parse_iso(row.get("started_at"))
        end_dt = _parse_iso(row.get("finished_at"))
        if (
            start_dt is not None
            and end_dt is not None
            and end_dt >= start_dt
            and start_dt >= launch_dt
            and end_dt <= event_dt
        ):
            return True
    for key in ("user_events", "brief_feedback"):
        events = state.get(key)
        if not isinstance(events, (list, tuple)):
            continue
        for raw_event in events:
            row = _dict(raw_event)
            if row.get("actor") not in (None, "user_cli"):
                continue
            event_time = _parse_iso(row.get("at"))
            if event_time is not None and launch_dt < event_time <= event_dt:
                return True
    return False


def _interaction_route(
    event: str,
    event_at: object,
    all_records: list[dict[str, Any]],
    state: RunState,
    model_of: Callable[[dict[str, Any]], str],
) -> dict[str, str | None] | None:
    event_dt = _parse_iso(event_at)
    model_records = [
        r for r in all_records if not _is_runner_owned(r) and str(r.get("stage") or "") not in _EXCLUDED_STAGE_NAMES
    ]
    if event == "question":
        for record in model_records:
            mapped = _mapped_base_stage(str(record.get("stage") or ""))
            if mapped in _QUESTION_STAGES:
                return _route_dict(record, model_of(record))
        for record in model_records:
            started_dt = _parse_iso(record.get("started_at"))
            if event_dt is None or started_dt is None or started_dt <= event_dt:
                return _route_dict(record, model_of(record))
        return _configured_route(state, ("requirements", "planner", "task_planner"))
    if event == "plan":
        for record in model_records:
            mapped = _mapped_base_stage(str(record.get("stage") or ""))
            if mapped in _PLAN_STAGES:
                return _route_dict(record, model_of(record))
        return _configured_route(state, ("planner", "task_planner", "plan_reviewer"))
    for record in model_records:
        mapped = _mapped_base_stage(str(record.get("stage") or ""))
        if mapped == "builder":
            return _route_dict(record, model_of(record))
    return _configured_route(state, ("builder",))


def _first_interaction(
    state: RunState,
    all_records: list[dict[str, Any]],
    model_of: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    timing = interaction_timing.project(state)
    launched_at = timing.get("launched_at")
    result: dict[str, Any] = {
        "launched_at": launched_at if isinstance(launched_at, str) and launched_at else None,
    }
    for event in interaction_timing.EVENTS:
        at_key = f"first_{event}_at"
        sec_key = f"first_{event}_seconds"
        event_at = timing.get(at_key)
        secs = _nonneg_float(timing.get(sec_key))
        has_wait = _has_human_wait(state, launched_at, event_at) if secs is not None else None
        route = _interaction_route(event, event_at, all_records, state, model_of) if secs is not None else None
        result[f"first_{event}"] = {
            "seconds": round(secs, 3) if secs is not None else None,
            "human_wait": has_wait,
            "route": route,
        }
    return result


def _plan_size(state: RunState) -> str | None:
    planning = _dict(state.get("planning"))
    saved = _dict(planning.get("adaptive")).get("size")
    if saved in ("small", "medium", "large"):
        return str(saved)
    body = _dict(_dict(state.get("goal_contract")).get("body"))
    if not body:
        return None
    milestones = body.get("milestones")
    if milestones is not None and not isinstance(milestones, list):
        return None
    if isinstance(milestones, list) and any(not isinstance(row, dict) for row in milestones):
        return None
    try:
        sized = adaptive.plan_size(body).get("size")
        return str(sized) if sized in ("small", "medium", "large") else None
    except (TypeError, KeyError, AttributeError):
        return None


def _outcome_state(run_status_val: str | None, state: RunState) -> tuple[str, str | None]:
    if run_status_val in _COMPLETED_STATUSES:
        return "completed", None
    if run_status_val in _ABANDONED_STATUSES or state.get("abandoned") is True:
        return "abandoned", None
    if isinstance(run_status_val, str) and (
        run_status_val.startswith(("PAUSED_", "BLOCKED_"))
        or run_status_val.endswith("_REWORK_REQUIRED")
        or run_status_val in _PAUSED_EXACT_STATUSES
    ):
        return "paused", run_status_val
    return "running", None


def _started_at(state: RunState, records: list[dict[str, Any]], launched_at: object) -> str | None:
    if isinstance(launched_at, str) and _parse_iso(launched_at) is not None:
        return launched_at
    created_at = state.get("created_at")
    if isinstance(created_at, str) and _parse_iso(created_at) is not None:
        return created_at
    earliest_str: str | None = None
    earliest_dt: dt.datetime | None = None
    for record in records:
        for key in ("started_at", "finished_at"):
            val = record.get(key)
            parsed = _parse_iso(val)
            if isinstance(val, str) and parsed is not None and (earliest_dt is None or parsed < earliest_dt):
                earliest_dt, earliest_str = parsed, val
    return earliest_str


def _wall_seconds(
    state: RunState,
    records: list[dict[str, Any]],
    started_at: str | None,
    *,
    has_running_stage: bool,
    has_pending_workers: bool,
) -> float | None:
    if started_at is None or has_running_stage or has_pending_workers:
        return None
    start_dt = _parse_iso(started_at)
    if start_dt is None:
        return None
    latest_dt: dt.datetime | None = None
    for val in (state.get("completed_at"), state.get("finished_at")):
        parsed = _parse_iso(val)
        if parsed is not None and (latest_dt is None or parsed > latest_dt):
            latest_dt = parsed
    for record in records:
        for key in ("finished_at", "started_at"):
            parsed = _parse_iso(record.get(key))
            if parsed is not None and (latest_dt is None or parsed > latest_dt):
                latest_dt = parsed
    if latest_dt is None or latest_dt < start_dt:
        return None
    return round((latest_dt - start_dt).total_seconds(), 3)


def _spent_usd(
    state: RunState,
    priced_rows: list[dict[str, Any]],
    *,
    has_running_stage: bool,
    has_pending_workers: bool,
) -> dict[str, Any]:
    known = 0.0
    unpriced = 0
    partial = 0
    priced_seen = False
    for row in priced_rows:
        basis = row.get("basis")
        if basis in ("reported", "estimated"):
            cost = _nonneg_float(row.get("cost_usd"))
            if cost is not None:
                known += cost
                priced_seen = True
            else:
                unpriced += 1
        elif basis == "unknown":
            unpriced += 1
        if row.get("partial") is True:
            partial += 1
    carried = _dict(state.get("inherited_spend"))
    inherited = _nonneg_float(carried.get("usd")) or 0.0
    if inherited > 0:
        known += inherited
        priced_seen = True
    unpriced += _nonneg_int(carried.get("unpriced_stages")) or 0
    partial += _nonneg_int(carried.get("partial_stages")) or 0
    complete = (
        bool(priced_rows or inherited > 0)
        and unpriced == 0
        and partial == 0
        and not has_running_stage
        and not has_pending_workers
    )
    value = round(known, 6) if (priced_seen or (bool(priced_rows) and unpriced == 0)) else None
    return {"value": value, "complete": complete}


def ledger_fields(
    state: RunState,
    *,
    records: list[dict[str, Any]],
    priced_rows: list[dict[str, Any]],
    has_running_stage: bool,
    has_pending_workers: bool,
    model_of: Callable[[dict[str, Any]], str],
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Compute additive outcome, rejection, latency and prompt fields for one usage.jsonl row."""
    totals = {bucket: _empty_bucket() for bucket in OUTCOME_BUCKETS}
    by_job: dict[str, dict[str, dict[str, Any]]] = {}
    rejections: list[dict[str, Any]] = []
    stage_prompts: list[dict[str, Any]] = []
    model_calls = 0
    missing_duration = False

    for record in records:
        raw_stage = str(record.get("stage") or "")
        mapped_base = _mapped_base_stage(raw_stage)
        if _is_excluded_stage(record, raw_stage, mapped_base):
            continue
        is_repair = _is_report_repair(record, raw_stage)
        is_failed = _is_failed_attempt(record)
        bucket = "report_repair" if is_repair else ("rejected" if is_failed else "accepted")
        target_raw = str(record.get("original_stage") or raw_stage.removesuffix("_report_repair") or raw_stage)
        target_mapped = names.stage(target_raw)
        job = roles.screen_name(target_raw, state) or target_mapped
        duration = _nonneg_float(record.get("duration_seconds"))
        if duration is None:
            missing_duration = True

        _accumulate(totals[bucket], duration)
        job_buckets = by_job.setdefault(job, {name: _empty_bucket() for name in OUTCOME_BUCKETS})
        _accumulate(job_buckets[bucket], duration)

        model = model_of(record)
        route = _route_dict(record, model)

        if is_failed:
            reason_class = rejection_classes.classify(
                record.get("rejection_reason"),
                timeout_kind=record.get("timeout_kind"),
                timeout_reason=record.get("timeout_reason"),
                timed_out=bool(record.get("timed_out")),
            )
            rejections.append(
                {
                    "stage": target_mapped,
                    "job": job,
                    "class": reason_class,
                    "seconds": round(duration, 3) if duration is not None else None,
                    "report_repair": is_repair,
                    "route": route,
                }
            )

        if not _is_runner_owned(record):
            model_calls += 1
            cache_read, cache_write = _first_request_cache(record)
            stage_prompts.append(
                {
                    "stage": target_mapped,
                    "job": job,
                    "report_repair": is_repair,
                    "prompt_bytes": _prompt_bytes(record, run_dir),
                    "first_request_cache_read_tokens": cache_read,
                    "first_request_cache_write_tokens": cache_write,
                    "route": route,
                }
            )

    active = _dict(state.get("active_stage"))
    all_records = [*records, *([active] if active and has_running_stage else [])]
    first_interaction = _first_interaction(state, all_records, model_of)
    raw_prov = evidence_provenance.projection(_dict(state.get("settings")))
    if isinstance(raw_prov, dict):
        provenance = str(raw_prov.get("kind") or "unknown")
    elif isinstance(raw_prov, str) and raw_prov in evidence_provenance.KINDS:
        provenance = raw_prov
    else:
        provenance = "unknown"
    run_status_val = state.get("status") if isinstance(state.get("status"), str) else None
    outcome_state, pause_reason = _outcome_state(run_status_val, state)
    started_at = _started_at(state, records, first_interaction.get("launched_at"))
    wall_seconds = _wall_seconds(
        state,
        records,
        started_at,
        has_running_stage=has_running_stage,
        has_pending_workers=has_pending_workers,
    )
    spent = _spent_usd(
        state,
        priced_rows,
        has_running_stage=has_running_stage,
        has_pending_workers=has_pending_workers,
    )

    return {
        "ledger_schema": LEDGER_SCHEMA,
        "evidence_provenance": provenance,
        "started_at": started_at,
        "outcome": {
            "state": outcome_state,
            "run_status": run_status_val,
            "pause_reason_code": pause_reason,
            "plan_size": _plan_size(state),
            "model_calls": model_calls,
            "wall_seconds": wall_seconds,
            "spent_usd": spent,
        },
        "outcomes": {
            "complete": (not missing_duration) and (not has_running_stage) and (not has_pending_workers),
            "totals": totals,
            "by_job": by_job,
        },
        "rejections": rejections,
        "first_interaction": first_interaction,
        "stage_prompts": stage_prompts,
    }
