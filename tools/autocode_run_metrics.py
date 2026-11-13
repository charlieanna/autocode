"""Where a run's model spend went, what was spent again, and what its checking stages produced.

The status view's ``run_metrics``: a client shows it as it is (the terminal chat's Spend panel)
so a person can see where a run could cost less and what AutoCode did beyond one model building
the change. Every figure is read from parts of the view that are already public:

- ``cost.breakdown.by_stage`` for spend by job and for report repairs, and ``usage.accounting`` for
  tokens, attempts and each job's time;
- ``recoveries`` and ``evidence`` (findings, regression proof, clean replay, mutation check) for
  what the checking stages produced.

``single_model`` compares with the Builder's own spend, a stand-in for one model doing the task
alone: the Builder is the stage that writes the change. It is not a measured single-model run,
and ``basis`` says so. An amount a source does not know stays
``None``; a known part is never shown as a whole (``complete``).

A report repair (a stage that only rewrites a rejected report) is another attempt at the stage it
repairs, never another run of that job: ``repeated_jobs`` counts the two apart.

Pure function over the view being built: no file access, no state, imports nothing from AutoCode.
"""

from __future__ import annotations

import math

SCHEMA = 1
BUILDER_STAGE = "builder"
# The jobs that decide what to build, named apart from the checking after it (#3268): "checking 82%"
# once counted Requirements and the Planner too.
PLANNING_JOBS = frozenset({"Requirements", "Planner", "Plan Reviewer"})
BASIS = (
    "The Builder's spend stands in for one model doing the task alone; "
    "it is not a measured single-model run of this task"
)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _money(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _quantity(value) -> tuple[int | float | None, bool]:
    """A ``{value, known, complete}`` quantity as (amount, complete); a partial one keeps its known part."""
    value = _mapping(value)
    if value.get("complete") is True and _money(value.get("value")) is not None:
        return value["value"], True
    return _money(value.get("known")), False


def _count_of_every_attempt(value) -> tuple[int | float | None, bool]:
    """A token count, unknown when some attempt did not report it at all: a known part from two of eight
    stages would read as the run's (an older Claude run recorded no cache writes for most stages)."""
    if type(_mapping(value).get("unknown_items")) is int and value["unknown_items"] > 0:
        return None, False
    return _quantity(value)


def _share(part, whole):
    return round(part / whole, 4) if part is not None and whole else None


def _failed(row: dict) -> bool:
    exit_code = row.get("exit_code")
    return bool(
        row.get("rejected")
        or row.get("interrupted")
        or row.get("timed_out")
        or (type(exit_code) is int and exit_code != 0)
    )


def _repair(row: dict) -> bool:
    """A report repair: an attempt the core records against the stage it repairs (``original_stage``)."""
    original = row.get("original_stage")
    return bool(original) and original != row.get("stage")


def _attempts(accounting: dict) -> list[dict]:
    return _rows(accounting.get("attempts"))


def _seconds(attempts: list[dict], stages: list) -> tuple[float | None, bool]:
    """The summed run time of a job's own stages' attempts, and whether every one reported it."""
    total, complete, seen = 0.0, True, False
    for row in attempts:
        if row.get("stage") not in stages:
            continue
        seen = True
        seconds = _money(row.get("duration_seconds"))
        if seconds is None or row.get("active"):
            complete = False
        else:
            total += seconds
    return (round(total, 3), complete) if seen else (None, False)


def _by_job(cost: dict, total, attempts: list[dict]) -> list[dict]:
    jobs: dict[str, dict] = {}
    for row in _rows(_mapping(cost.get("breakdown")).get("by_stage")):
        job = str(row.get("job") or row.get("stage") or "Other")
        entry = jobs.setdefault(job, {"job": job, "stages": [], "attempts": 0, "spent_usd": 0.0, "complete": True})
        if row.get("stage") not in entry["stages"]:
            entry["stages"].append(row.get("stage"))
        entry["attempts"] += _count(row.get("stages")) or 0
        spent = _money(row.get("spent_usd"))
        if spent is None:
            entry["complete"] = False
        else:
            entry["spent_usd"] += spent
        if row.get("complete") is not True:
            entry["complete"] = False
    result = []
    for entry in jobs.values():
        if not (entry["attempts"] or entry["spent_usd"]):
            continue
        seconds, seconds_complete = _seconds(attempts, entry["stages"])
        result.append(
            {
                **entry,
                "spent_usd": round(entry["spent_usd"], 6),
                "share": _share(entry["spent_usd"], total),
                "seconds": seconds,
                "seconds_complete": seconds_complete,
            }
        )
    return sorted(result, key=lambda row: -row["spent_usd"])


def _tokens(accounting: dict) -> dict:
    tokens = _mapping(accounting.get("tokens"))
    parts = {
        "input": "input_tokens",
        "new": "fresh_input_tokens",
        "cache_write": "cache_write_tokens",
        "cache_read": "cached_input_tokens",
        "output": "output_tokens",
    }
    result: dict = {}
    complete = True
    for name, key in parts.items():
        amount, whole = _count_of_every_attempt(tokens.get(key))
        result[name] = amount
        complete = complete and whole
    result["complete"] = complete
    known_input = result["input"]
    result["cache_read_share"] = _share(result["cache_read"], known_input)
    result["cache_write_share"] = _share(result["cache_write"], known_input)
    return result


def _report_repair_spend(cost: dict) -> tuple[float | None, bool]:
    """What report repairs cost, from the same stage costs as every other figure here (``spent_usd``)."""
    total, complete = 0.0, True
    for row in _rows(_mapping(cost.get("breakdown")).get("by_stage")):
        if not str(row.get("stage") or "").endswith("_report_repair"):
            continue
        spent = _money(row.get("spent_usd"))
        if spent is None:
            complete = False
        else:
            total += spent
        if row.get("complete") is not True:
            complete = False
    return round(total, 6), complete


def _waste(accounting: dict, cost: dict) -> dict:
    attempts = [row for row in _attempts(accounting) if not row.get("runner_owned")]
    failed = [row for row in attempts if _failed(row) and not row.get("active")]
    amounts = [_quantity(row.get("attributed_reported_usd")) for row in failed]
    counts: dict[str, dict] = {}
    for row in attempts:
        job = str(row.get("job") or row.get("stage") or "Other")
        entry = counts.setdefault(job, {"job": job, "attempts": 0, "runs": 0, "report_repairs": 0})
        entry["attempts"] += 1
        entry["report_repairs" if _repair(row) else "runs"] += 1
    repair, repair_complete = _report_repair_spend(cost)
    return {
        "failed_attempts": len(failed),
        "failed_usd": round(sum(amount or 0 for amount, _ in amounts), 6),
        "failed_usd_complete": all(whole for _, whole in amounts),
        "repeated_jobs": [row for row in counts.values() if row["attempts"] > 1],
        "report_repair_usd": repair,
        "report_repair_complete": repair_complete,
    }


def _produced(view: dict) -> dict:
    evidence = _mapping(view.get("evidence"))
    proof = _mapping(evidence.get("regression_proof"))
    replay = _mapping(evidence.get("check_replay"))
    mutants = [
        mutant
        for row in _rows(_mapping(evidence.get("mutation_check")).get("files"))
        for mutant in _rows(row.get("mutants"))
    ]
    criteria = _mapping(_mapping(view.get("efficiency")).get("criteria"))
    findings = _rows(evidence.get("findings"))
    return {
        "criteria_requested": _count(criteria.get("requested")),
        "criteria_accepted": _count(criteria.get("accepted")),
        "regression_verdict": proof.get("verdict") if isinstance(proof.get("verdict"), str) else None,
        "fail_to_pass_tests": len(proof.get("fail_to_pass") or [])
        if isinstance(proof.get("fail_to_pass"), list)
        else 0,
        "clean_replay_checks": len(_rows(replay.get("checks"))),
        "clean_replay_verdict": replay.get("verdict") if isinstance(replay.get("verdict"), str) else None,
        "mutants_killed": sum(1 for mutant in mutants if mutant.get("outcome") == "killed"),
        "mutants_tried": sum(1 for mutant in mutants if mutant.get("outcome") in ("killed", "survived", "timeout")),
        "findings": len(findings),
        "blocking_findings": sum(1 for row in findings if row.get("blocking") is True),
        "problems_handled": sum(row.get("handled") is True for row in _rows(view.get("recoveries"))),
    }


def project(view: dict) -> dict:
    """``run_metrics`` from the cost, usage, recoveries and evidence already in ``view``."""
    cost = _mapping(view.get("cost"))
    accounting = _mapping(_mapping(view.get("usage")).get("accounting"))
    total = _money(cost.get("spent_usd"))
    by_job = _by_job(cost, total, _attempts(accounting))
    builder = [
        _money(row.get("spent_usd"))
        for row in _rows(_mapping(cost.get("breakdown")).get("by_stage"))
        if row.get("stage") == BUILDER_STAGE
    ]
    builder_usd = round(sum(amount for amount in builder if amount is not None), 6) if builder else None
    checking_usd = round(total - builder_usd, 6) if total is not None and builder_usd is not None else None
    planning_usd = (
        round(sum(row["spent_usd"] for row in by_job if row["job"] in PLANNING_JOBS), 6)
        if checking_usd is not None
        else None
    )
    return {
        "schema": SCHEMA,
        "spent_usd": total,
        "complete": cost.get("complete") is True,
        "by_job": by_job,
        "tokens": _tokens(accounting),
        "waste": _waste(accounting, cost),
        "single_model": {
            "builder_usd": builder_usd,
            "builder_share": _share(builder_usd, total),
            "checking_usd": checking_usd,
            # checking_usd split (#3268): Requirements, Planner and Plan Reviewer, then everything else.
            "planning_usd": planning_usd,
            "review_usd": round(max(checking_usd - planning_usd, 0), 6) if planning_usd is not None else None,
            "planning_share": _share(planning_usd, total),
            "review_share": _share(checking_usd - planning_usd, total) if planning_usd is not None else None,
            "basis": BASIS,
            "produced": _produced(view),
        },
    }
