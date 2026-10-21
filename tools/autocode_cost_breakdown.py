"""Attribute the money-cap ledger without guessing whether an attempt was useful.

Only finished stage rows already charged by autocode_cost are grouped here.
Checkpoint-inherited money has no stage attribution; the cost view keeps it
separate. A repeated stage can be required work, and a successful process can
return a rejecting review. Neither implies productivity or acceptance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as roles
except ImportError:
    import autocode_roles as roles


def _group(rows, field):
    groups = {}
    for row in rows:
        key = row.get(field)
        key = key if isinstance(key, str) and key else "unknown"
        group = groups.setdefault(
            key,
            {
                field: key,
                "stages": 0,
                "reported_usd": None,
                "estimated_usd": None,
                "spent_usd": 0.0,
                "unpriced_stages": 0,
                "partial_stages": 0,
                "failed_stages": 0,
                "interrupted_stages": 0,
            },
        )
        group["stages"] += 1
        group["partial_stages"] += row["partial"]
        group["failed_stages"] += row["process_outcome"] == "failed"
        group["interrupted_stages"] += row["process_outcome"] == "interrupted"
        if row["basis"] in ("reported", "estimated"):
            basis = row["basis"] + "_usd"
            group[basis] = (group[basis] or 0.0) + row["cost_usd"]
        group["spent_usd"] += row["cost_usd"] or 0.0
        group["unpriced_stages"] += row["basis"] == "unknown"
    for group in groups.values():
        group["complete"] = not (group["unpriced_stages"] or group["partial_stages"])
        if group["unpriced_stages"] == group["stages"]:
            group["spent_usd"] = None
        for key in ("spent_usd", "reported_usd", "estimated_usd"):
            if group[key] is not None:
                group[key] = round(group[key], 6)
    return list(groups.values())


def summary(rows, state: RunState):
    """Two alternative groupings of the same ledger, never additive to each other."""
    by_stage = _group(rows, "stage")
    # The public view tolerates malformed legacy settings; names still come from
    # the shared catalogue, with routing-aware Tester/Completion Reviewer names.
    settings = state.get("settings")
    workflow = settings.get("workflow") if isinstance(settings, dict) else None
    naming = {"settings": {"workflow": workflow if isinstance(workflow, dict) else {}}}
    for group in by_stage:
        stage = group["stage"]
        group["job"] = roles.screen_name(stage, naming)
        if stage.endswith("_report_repair"):
            group["job"] += " (report repair)"
    unpriced = [row for row in rows if row["basis"] == "unknown"]
    return {
        "by_stage": by_stage,
        "by_process_outcome": _group(rows, "process_outcome"),
        # Why each unpriced stage has no cost (#1713); a count, never a guessed amount.
        "unpriced": {
            "usage_missing": sum(row["unpriced_reason"] == "usage_missing" for row in unpriced),
            "model_unknown": sum(row["unpriced_reason"] == "model_unknown" for row in unpriced),
            "rate_unavailable": sum(row["unpriced_reason"] == "rate_unavailable" for row in unpriced),
            "models_without_rate": sorted(
                {row["model"] for row in unpriced if row["unpriced_reason"] == "rate_unavailable"}
            ),
        },
        "basis": "Finished stage ledger only; excludes inherited spend and active attempts. "
        "Groupings overlap: do not add them together. Process completion is not acceptance; "
        "repetition is not evidence of waste.",
    }
