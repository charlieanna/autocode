"""Where a run's spend went, what it spent again, and what its checking produced, for the terminal chat.

Shows the status view's ``run_metrics`` as it is: the core computes every figure from its own cost,
usage and evidence projections, and this module only words them. It never adds up spend or decides
anything about the run. The side panel takes ``compact``; ``/spend`` prints ``details``.
"""

from __future__ import annotations

import math

TOP_JOBS = 3  # the jobs the side panel lists; /spend lists every one


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _money(value) -> str:
    return f"${value:.2f}" if _number(value) else "unknown"


def _percent(value) -> str:
    return f"{round(value * 100)}%" if _number(value) else "?"


def _tokens(value) -> str:
    if not _number(value):
        return "unknown"
    for size, unit in ((1_000_000, "M"), (1_000, "k")):
        if value >= size:
            return f"{value / size:.1f}{unit}"
    return str(int(value))


def _counted(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _metrics(view: dict | None) -> dict:
    metrics = _mapping(_mapping(view).get("run_metrics"))
    return metrics if metrics.get("schema") == 1 else {}


def _spent(metrics: dict) -> bool:
    return _number(metrics.get("spent_usd")) and metrics["spent_usd"] > 0


def _job_line(row: dict) -> str:
    attempts = row.get("attempts")
    again = f", {attempts} runs" if type(attempts) is int and attempts > 1 else ""
    known = "" if row.get("complete") is True else " (known part)"
    return f"{row.get('job')} {_money(row.get('spent_usd'))}{known} · {_percent(row.get('share'))}{again}"


def _cache_line(tokens: dict) -> str | None:
    if not _number(tokens.get("input")) or not tokens["input"]:
        return None
    parts = [f"cache reads {_percent(tokens.get('cache_read_share'))} of input"]
    if _number(tokens.get("cache_write_share")):
        parts.append(f"cache writes {_percent(tokens['cache_write_share'])}")
    return ", ".join(parts) + ("" if tokens.get("complete") is True else " (known part)")


def _waste_line(waste: dict) -> str:
    failed = waste.get("failed_attempts")
    if type(failed) is not int or not failed:
        line = "No failed attempts"
    else:
        known = "" if waste.get("failed_usd_complete") is True else " known"
        line = f"{failed} failed attempt{'' if failed == 1 else 's'} {_money(waste.get('failed_usd'))}{known}"
    repeated = _rows(waste.get("repeated_jobs"))
    if repeated:
        line += "; ran again: " + ", ".join(f"{row.get('job')} ×{row.get('attempts')}" for row in repeated)
    return line


def _checked(produced: dict) -> list[str]:
    """What the stages beyond the Builder produced, each only when the run has it."""
    parts = []
    requested, accepted = produced.get("criteria_requested"), produced.get("criteria_accepted")
    if type(requested) is int and requested:
        parts.append(f"{accepted if type(accepted) is int else 0}/{requested} criteria accepted")
    if produced.get("fail_to_pass_tests"):
        parts.append(_counted(produced["fail_to_pass_tests"], "test fails", "tests fail") + " before, pass after")
    if produced.get("clean_replay_checks"):
        parts.append(_counted(produced["clean_replay_checks"], "check", "checks") + " rerun clean")
    if produced.get("mutants_tried"):
        parts.append(f"{produced.get('mutants_killed', 0)}/{produced['mutants_tried']} broken lines caught")
    if produced.get("blocking_findings"):
        parts.append(_counted(produced["blocking_findings"], "blocking review finding", "blocking review findings"))
    if produced.get("problems_handled"):
        parts.append(_counted(produced["problems_handled"], "problem", "problems") + " handled")
    return parts


def compact(view: dict | None) -> tuple[str, list[str]] | None:
    """The side panel's Spend section: the Builder's share, the costliest jobs, cache use and waste."""
    metrics = _metrics(view)
    if not _spent(metrics):
        return None
    single = _mapping(metrics.get("single_model"))
    if _number(single.get("builder_share")):
        summary = f"Builder {_percent(single['builder_share'])} · checking {_percent(1 - single['builder_share'])}"
    else:
        summary = f"{_money(metrics.get('spent_usd'))} across {len(_rows(metrics.get('by_job')))} jobs"
    entries = [_job_line(row) for row in _rows(metrics.get("by_job"))[:TOP_JOBS] if _number(row.get("spent_usd"))]
    if cache := _cache_line(_mapping(metrics.get("tokens"))):
        entries.append(cache.capitalize())
    entries.append(_waste_line(_mapping(metrics.get("waste"))))
    return summary, entries


def details(view: dict | None) -> str:
    """``/spend``: every job's share, the token split, what was spent again, and the single-model stand-in."""
    metrics = _metrics(view)
    if not _spent(metrics):
        return "Nothing has been spent on models yet."
    whole = "" if metrics.get("complete") is True else " (known part; some usage is not recorded)"
    lines = [f"Where the money went: {_money(metrics.get('spent_usd'))}{whole}"]
    lines += [f"  {_job_line(row)}" for row in _rows(metrics.get("by_job")) if row.get("spent_usd")]
    tokens = _mapping(metrics.get("tokens"))
    split = ", ".join(
        f"{label} {_tokens(tokens.get(key))}"
        for key, label in (
            ("input", "input"),
            ("new", "new"),
            ("cache_write", "cache write"),
            ("cache_read", "cache read"),
            ("output", "output"),
        )
    )
    lines.append(f"  Tokens: {split}" + ("" if tokens.get("complete") is True else " (known part)"))
    if cache := _cache_line(tokens):
        lines.append(f"  {cache.capitalize()}")
    waste = _mapping(metrics.get("waste"))
    lines.append("Spent again")
    lines.append(f"  {_waste_line(waste)}")
    if _number(waste.get("report_repair_usd")) and waste["report_repair_usd"]:
        lines.append(f"  Report repairs {_money(waste['report_repair_usd'])}")
    single = _mapping(metrics.get("single_model"))
    lines.append("Compared with one model building it alone")
    lines.append(
        f"  Builder {_money(single.get('builder_usd'))} ({_percent(single.get('builder_share'))}); "
        f"planning and checking {_money(single.get('checking_usd'))}"
    )
    checked = _checked(_mapping(single.get("produced")))
    lines.append("  What that bought: " + ("; ".join(checked) if checked else "nothing recorded yet"))
    if isinstance(single.get("basis"), str):
        lines.append(f"  {single['basis']}.")
    return "\n".join(lines)
