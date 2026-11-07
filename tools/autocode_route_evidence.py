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
        and ("execution_evidence" not in run or _valid_execution_evidence(run["execution_evidence"]))
        and (
            "execution_evidence" not in run
            or "attempt_accounting" not in run
            or run["execution_evidence"]["attempts"] == run["attempt_accounting"]["attempts"]
        )
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
    observed = [row["execution_evidence"] for row in records if "execution_evidence" in row]
    if observed:
        known = sum(row["known_attempts"] for row in observed)
        total = sum(row["attempts"] for row in observed)
        parts.append(f"execution identity {known}/{total} attempts [{len(observed)}/{len(records)} runs recorded]")
    else:
        parts.append(f"execution identities unrecorded [0/{len(records)} runs]")
    parts.append("not whole-task totals or invoices")
    return f"{entry.get('provenance')}: " + "; ".join(parts)


def _text(value):
    return isinstance(value, str) and bool(value) and len(value) <= 1024 and all(c.isprintable() for c in value)


def endpoint_identity(endpoint, protocol, api_provider):
    """Configured target identity without credential/query/tenant-path disclosure."""
    from hashlib import sha256
    from urllib.parse import urlsplit

    if not _text(endpoint) or any(c.isspace() for c in endpoint):
        return None
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
            or protocol not in ("compatible", "anthropic")
            or not _text(api_provider)
        ):
            return None
        return {
            "origin": parsed.scheme + "://" + parsed.netloc,
            "sha256": sha256(endpoint.encode()).hexdigest(),
            "protocol": protocol,
            "api_provider": api_provider,
        }
    except ValueError:
        return None


def _valid_endpoint(value):
    if not isinstance(value, dict) or set(value) != {"origin", "sha256", "protocol", "api_provider"}:
        return False
    probe = endpoint_identity(value.get("origin"), value.get("protocol"), value.get("api_provider"))
    return (
        probe is not None
        and probe["origin"] == value["origin"]
        and value["origin"] == value["origin"].rstrip("/")
        and isinstance(value.get("sha256"), str)
        and len(value["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value["sha256"])
    )


def launch_identity(*, role, engine, provider, model, effort, runtime_commit, settings, endpoint=None):
    """Freeze launch facts; permitted-but-unselected accounts grant no billing fact."""
    import re
    from copy import deepcopy

    if runtime_commit is not None and (
        not isinstance(runtime_commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}(?:\+modified)?", runtime_commit)
    ):
        runtime_commit = None
    settings = settings if isinstance(settings, dict) else {}
    selection = settings.get("route_selection")
    selected_routes = selection.get("routes") if isinstance(selection, dict) else None
    selected = selected_routes.get(role) if isinstance(selected_routes, dict) else None
    selected = selected if isinstance(selected, dict) else {}
    billing = selected.get("billing") if selected.get("model") == model and selected.get("tool") == provider else None
    if billing not in ("subscription", "pay per token"):
        billing = None
    return {
        "schema": 1,
        "role": role,
        "engine": engine,
        "provider": provider,
        "model": model,
        "reasoning_effort": effort,
        "runtime_commit": runtime_commit,
        "billing_kind": billing,
        "billing_basis": "saved_selected_route" if billing else "unrecorded",
        "endpoint": deepcopy(endpoint) if _valid_endpoint(endpoint) else None,
    }


def valid_execution_identity(value):
    import re

    return (
        isinstance(value, dict)
        and set(value)
        == {
            "schema",
            "role",
            "engine",
            "provider",
            "model",
            "reasoning_effort",
            "runtime_commit",
            "billing_kind",
            "billing_basis",
            "endpoint",
        }
        and type(value.get("schema")) is int
        and value["schema"] == 1
        and all(_text(value.get(key)) for key in ("role", "engine", "provider", "model"))
        and (value.get("reasoning_effort") in (None, "") or _text(value["reasoning_effort"]))
        and (value.get("runtime_commit") is None or _text(value["runtime_commit"]))
        and (
            value["runtime_commit"] is None
            or re.fullmatch(r"[0-9a-f]{7,64}(?:\+modified)?", value["runtime_commit"]) is not None
        )
        and value.get("billing_kind") in (None, "subscription", "pay per token")
        and value.get("billing_basis") == ("unrecorded" if value["billing_kind"] is None else "saved_selected_route")
        and (value.get("endpoint") is None or _valid_endpoint(value["endpoint"]))
    )


def execution_for_attempt(row):
    facts = row.get("execution_identity")
    if not valid_execution_identity(facts) or (
        facts["role"] != (row.get("route_role") or row.get("role"))
        or facts["model"] != row.get("model")
        or facts["engine"] != row.get("engine")
    ):
        return None
    if "reasoning_effort" in row and facts["reasoning_effort"] != row["reasoning_effort"]:
        return None
    return facts


def execution_snapshot(rows, *, accepted):
    """Group exact launch facts; never backfill an old attempt from current settings."""
    import copy
    import json

    groups = {}
    for row in rows:
        facts = execution_for_attempt(row)
        if facts is None:
            continue
        key = json.dumps(facts, sort_keys=True)
        group = groups.setdefault(key, {"identity": copy.deepcopy(facts), "attempts": 0, "accepted_attempts": 0})
        group["attempts"] += 1
        group["accepted_attempts"] += bool(accepted(row))
    known = sum(group["attempts"] for group in groups.values())
    return {
        "schema": 1,
        "attempts": len(rows),
        "known_attempts": known,
        "unknown_attempts": len(rows) - known,
        "observations": [groups[key] for key in sorted(groups)],
    }


def _valid_execution_evidence(value):
    import json

    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        return False
    if any(not _count(value.get(key)) for key in ("attempts", "known_attempts", "unknown_attempts")):
        return False
    observations = value.get("observations")
    if not isinstance(observations, list) or value["known_attempts"] + value["unknown_attempts"] != value["attempts"]:
        return False
    keys = set()
    for row in observations:
        if (
            not isinstance(row, dict)
            or not valid_execution_identity(row.get("identity"))
            or not _count(row.get("attempts"))
            or row["attempts"] == 0
            or not _count(row.get("accepted_attempts"))
            or row["accepted_attempts"] > row["attempts"]
        ):
            return False
        key = json.dumps(row["identity"], sort_keys=True)
        if key in keys:
            return False
        keys.add(key)
    return sum(row["attempts"] for row in observations) == value["known_attempts"]
