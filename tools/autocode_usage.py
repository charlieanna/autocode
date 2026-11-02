"""Tokens and cost of every task, measured from what each run already records.

A run saves token counts per stage (`metrics.provider_tokens`) and, when its provider reports
one, the stage's cost (`metrics.provider_cost_usd`). This module turns those into

  summary(state)   the totals for one run, by role: the `usage` field of the status view;
  record(...)      one row per run in PROJECT/.autocode/usage.jsonl, kept current at every
                   checkpoint save (autocode_status.persist), so it is right while a task runs
                   and after it pauses, completes or is killed;
  report(project)  the ledger as a table; `python3 tools/autocode_usage.py [PROJECT]`.

Cost has three bases and the output says which: "reported" (the provider's own number, as the
built-in claude provider writes it), "estimated" (tokens at the flat comparison rates below, only
for models listed there) and unknown. An estimate is never added to a reported cost without being
shown separately, and unknown is never zero: `complete` is false while any stage lacks a cost.
A stage that only the runner executed costs nothing. Imports only the standard library and
the request accounting, role catalogue and utilities; RunState is type checking only.
`metrics.provider_tokens_partial` marks known consumption from an interrupted stream as a lower bound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import datetime as dt
import json
import math
import os
import sys
import tempfile
import warnings
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_request_usage as request_usage
    from . import autocode_roles as roles
    from . import autocode_route_evidence as route_evidence
    from . import autocode_util as util
except ImportError:
    import autocode_request_usage as request_usage
    import autocode_roles as roles
    import autocode_route_evidence as route_evidence
    import autocode_util as util

# Historical comparison rates, USD per 1M inclusive input/output tokens.
# These are not verified current provider prices or subscription charges.
REFERENCE_PRICES = {
    "zai-coding-plan/glm-5.3": {"input": 0.60, "output": 2.20},
    # Historical comparison rates also apply to configured MiMo routes.
    "xiaomi-token-plan-sgp/mimo-v2.6-pro": {"input": 0.30, "output": 1.20},
}
LEDGER = "usage.jsonl"
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")

# Run directories whose last ledger row is current, so an unchanged save writes nothing.
_written: dict[str, str] = {}


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _money(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def reported_cost(rows):
    """The cost a provider reported for a stage's turns, or None when any turn lacks one.

    A zero is not a price (a subscription plan reports 0 for work that was not free), so a total of
    zero counts as not reported.
    """
    turns = [row for row in rows if row.get("type") in ("turn.completed", "turn.failed")]
    costs = [row.get("cost_usd") for row in turns]
    if not costs or any(_money(cost) is None for cost in costs):
        return None
    return sum(costs) or None


def estimate(model, tokens, at=None):
    """Tokens priced at the comparison rates; None for an unlisted model or unknown token counts.

    The comparison rates are frozen, so `at` (when the stage finished) changes nothing.
    """
    prices, inp, out = (
        REFERENCE_PRICES.get(model),
        _count(tokens.get("input_tokens")),
        _count(tokens.get("output_tokens")),
    )
    return (
        None if prices is None or inp is None or out is None else (inp * prices["input"] + out * prices["output"]) / 1e6
    )


def cost_state_cost(metrics, sessions=None):
    """A CLI-reported cost-state amount for exactly one attempt's own session spend (#2592).

    The cost state is cumulative per provider session, so the amount here is the delta against
    `previous_total_usd` (a record without one states the whole session, which is that attempt's
    spend when its session started there). The record must bind to the attempt — its session must
    appear in the attempt's own request receipts or observed provider sessions — and its arithmetic
    must hold. Repeated reports of the same record count once; conflicting distinct amounts resolve
    only through their recorded timestamps. Anything else stays unknown rather than guessed at.
    """
    records = metrics.get("cost_state")
    records = [records] if isinstance(records, dict) else records if isinstance(records, list) else []
    bound = {session for session in (sessions or ()) if isinstance(session, str)}
    for item in _dict(_dict(metrics.get("request_context")).get("accounting")).get("requests") or []:
        identity = item.get("identity") if isinstance(item, dict) else None
        if isinstance(identity, list) and identity and isinstance(identity[0], str):
            bound.add(identity[0])
    observed = metrics.get("provider_sessions")
    if isinstance(observed, list):
        bound.update(session for session in observed if isinstance(session, str))
    deltas = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("session_id"), str):
            continue
        session = record["session_id"]
        if session not in bound:
            continue  # an unbound record prices no attempt (#2592)
        total = _money(record.get("total_cost_usd"))
        raw_previous = record.get("previous_total_usd")
        previous = 0.0 if raw_previous is None else _money(raw_previous)
        if total is None or previous is None:
            continue
        delta = round(total - previous, 10)
        if not delta > 0:
            continue  # a zero or reversed delta is not a price
        stamp = record.get("updated_at")
        stamps = deltas.setdefault(session, {}).setdefault(delta, [])
        stamps.append(stamp if type(stamp) in (int, float) and math.isfinite(stamp) else None)
    if len(deltas) != 1:
        return None  # one attempt's cost needs one session's amount
    values = next(iter(deltas.values()))
    if len(values) == 1:
        return round(next(iter(values)), 10)
    ordered = {delta: max(stamps) for delta, stamps in values.items() if stamps and None not in stamps}
    if len(ordered) != len(values):
        return None  # conflicting amounts that cannot be ordered stay unknown
    latest = max(ordered.values())
    winners = [delta for delta, stamp in ordered.items() if stamp == latest]
    return round(winners[0], 10) if len(winners) == 1 else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _model(record):
    """The model a stage ran: the launch command first (`--model X` or `--model=X`), then the saved launch route."""
    command = record.get("command")
    if isinstance(command, list):
        for index, arg in enumerate(command):
            if arg in ("--model", "-m") and index + 1 < len(command):
                return str(command[index + 1])
            if isinstance(arg, str) and arg.startswith("--model="):
                return arg.partition("=")[2]
    for model in (_dict(record.get("launch_route")).get("model"), record.get("model")):
        if isinstance(model, str) and model:
            return model
    return ""


def pending_workers(state: RunState):
    """The parallel Builder rows still running: their spend reaches the parent only when the batch is accounted."""
    workers = _dict(state.get("orchestration_batch")).get("workers") or []
    return [
        row
        for row in (workers if isinstance(workers, list) else [])
        if isinstance(row, dict) and row.get("status") not in ("COMPLETE", "INTEGRATED", "DONE")
    ]


def stage_rows(state: RunState, estimator=None):
    """One row per finished stage: role, model, tokens, cost and where the cost came from.

    `estimator(model, tokens, finished_at)` prices a stage without a reported cost; the default is
    `estimate`, the frozen comparison rates. autocode_cost passes the dated price table instead, which
    prices each stage at the rate in force when it finished.
    """
    estimator = estimator or estimate
    rows = []
    stages = state.get("stages")
    for record in [item for item in stages if isinstance(item, dict)] if isinstance(stages, list) else []:
        metrics = _dict(record.get("metrics"))
        tokens = dict(_dict(metrics.get("provider_tokens")))
        model = _model(record)
        native = _dict(_dict(metrics.get("request_context")).get("accounting"))
        requests = native.get("requests") or []
        # A cut stream's saved receipts are the only usage left; their sums are a lower bound
        # (hence partial below), and a request missing a key leaves that key unknown (#2592).
        fallback_used = False
        if requests and any(_count(tokens.get(key)) is None for key in ("input_tokens", "output_tokens")):
            for key in TOKEN_KEYS:
                if _count(tokens.get(key)) is None:
                    values = [_count(_dict(item.get("tokens")).get(key)) for item in requests]
                    if all(value is not None for value in values):
                        tokens[key] = sum(values)
                        fallback_used = True
        if record.get("runner_owned") is True and record.get("engine") == "runner":
            cost, basis = 0.0, "runner"
        elif _money(metrics.get("provider_cost_usd")) is not None:
            cost, basis = metrics["provider_cost_usd"], "reported"
        elif (observation := cost_state_cost(metrics)) is not None:
            cost, basis = observation, "reported"
        elif (guess := estimator(model, tokens, record.get("finished_at"))) is not None:
            cost, basis = guess, "estimated"
        else:
            cost, basis = None, "unknown"
        row_tokens = {key: _count(tokens.get(key)) for key in TOKEN_KEYS}
        rows.append(
            {
                "stage": record.get("stage"),
                "role": str(record.get("role") or record.get("route_role") or ""),
                "model": model,
                "tokens": row_tokens,
                "partial": metrics.get("provider_tokens_partial") is True
                or (fallback_used and native.get("complete") is not True),
                # Process completion is not a passing review or accepted work.
                "process_outcome": (
                    "interrupted"
                    if any(record.get(flag) is True for flag in ("timed_out", "abandoned", "interrupted"))
                    else "failed"
                    if type(record.get("exit_code")) is int and record["exit_code"] != 0
                    else "completed"
                    if type(record.get("exit_code")) is int and record["exit_code"] == 0
                    else "unknown"
                ),
                "cost_usd": cost,
                "basis": basis,
                "finished_at": record.get("finished_at"),
                "unpriced_reason": _unpriced_reason(model, row_tokens) if basis == "unknown" else None,
            }
        )
    return rows


def _unpriced_reason(model, tokens):
    """Why a stage has no cost (#1713): no token counts recorded, no model identified, or no rate for its model."""
    if tokens.get("input_tokens") is None or tokens.get("output_tokens") is None:
        return "usage_missing"
    return "model_unknown" if not model else "rate_unavailable"


def _sum(values):
    return sum(value for value in values if value is not None)


def summary(state: RunState):
    """Totals for one run. Sums cover the stages that reported; `unknown_stages` counts those that did not."""
    rows = stage_rows(state)
    roles: dict = {}
    for row in rows:
        entry = roles.setdefault(
            row["role"] or "other",
            {"stages": 0, "input_tokens": 0, "output_tokens": 0, "reported_usd": 0.0, "estimated_usd": 0.0},
        )
        entry["stages"] += 1
        entry["input_tokens"] += row["tokens"]["input_tokens"] or 0
        entry["output_tokens"] += row["tokens"]["output_tokens"] or 0
        if row["basis"] in ("reported", "estimated"):
            entry[row["basis"] + "_usd"] += row["cost_usd"]
    reported = _sum(row["cost_usd"] for row in rows if row["basis"] == "reported")
    guessed = _sum(row["cost_usd"] for row in rows if row["basis"] == "estimated")
    unknown = sum(row["basis"] == "unknown" for row in rows)
    partial = sum(row["partial"] for row in rows)
    return {
        "stages": len(rows),
        "active_stage": _dict(state.get("active_stage")).get("stage"),
        "tokens": {key: _sum(row["tokens"][key] for row in rows) for key in TOKEN_KEYS},
        "cost_usd": {
            "reported": round(reported, 6),
            "estimated": round(guessed, 6),
            "complete": unknown == 0 and partial == 0 and not state.get("active_stage"),
        },
        "unknown_stages": unknown,
        "partial_stages": partial,
        "by_role": {
            role: {key: round(value, 6) if isinstance(value, float) else value for key, value in entry.items()}
            for role, entry in sorted(roles.items())
        },
        # Unlike the legacy known subtotals above, totals here are nullable and
        # each quantity has its own coverage, including active/failed attempts.
        "accounting": accounting(state),
        "legacy_scope": "Legacy fields are saved-stage known subtotals; nullable all-attempt quantities are in accounting",
    }


def attempt_identity(record, *, namespace="", index=None):
    """Artifact identity survives parent imports; anonymous legacy rows never merge."""
    for field in ("events", "output"):
        value = record.get(field)
        if isinstance(value, str) and value:
            path = Path(value)
            if path.is_absolute():
                return f"{field}:{path.resolve()}"
            if namespace:
                return f"{field}:{(Path(namespace) / path).resolve()}"
    for field in ("worker_attempt", "attempt_id"):
        if isinstance(record.get(field), str) and record[field]:
            return f"{namespace}:{field}:{record[field]}"
    return f"{namespace}:unidentified:{index}"


def run_identity(value):
    """Canonicalize public run paths without opening any run state."""
    if not value:
        return None
    path = Path(str(value))
    return str(path.resolve()) if path.is_absolute() else str(value)


def _quantity(values, *, uncertain=False):
    known = [value for value in values if value is not None]
    complete = len(known) == len(values) and not uncertain
    return {
        "value": sum(known) if complete else None,
        "known": sum(known),
        "complete": complete,
        "known_items": len(known),
        "unknown_items": len(values) - len(known),
    }


def accounting(state: RunState):
    """Read supplied attempt logs, never another run's private state.

    Saved request receipts are used when present. An explicit event path is
    refreshed for live/partial usage; unreadable logs remain a coverage warning.
    Cross-run callers use combine_accounting with public status attempts.
    """
    records = state.get("stages")
    records = [row for row in records if isinstance(row, dict)] if isinstance(records, list) else []
    active = _dict(state.get("active_stage"))
    if active:
        records = [*records, active]
    namespace = run_identity(state.get("run_dir") or state.get("task_id")) or ""
    attempts = []
    for index, record in enumerate(records):
        runner = record.get("runner_owned") is True and record.get("engine") == "runner"
        metrics = _dict(record.get("metrics"))
        context = _dict(metrics.get("request_context"))
        native = _dict(context.get("accounting"))
        is_active = record is active
        if not runner and isinstance(record.get("events"), str) and record["events"]:
            saved = _dict(native.get("event_file"))
            try:
                path = Path(record["events"])
                stat = path.stat()
                current = (
                    saved.get("path") == str(path.resolve())
                    and saved.get("sha256")
                    and saved.get("size") == stat.st_size
                    and saved.get("mtime_ns") == stat.st_mtime_ns
                )
            except (OSError, ValueError):
                current = False
            if is_active or not current:
                native = request_usage.read(record["events"])["accounting"]
        issues = [] if runner else list(native.get("issues") or [])
        requests = [] if runner else native.get("requests") or []
        if record.get("engine") == "opencode" and not requests:
            issues.append("No native request finishes; unobserved usage is unknown")
        tokens = {
            key: 0 if runner else _count(_dict(metrics.get("provider_tokens")).get(key))
            for key in request_usage.TOKEN_KEYS
        }
        # Do not fall back to a stage sum when exact native counters are partial:
        # that would conceal conflicting/replayed/missing requests.
        if requests:
            tokens = {
                key: sum(row["tokens"][key] for row in requests)
                if all(row["tokens"].get(key) is not None for row in requests)
                else None
                for key in request_usage.TOKEN_KEYS
            }
        model = _model(record)
        reported = _money(metrics.get("provider_cost_usd"))
        reported = reported if reported else None
        if requests:
            costs = [row.get("reported_cost_usd") for row in requests]
            reported = sum(costs) if all(cost is not None for cost in costs) else None
        if reported is None and not runner:
            # A cost-state observation is the failed call's own spend when its requests never
            # priced (#2592); it replaces an estimate, never adds to a reported amount.
            sessions = [item.get("identity") or [None] for item in requests]
            reported = cost_state_cost(metrics, sessions={row[0] for row in sessions if isinstance(row[0], str)})
        guessed = None if reported is not None or runner else estimate(model, tokens)
        local_id = record.get("attempt_id")
        if not local_id and type(record.get("iteration")) is int and record.get("output"):
            local_id = f"{record['iteration']:03d}/{Path(record['output']).stem}"
        attempts.append(
            {
                "identity": attempt_identity(record, namespace=namespace, index=index),
                "attempt_id": local_id,
                "run_id": namespace or None,
                "worker_attempt": record.get("worker_attempt"),
                "stage": record.get("stage"),
                "role": record.get("role"),
                # The slot whose route the stage launched; a unit role can run another slot's job (#2114).
                "route_role": record.get("route_role"),
                "job": roles.screen_name(record.get("stage") or "", state),
                "model": model,
                **({"prompts_hash": record["prompts_hash"]} if "prompts_hash" in record else {}),
                "original_stage": record.get("original_stage"),
                "launch_route": deepcopy(_dict(record.get("launch_route"))),
                **({"reasoning_effort": record["reasoning_effort"]} if "reasoning_effort" in record else {}),
                "execution_identity": deepcopy(
                    route_evidence.execution_for_attempt(
                        {
                            "execution_identity": record.get("execution_identity"),
                            "model": model,
                            "role": record.get("role"),
                            "route_role": record.get("route_role"),
                            "engine": record.get("engine"),
                            "launch_route": _dict(record.get("launch_route")),
                            **(
                                {"reasoning_effort": record["reasoning_effort"]} if "reasoning_effort" in record else {}
                            ),
                        }
                    )
                )
                or {},
                "engine": record.get("engine"),
                "events": record.get("events"),
                "output": record.get("output"),
                "recovery_novelty": deepcopy(_dict(record.get("recovery_novelty"))),
                "task_id": record.get("task_id"),
                "source_revision": record.get("source_revision"),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "duration_seconds": _money(record.get("duration_seconds")),
                "runner_owned": runner,
                "active": is_active,
                "rejected": bool(record.get("rejected")),
                "interrupted": bool(record.get("interrupted") or record.get("abandoned")),
                "timed_out": bool(record.get("timed_out")),
                "exit_code": record.get("exit_code"),
                "report_only": bool(record.get("report_only")),
                "planning": bool(record.get("planning")),
                "tokens": tokens,
                "requests": requests,
                "reported_cost_usd": 0.0 if runner else reported,
                "event_file": deepcopy(native.get("event_file")),
                "historical_estimated_cost_usd": guessed,
                "issues": issues,
                "request_coverage_complete": runner or bool(requests) and not issues and not is_active,
                "unfinished_requests": native.get("unfinished_requests"),
                "usage_basis": "runner" if runner else "native_requests" if requests else "stage_counters",
                "unobserved_usage_possible": bool(is_active or issues),
                "identity_complete": ":unidentified:" not in attempt_identity(record, namespace=namespace, index=index),
            }
        )
    result = combine_accounting(attempts)
    pending = pending_workers(state)
    result["parallel_workers_pending"] = [row.get("milestone_id") for row in pending]
    result["parallel_worker_runs"] = [
        {"run_id": run_identity(row.get("run_dir")), "milestone_id": row.get("milestone_id")} for row in pending
    ]
    if result["parallel_workers_pending"]:
        result["issues"].append(
            "Active parallel workers require their public TaskRun views; parent imports are not live totals"
        )
        for value in result["tokens"].values():
            value.update(value=None, complete=False)
        result["cost"]["reported_usd"].update(value=None, complete=False)
        result["cost"]["historical_estimated_usd"].update(value=None, complete=False)
        result["provider_requests"].update(value=None, complete=False)
        result["complete"] = False
    return result


def combine_accounting(attempts):
    """Combine public attempt rows, deduplicating both imports and native events."""
    unique, issues = {}, []
    for index, row in enumerate(attempts):
        row = deepcopy(row)
        key = row["identity"] if row.get("identity_complete") else f"unidentified:{index}"
        previous = unique.get(key)
        if previous:
            if previous.get("active") and not row.get("active"):
                unique[key] = row
            elif not previous.get("active") and row.get("active"):
                continue
            elif any(
                previous.get(field) != row.get(field)
                for field in ("tokens", "requests", "model", "reported_cost_usd", "historical_estimated_cost_usd")
            ):
                previous.update(
                    tokens=dict.fromkeys(request_usage.TOKEN_KEYS),
                    requests=[],
                    reported_cost_usd=None,
                    historical_estimated_cost_usd=None,
                    request_coverage_complete=False,
                    unobserved_usage_possible=True,
                )
                issues.append(f"Conflicting attempt snapshots: {key}")
            continue
        unique[key] = row
    rows, requests, owners, sources = list(unique.values()), {}, {}, []
    for row in rows:
        issues.extend(f"{row['identity']}: {issue}" for issue in row.get("issues") or [])
        if not row.get("identity_complete"):
            issues.append(f"Missing exact attempt identity: {row['identity']}")
        if not row.get("requests"):
            sources.append(row)
        for request in row.get("requests") or []:
            key = tuple(request["identity"])
            owners.setdefault(key, row["identity"])
            item = {**request, "model": row.get("model")}
            if key in requests and (
                requests[key].get("conflict")
                or any(requests[key].get(field) != item.get(field) for field in ("tokens", "reported_cost_usd"))
            ):
                issues.append(f"Conflicting native request: {'/'.join(key)}")
                item = {
                    "identity": list(key),
                    "tokens": dict.fromkeys(request_usage.TOKEN_KEYS),
                    "reported_cost_usd": None,
                    "model": None,
                    "conflict": True,
                }
            elif key in requests and requests[key].get("model") != item.get("model"):
                issues.append(f"Conflicting model attribution for native request: {'/'.join(key)}")
                requests[key].update(model=None, model_conflict=True)
            if key not in requests or item.get("conflict"):
                requests[key] = item
    sources.extend(requests.values())
    costs = []
    for row in rows:
        charged = [request for key, request in requests.items() if owners[key] == row["identity"]]
        portions = charged if row.get("requests") else [row]
        # Each attempt contributes its own amount exactly once (#2592): its receipts' prices
        # when they carry every one, its own cost-state observation when they are unpriced, and
        # nothing when every receipt was an already-counted duplicate. A conflict stays unknown.
        # The same decision prices the attribution: priced receipts attribute per request, a
        # distinct attempt-bound amount attributes as that one amount, and never invented
        # request prices (#2599 review).
        if not row.get("requests"):
            amount, price_parts = row.get("reported_cost_usd"), [row]
        elif any(request.get("conflict") for request in charged):
            amount, price_parts = None, charged
        elif not charged:
            # The shared receipts were already counted; a pure replay adds nothing. Only a
            # distinct attempt-bound amount (its own cost-state delta, not the receipts) is
            # not covered by the dedup and survives it (#2599 review).
            receipt_derived = (
                all(request.get("reported_cost_usd") is not None for request in row.get("requests") or [])
                or row.get("reported_cost_usd") is None
            )
            amount = 0.0 if receipt_derived else row.get("reported_cost_usd")
            price_parts = [] if receipt_derived else [row]
        elif all(request.get("reported_cost_usd") is not None for request in charged):
            amount = sum(request["reported_cost_usd"] for request in charged)
            price_parts = charged
        else:
            amount, price_parts = row.get("reported_cost_usd"), [row]
        costs.append(amount)
        row["attributed_tokens"] = {
            key: _quantity(
                [_dict(part.get("tokens")).get(key) for part in portions],
                uncertain=row.get("unobserved_usage_possible", False),
            )
            for key in request_usage.TOKEN_KEYS
        }
        row["attributed_requests"] = len(charged) if row.get("requests") or row.get("runner_owned") else None
        row["attributed_reported_usd"] = _quantity(
            [part.get("reported_cost_usd") for part in price_parts],
            uncertain=row.get("unobserved_usage_possible", False),
        )
    uncertain = any(row.get("unobserved_usage_possible") for row in rows)
    tokens = {
        key: _quantity([_dict(row.get("tokens")).get(key) for row in sources], uncertain=uncertain)
        for key in request_usage.TOKEN_KEYS
    }
    # Historic flat estimates are stage-based comparison figures, never combined
    # with native reported cost or described as a current API rate-card estimate.
    estimates = [row.get("historical_estimated_cost_usd") for row in rows if not row.get("runner_owned")]
    return {
        "schema": 1,
        "attempts": rows,
        "attempt_count": len(rows),
        "active_attempts": sum(bool(row.get("active")) for row in rows),
        "tokens": tokens,
        "provider_requests": {
            "observed": len(requests),
            "unfinished_observed": sum(row.get("unfinished_requests") or 0 for row in rows),
            "value": len(requests) if all(row.get("request_coverage_complete") for row in rows) else None,
            "complete": all(row.get("request_coverage_complete") for row in rows),
        },
        "cost": {
            "reported_usd": _quantity(costs, uncertain=uncertain),
            "historical_estimated_usd": _quantity(estimates, uncertain=uncertain),
            "api_equivalent_usd": None,
            "subscription_invoice_usd": None,
            "basis": "Provider/transport-reported cost and historical estimates are separate; neither is a subscription invoice; no current rate card applied",
        },
        "issues": list(dict.fromkeys(issues)),
        "complete": all(value["complete"] for value in tokens.values()) and not issues,
        "token_semantics": "Inclusive input includes cache once; reasoning is a subset of inclusive output",
    }


def _read(path):
    rows = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _upsert(ledger, row):
    """Replace the run's row in place, or append it. Whole-file replace under a lock: runs share the file."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with (ledger.parent / "usage.lock").open("a+") as lock:
        util.lock_file(lock, blocking=True)
        try:
            rows = _read(ledger)
            for index, existing in enumerate(rows):
                if existing.get("run") == row["run"]:
                    rows[index] = row
                    break
            else:
                rows.append(row)
            fd, tmp = tempfile.mkstemp(prefix=".usage-", dir=ledger.parent)
            with os.fdopen(fd, "w") as stream:
                stream.write("".join(json.dumps(item, sort_keys=True) + "\n" for item in rows))
            os.replace(tmp, ledger)
        finally:
            util.unlock_file(lock)


def record(path, state: RunState):
    """Keep this run's ledger row current. Best effort: a failure never fails the checkpoint or the run."""
    try:
        run_dir = Path(path).resolve().parent
        # A parallel Builder's attempts are charged to its parent run (autocode_dispatch.account_workers copies
        # each finished stage, cost included), so the worker keeps no ledger of its own: it would count twice.
        if (
            state.get("parent_run")
            or run_dir.parent.name != "runs"
            or not (state.get("stages") or state.get("active_stage"))
        ):
            return
        totals = summary(state)
        signature = json.dumps([state.get("status"), totals], sort_keys=True)
        if _written.get(str(run_dir)) == signature:
            return
        row = {
            "run": run_dir.name,
            "status": state.get("status"),
            "workflow": (state.get("workflow") or {}).get("kind"),
            "updated_at": dt.datetime.now(dt.UTC).isoformat(),
            **totals,
        }
        _upsert(run_dir.parent.parent / LEDGER, row)
        _written[str(run_dir)] = signature
    except Exception as error:
        warnings.warn(f"Usage ledger could not be saved: {error}", RuntimeWarning, stacklevel=2)
        return


def report(project):
    """The project's ledger as text: one line per run and a total."""
    rows = _read(Path(project) / ".autocode" / LEDGER)
    if not rows:
        return f"No usage recorded in {Path(project) / '.autocode' / LEDGER}"
    lines = [f"{'run':<60} {'status':<24} {'input':>11} {'output':>9} {'reported':>10} {'estimated':>10}"]
    for row in rows:
        tokens, cost = row.get("tokens") or {}, row.get("cost_usd") or {}
        flag = "" if cost.get("complete") else " *"
        lines.append(
            f"{str(row.get('run'))[:60]:<60} {str(row.get('status'))[:24]:<24} "
            f"{tokens.get('input_tokens') or 0:>11,} {tokens.get('output_tokens') or 0:>9,} "
            f"${cost.get('reported') or 0:>9.2f} ${cost.get('estimated') or 0:>9.2f}{flag}"
        )
    lines.append(
        f"{f'total ({len(rows)} runs)':<60} {'':<24} "
        f"{_sum((row.get('tokens') or {}).get('input_tokens') for row in rows):>11,} "
        f"{_sum((row.get('tokens') or {}).get('output_tokens') for row in rows):>9,} "
        f"{'${:.2f}'.format(_sum((row.get('cost_usd') or {}).get('reported') for row in rows)):>10} "
        f"{'${:.2f}'.format(_sum((row.get('cost_usd') or {}).get('estimated') for row in rows)):>10}"
    )
    lines.append(
        "* a stage has no cost or only partial usage is known: the total is a floor. Reported is the provider's "
        "own cost; estimated is tokens at flat comparison rates, not a bill."
    )
    return "\n".join(lines)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    paths = [arg for arg in args if arg != "--json"]
    project = Path(paths[0] if paths else ".")
    print(json.dumps(_read(project / ".autocode" / LEDGER), indent=2) if as_json else report(project))
    return 0


if __name__ == "__main__":
    sys.exit(main())
