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
`metrics.provider_tokens_partial` marks known consumption from an interrupted stream as a lower bound.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

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
    return value if type(value) in (int, float) and value >= 0 else None


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
                     "partial": metrics.get("provider_tokens_partial") is True,
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
    partial = sum(row["partial"] for row in rows)
    return {
        "stages": len(rows),
        "active_stage": _dict(state.get("active_stage")).get("stage"),
        "tokens": {key: _sum(row["tokens"][key] for row in rows) for key in TOKEN_KEYS},
        "cost_usd": {"reported": round(reported, 6), "estimated": round(guessed, 6),
                     "complete": unknown == 0 and partial == 0 and not state.get("active_stage")},
        "unknown_stages": unknown,
        "partial_stages": partial,
        "by_role": {role: {key: round(value, 6) if isinstance(value, float) else value for key, value in entry.items()}
                    for role, entry in sorted(roles.items())},
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
    except Exception:
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
    lines.append("* a stage has no cost or only partial usage is known: the total is a floor. Reported is the provider's "
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
