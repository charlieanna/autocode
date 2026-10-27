"""Qualification measurement records and display; no route-selection policy.

Legacy metrics sum accepted role/model attempts. New accounting also retains
failed and report-repair work, with separate reported/estimated coverage.
Input rows are the existing public accounting projection, already deduplicated.
"""

from __future__ import annotations

import math
from statistics import median


def amount(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _quantity(portions, *, uncertain=False):
    known = sum(value for value, _ in portions if amount(value))
    observed = sum(amount(value) and not partial for value, partial in portions)
    complete = bool(portions) and observed == len(portions) and not uncertain
    return {
        "known": known,
        "value": known if complete else None,
        "complete": complete,
        "known_items": observed,
        "unknown_items": len(portions) - observed,
    }


def snapshot(rows, accepted_count, *, pending_workers=False):
    """All attempts for one launched role/model, never whole-task wall time/cost."""
    duration, reported, estimated = [], [], []
    for row in rows:
        uncertain = bool(row.get("active") or row.get("unobserved_usage_possible") or row.get("issues"))
        duration.append((row.get("duration_seconds"), bool(row.get("active"))))
        attributed = row.get("attributed_reported_usd")
        if isinstance(attributed, dict):
            # The accounting projection already deduplicates shared native requests.
            reported.append((attributed.get("known"), uncertain or attributed.get("complete") is not True))
        else:
            reported.append((row.get("reported_cost_usd"), uncertain))
        estimated.append((row.get("historical_estimated_cost_usd"), uncertain))
    return {
        "schema": 1,
        "scope": "role_model_all_attempts",
        "attempts": len(rows),
        "accepted_attempts": accepted_count,
        "failed_attempts": sum(any(row.get(key) for key in ("rejected", "timed_out", "interrupted")) for row in rows),
        "report_only_attempts": sum(bool(row.get("report_only")) for row in rows),
        "active_attempts": sum(bool(row.get("active")) for row in rows),
        "pending_workers": bool(pending_workers),
        "duration_seconds": _quantity(duration, uncertain=pending_workers),
        "reported_cost_usd": _quantity(reported, uncertain=pending_workers),
        "historical_estimated_cost_usd": _quantity(estimated, uncertain=pending_workers),
    }


def _count(value):
    return type(value) is int and value >= 0


def _valid_quantity(value, count):
    if not isinstance(value, dict) or not amount(value.get("known")) or type(value.get("complete")) is not bool:
        return False
    observed, unknown = value.get("known_items"), value.get("unknown_items")
    if not _count(observed) or not _count(unknown) or observed + unknown != count:
        return False
    if value["complete"]:
        return bool(count) and unknown == 0 and amount(value.get("value")) and value["value"] == value["known"]
    return value.get("value") is None


def _valid_accounting(value):
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        return False
    count = value.get("attempts")
    if value.get("scope") != "role_model_all_attempts" or not _count(count):
        return False
    if (
        any(
            not _count(value.get(key)) or value[key] > count
            for key in ("accepted_attempts", "failed_attempts", "report_only_attempts", "active_attempts")
        )
        or type(value.get("pending_workers")) is not bool
    ):
        return False
    if (
        value["accepted_attempts"]
        + max(value[key] for key in ("failed_attempts", "report_only_attempts", "active_attempts"))
        > count
    ):
        return False
    return all(
        _valid_quantity(value.get(key), count)
        and not ((value["pending_workers"] or value["active_attempts"]) and value[key]["complete"])
        for key in ("duration_seconds", "reported_cost_usd", "historical_estimated_cost_usd")
    )


def valid_run(run):
    """Historical records remain readable; optional all-attempt records are strict."""
    return (
        isinstance(run, dict)
        and isinstance(run.get("date"), str)
        and bool(run["date"])
        and isinstance(run.get("run"), str)
        and bool(run["run"])
        and all(run.get(key) is None or amount(run[key]) for key in ("minutes", "cost_usd"))
        and ("attempt_accounting" not in run or _valid_accounting(run["attempt_accounting"]))
        and (
            "attempt_accounting" not in run
            or (
                "attempts" not in run
                or (_count(run["attempts"]) and run["attempts"] == run["attempt_accounting"]["accepted_attempts"])
            )
        )
    )


def _legacy_summary(records):
    parts = [f"accepted-only totals ({len(records)} legacy runs; failed/repair amounts unavailable)"]
    for key, label, suffix in (("minutes", "active median", " min"), ("cost_usd", "cost median", "")):
        values = [row[key] for row in records if row.get(key) is not None]
        metric = f"{median(values):.1f}{suffix}" if values else "unknown"
        if key == "cost_usd" and values:
            metric = f"${median(values):.2f} (reported or historical estimate)"
        parts.append(f"{label} {metric} [{len(values)}/{len(records)} runs]")
    return ", ".join(parts)


def _all_attempt_summary(records):
    values = [row["attempt_accounting"] for row in records]
    attempts = sum(row["attempts"] for row in values)
    failed = sum(row["failed_attempts"] for row in values)
    repairs = sum(row["report_only_attempts"] for row in values)
    parts = [f"all-attempt route totals ({len(values)} runs): {attempts} attempts ({failed} failed, {repairs} repairs)"]
    durations = [row["duration_seconds"]["value"] / 60 for row in values if row["duration_seconds"]["complete"]]
    active = f"{median(durations):.1f} min" if durations else "unknown"
    parts.append(f"active median {active} [{len(durations)}/{len(values)} runs]")
    for key, label in (("reported_cost_usd", "reported"), ("historical_estimated_cost_usd", "historical estimate")):
        quantities = [row[key] for row in values]
        known = sum(row["known"] for row in quantities)
        observed = sum(row["known_items"] for row in quantities)
        totals = [row["value"] for row in quantities if row["complete"]]
        total = f"median ${median(totals):.2f}" if totals else "total unknown"
        parts.append(
            f"{label} known ${known:.2f} [{observed}/{attempts} attempts; {total}, {len(totals)}/{len(values)} runs]"
        )
    return ", ".join(parts)


def summary(entry):
    """Never pool accepted-only legacy data with all-attempt measurements."""
    records = entry.get("runs") or []
    if not records:
        return (
            "provisional: no AutoCode run yet"
            if entry.get("provenance") == "provisional"
            else "qualified: no recorded run"
        )
    parts = [
        f"{len(records)} AutoCode run{'s' if len(records) != 1 else ''}, latest {max(row['date'] for row in records)}"
    ]
    current = [row for row in records if "attempt_accounting" in row]
    legacy = [row for row in records if "attempt_accounting" not in row]
    if current:
        parts.append(_all_attempt_summary(current))
    if legacy:
        parts.append(_legacy_summary(legacy))
    parts.append("not whole-task totals or invoices")
    return f"{entry.get('provenance')}: " + "; ".join(parts)
