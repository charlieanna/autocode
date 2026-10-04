"""Tokens and cost of every task, measured from what each run already records.

A run saves token counts per stage (`metrics.provider_tokens`) and, when its provider reports
one, the stage's cost (`metrics.provider_cost_usd`). This module turns those into

  summary(state)   the totals for one run, by role: the `usage` field of the status view;
  record(...)      one row per run in PROJECT/.autocode/usage.jsonl, kept current at every
                   checkpoint save (autocode_status.persist), so it is right while a task runs
                   and after it pauses, completes or is killed;
  report(project)  the ledger as a table; `python3 tools/autocode_usage.py [PROJECT]`.

Cost has three bases and the output says which: "reported" (the provider's own number, as the
Claude example provider writes it), "estimated" (tokens at the flat comparison rates below, only
for models listed there) and unknown. An estimate is never added to a reported cost without being
shown separately, and unknown is never zero: `complete` is false while any stage lacks a cost.
A stage that only the runner executed costs nothing. Imports only the standard library.
"""
from __future__ import annotations

import datetime as dt
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import warnings

try:
    from . import autocode_request_usage as request_usage
except ImportError:
    import autocode_request_usage as request_usage

# Historical comparison rates, USD per 1M inclusive input/output tokens.
# These are not verified current provider prices or subscription charges.
REFERENCE_PRICES = {
    "zai-coding-plan/glm-5.3": {"input": 0.60, "output": 2.20},
    # Only for pricing saved runs from before MiMo was dropped (user 2026-09-27); new runs
    # never use it (score_autocode_run.FORBIDDEN_MODEL_MARKERS).
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


def estimate(model, tokens):
    """Tokens priced at the comparison rates; None for an unlisted model or unknown token counts."""
    prices, inp, out = REFERENCE_PRICES.get(model), _count(tokens.get("input_tokens")), _count(tokens.get("output_tokens"))
    return None if prices is None or inp is None or out is None else (inp * prices["input"] + out * prices["output"]) / 1e6


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


def stage_rows(state):
    """One row per finished stage: role, model, tokens, cost and where the cost came from."""
    rows = []
    stages = state.get("stages")
    for record in [item for item in stages if isinstance(item, dict)] if isinstance(stages, list) else []:
        metrics = _dict(record.get("metrics"))
        tokens = _dict(metrics.get("provider_tokens"))
        model = _model(record)
        if record.get("runner_owned") is True and record.get("engine") == "runner":
            cost, basis = 0.0, "runner"
        elif _money(metrics.get("provider_cost_usd")) is not None:
            cost, basis = metrics["provider_cost_usd"], "reported"
        elif (guess := estimate(model, tokens)) is not None:
            cost, basis = guess, "estimated"
        else:
            cost, basis = None, "unknown"
        rows.append({"stage": record.get("stage"), "role": str(record.get("role") or record.get("route_role") or ""),
                     "model": model, "tokens": {key: _count(tokens.get(key)) for key in TOKEN_KEYS},
                     "cost_usd": cost, "basis": basis})
    return rows


def _sum(values):
    return sum(value for value in values if value is not None)


def summary(state):
    """Totals for one run. Sums cover the stages that reported; `unknown_stages` counts those that did not."""
    rows = stage_rows(state)
    roles = {}
    for row in rows:
        entry = roles.setdefault(row["role"] or "other", {"stages": 0, "input_tokens": 0, "output_tokens": 0,
                                                          "reported_usd": 0.0, "estimated_usd": 0.0})
        entry["stages"] += 1
        entry["input_tokens"] += row["tokens"]["input_tokens"] or 0
        entry["output_tokens"] += row["tokens"]["output_tokens"] or 0
        if row["basis"] in ("reported", "estimated"):
            entry[row["basis"] + "_usd"] += row["cost_usd"]
    reported = _sum(row["cost_usd"] for row in rows if row["basis"] == "reported")
    guessed = _sum(row["cost_usd"] for row in rows if row["basis"] == "estimated")
    unknown = sum(row["basis"] == "unknown" for row in rows)
    return {
        "stages": len(rows),
        "active_stage": _dict(state.get("active_stage")).get("stage"),
        "tokens": {key: _sum(row["tokens"][key] for row in rows) for key in TOKEN_KEYS},
        "cost_usd": {"reported": round(reported, 6), "estimated": round(guessed, 6),
                     "complete": unknown == 0 and not state.get("active_stage")},
        "unknown_stages": unknown,
        "by_role": {role: {key: round(value, 6) if isinstance(value, float) else value for key, value in entry.items()}
                    for role, entry in sorted(roles.items())},
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
    return {"value": sum(known) if complete else None, "known": sum(known),
            "complete": complete, "known_items": len(known), "unknown_items": len(values) - len(known)}


def accounting(state):
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
                current = (saved.get("path") == str(path.resolve()) and saved.get("sha256")
                           and saved.get("size") == stat.st_size and saved.get("mtime_ns") == stat.st_mtime_ns)
            except (OSError, ValueError):
                current = False
            if is_active or not current:
                native = request_usage.read(record["events"])["accounting"]
        issues = [] if runner else list(native.get("issues") or [])
        requests = [] if runner else native.get("requests") or []
        if record.get("engine") == "opencode" and not requests:
            issues.append("No native request finishes; unobserved usage is unknown")
        tokens = {key: 0 if runner else _count(_dict(metrics.get("provider_tokens")).get(key))
                  for key in request_usage.TOKEN_KEYS}
        # Do not fall back to a stage sum when exact native counters are partial:
        # that would conceal conflicting/replayed/missing requests.
        if requests:
            tokens = {key: sum(row["tokens"][key] for row in requests)
                      if all(row["tokens"].get(key) is not None for row in requests) else None
                      for key in request_usage.TOKEN_KEYS}
        model = _model(record)
        reported = _money(metrics.get("provider_cost_usd"))
        reported = reported if reported else None
        if requests:
            costs = [row.get("reported_cost_usd") for row in requests]
            reported = sum(costs) if all(cost is not None for cost in costs) else None
        guessed = None if reported is not None or runner else estimate(model, tokens)
        local_id = record.get("attempt_id")
        if not local_id and type(record.get("iteration")) is int and record.get("output"):
            local_id = f"{record['iteration']:03d}/{Path(record['output']).stem}"
        attempts.append({"identity": attempt_identity(record, namespace=namespace, index=index),
            "attempt_id": local_id, "run_id": namespace or None, "worker_attempt": record.get("worker_attempt"),
            "stage": record.get("stage"), "role": record.get("role"), "model": model,
            "original_stage": record.get("original_stage"),
            "launch_route": deepcopy(_dict(record.get("launch_route"))), "engine": record.get("engine"),
            "events": record.get("events"), "output": record.get("output"),
            "recovery_novelty": deepcopy(_dict(record.get("recovery_novelty"))),
            "task_id": record.get("task_id"), "source_revision": record.get("source_revision"),
            "started_at": record.get("started_at"), "finished_at": record.get("finished_at"),
            "duration_seconds": _money(record.get("duration_seconds")), "runner_owned": runner,
            "active": is_active, "rejected": bool(record.get("rejected")),
            "interrupted": bool(record.get("interrupted") or record.get("abandoned")),
            "timed_out": bool(record.get("timed_out")), "exit_code": record.get("exit_code"),
            "report_only": bool(record.get("report_only")), "planning": bool(record.get("planning")),
            "tokens": tokens, "requests": requests, "reported_cost_usd": 0.0 if runner else reported,
            "event_file": deepcopy(native.get("event_file")),
            "historical_estimated_cost_usd": guessed, "issues": issues,
            "request_coverage_complete": runner or bool(requests) and not issues and not is_active,
            "unfinished_requests": native.get("unfinished_requests"),
            "usage_basis": "runner" if runner else "native_requests" if requests else "stage_counters",
            "unobserved_usage_possible": bool(is_active or issues),
            "identity_complete": ":unidentified:" not in attempt_identity(record, namespace=namespace, index=index)})
    result = combine_accounting(attempts)
    workers = _dict(state.get("orchestration_batch")).get("workers") or []
    result["parallel_workers_pending"] = [row.get("milestone_id") for row in workers
        if isinstance(row, dict) and row.get("status") not in ("COMPLETE", "INTEGRATED", "DONE")]
    result["parallel_worker_runs"] = [{"run_id": run_identity(row.get("run_dir")), "milestone_id": row.get("milestone_id")}
        for row in workers if isinstance(row, dict) and row.get("status") not in ("COMPLETE", "INTEGRATED", "DONE")]
    if result["parallel_workers_pending"]:
        result["issues"].append("Active parallel workers require their public TaskRun views; parent imports are not live totals")
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
            elif any(previous.get(field) != row.get(field) for field in
                     ("tokens", "requests", "model", "reported_cost_usd", "historical_estimated_cost_usd")):
                previous.update(tokens=dict.fromkeys(request_usage.TOKEN_KEYS), requests=[],
                                reported_cost_usd=None, historical_estimated_cost_usd=None,
                                request_coverage_complete=False, unobserved_usage_possible=True)
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
            if key in requests and (requests[key].get("conflict") or any(
                    requests[key].get(field) != item.get(field) for field in ("tokens", "reported_cost_usd"))):
                issues.append(f"Conflicting native request: {'/'.join(key)}")
                item = {"identity": list(key), "tokens": dict.fromkeys(request_usage.TOKEN_KEYS),
                        "reported_cost_usd": None, "model": None, "conflict": True}
            elif key in requests and requests[key].get("model") != item.get("model"):
                issues.append(f"Conflicting model attribution for native request: {'/'.join(key)}")
                requests[key].update(model=None, model_conflict=True)
            if key not in requests or item.get("conflict"):
                requests[key] = item
    sources.extend(requests.values())
    for row in rows:
        charged = [request for key, request in requests.items() if owners[key] == row["identity"]]
        portions = charged if row.get("requests") else [row]
        row["attributed_tokens"] = {key: _quantity([_dict(part.get("tokens")).get(key) for part in portions],
            uncertain=row.get("unobserved_usage_possible", False)) for key in request_usage.TOKEN_KEYS}
        row["attributed_requests"] = len(charged) if row.get("requests") or row.get("runner_owned") else None
        row["attributed_reported_usd"] = _quantity([part.get("reported_cost_usd") for part in portions],
            uncertain=row.get("unobserved_usage_possible", False))
    uncertain = any(row.get("unobserved_usage_possible") for row in rows)
    tokens = {key: _quantity([_dict(row.get("tokens")).get(key) for row in sources], uncertain=uncertain)
              for key in request_usage.TOKEN_KEYS}
    costs = [row.get("reported_cost_usd") for row in sources]
    # Historic flat estimates are stage-based comparison figures, never combined
    # with native reported cost or described as a current API rate-card estimate.
    estimates = [row.get("historical_estimated_cost_usd") for row in rows if not row.get("runner_owned")]
    return {"schema": 1, "attempts": rows, "attempt_count": len(rows),
            "active_attempts": sum(bool(row.get("active")) for row in rows),
            "tokens": tokens, "provider_requests": {"observed": len(requests),
                "unfinished_observed": sum(row.get("unfinished_requests") or 0 for row in rows),
                "value": len(requests) if all(row.get("request_coverage_complete") for row in rows) else None,
                "complete": all(row.get("request_coverage_complete") for row in rows)},
            "cost": {"reported_usd": _quantity(costs, uncertain=uncertain),
                "historical_estimated_usd": _quantity(estimates, uncertain=uncertain),
                "api_equivalent_usd": None, "subscription_invoice_usd": None,
                "basis": "Provider/transport-reported cost and historical estimates are separate; neither is a subscription invoice; no current rate card applied"},
            "issues": list(dict.fromkeys(issues)),
            "complete": all(value["complete"] for value in tokens.values()) and not issues,
            "token_semantics": "Inclusive input includes cache once; reasoning is a subset of inclusive output"}


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
        fcntl.flock(lock, fcntl.LOCK_EX)
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
            fcntl.flock(lock, fcntl.LOCK_UN)


def record(path, state):
    """Keep this run's ledger row current. Best effort: a failure never fails the checkpoint or the run."""
    try:
        run_dir = Path(path).resolve().parent
        # A parallel Builder's attempts are charged to its parent run (autocode_dispatch.account_workers copies
        # each finished stage, cost included), so the worker keeps no ledger of its own: it would count twice.
        if state.get("parent_run") or run_dir.parent.name != "runs" or not (state.get("stages") or state.get("active_stage")):
            return
        totals = summary(state)
        signature = json.dumps([state.get("status"), totals], sort_keys=True)
        if _written.get(str(run_dir)) == signature:
            return
        row = {"run": run_dir.name, "status": state.get("status"), "workflow": (state.get("workflow") or {}).get("kind"),
               "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(), **totals}
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
        lines.append(f"{str(row.get('run'))[:60]:<60} {str(row.get('status'))[:24]:<24} "
                     f"{tokens.get('input_tokens') or 0:>11,} {tokens.get('output_tokens') or 0:>9,} "
                     f"{'$%.2f' % (cost.get('reported') or 0):>10} {'$%.2f' % (cost.get('estimated') or 0):>10}{flag}")
    lines.append(f"{'total (%d runs)' % len(rows):<60} {'':<24} "
                 f"{_sum((row.get('tokens') or {}).get('input_tokens') for row in rows):>11,} "
                 f"{_sum((row.get('tokens') or {}).get('output_tokens') for row in rows):>9,} "
                 f"{'$%.2f' % _sum((row.get('cost_usd') or {}).get('reported') for row in rows):>10} "
                 f"{'$%.2f' % _sum((row.get('cost_usd') or {}).get('estimated') for row in rows):>10}")
    lines.append("* a stage has no cost yet or none was reported: the total is a floor. Reported is the provider's "
                 "own cost; estimated is tokens at flat comparison rates, not a bill.")
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
